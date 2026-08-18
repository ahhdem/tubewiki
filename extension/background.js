// Background service worker: owns the backend POST (cross-origin fetch here bypasses
// CORS via host_permissions) and keeps a small per-session list of captures for the popup.

const DEFAULT_BACKEND = "http://localhost:8000";

async function backendUrl() {
  const { backendUrl } = await chrome.storage.sync.get("backendUrl");
  return (backendUrl || DEFAULT_BACKEND).replace(/\/$/, "");
}

async function recordSession(entry) {
  const { session = [] } = await chrome.storage.session.get("session");
  session.unshift(entry);
  await chrome.storage.session.set({ session: session.slice(0, 50) });
}

async function ingest(payload) {
  const base = await backendUrl();
  const res = await fetch(base + "/ingest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await res.json();
  await recordSession({
    video_id: payload.video_id,
    title: payload.title,
    concept: result.concept_title || null,
    status: result.status,
    at: Date.now(),
  });
  return result;
}

// Read one tab's transcript through the logged-in session, in the PAGE (MAIN) world so
// we can reach ytInitialPlayerResponse. Works on any open watch tab, even ones opened
// before the extension loaded — no reliance on the content script.
async function captureTab(tabId) {
  const [inj] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: async () => {
      const pr = window.ytInitialPlayerResponse;
      if (!pr) return null;
      const vd = pr.videoDetails || {};
      const tracks =
        (((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || [];
      const pick =
        tracks.find((t) => t.kind !== "asr" && (t.languageCode || "").startsWith("en")) ||
        tracks.find((t) => t.kind !== "asr") ||
        tracks.find((t) => (t.languageCode || "").startsWith("en")) ||
        tracks[0];
      let transcript = null;
      if (pick) {
        try {
          const r = await fetch(pick.baseUrl + "&fmt=json3", { credentials: "include" });
          const d = await r.json();
          transcript = (d.events || [])
            .flatMap((e) => e.segs || [])
            .map((s) => s.utf8 || "")
            .join("")
            .replace(/\s+/g, " ")
            .trim();
        } catch (e) {
          /* leave transcript null → backend fallback */
        }
      }
      return { video_id: vd.videoId, title: vd.title, channel: vd.author,
               url: location.href.split("&")[0], transcript };
    },
  });
  return inj && inj.result;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// MV3 kills the service worker after ~30s idle, which stops a long batch the moment the
// popup closes. Pinging an extension API on an interval resets that timer for the batch's
// duration. Ref: Chrome "keep a service worker alive" pattern.
let _keepAlive = null;
function startKeepAlive() {
  if (_keepAlive) return;
  _keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
}
function stopKeepAlive() {
  if (_keepAlive) { clearInterval(_keepAlive); _keepAlive = null; }
}

// Toolbar badge = progress you can watch without the popup open.
function setBadge(text) {
  chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
  chrome.action.setBadgeText({ text });
}

function videoIdFromUrl(url) {
  const m = (url || "").match(/(?:[?&]v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

function cleanTabTitle(title) {
  return (title || "")
    .replace(/^\(\d+\)\s*/, "")   // strip "(3) " unread-count prefix
    .replace(/\s*-\s*YouTube$/, "")
    .trim();
}

// Wait for a tab to finish (re)loading, with a timeout.
function waitForComplete(tabId, timeout) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(); } };
    const listener = (id, info) => { if (id === tabId && info.status === "complete") finish(); };
    const timer = setTimeout(finish, timeout);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// Reload a discarded/unloaded tab, then capture once the player data is present.
async function loadAndCapture(tabId) {
  try {
    const t = await chrome.tabs.get(tabId);
    if (t.discarded || t.status !== "complete") {
      await chrome.tabs.reload(tabId);
      await waitForComplete(tabId, 20000);
      await sleep(1500); // let ytInitialPlayerResponse populate
    }
  } catch (e) {
    return null; // tab was closed
  }
  for (let i = 0; i < 6; i++) {
    let info = null;
    try { info = await captureTab(tabId); } catch (e) { /* retry */ }
    if (info && info.video_id && info.transcript) return info;
    await sleep(1200);
  }
  try { return await captureTab(tabId); } catch (e) { return null; }
}

// Phase 1: enumerate ALL open watch tabs (including discarded ones). Capture the
// transcript now for loaded tabs; for discarded tabs, record metadata from the tab
// object and defer the transcript to ingest time. Nothing is ingested here.
async function scanTabs() {
  startKeepAlive();
  try {
  const tabs = await chrome.tabs.query({ url: ["*://www.youtube.com/watch*", "*://youtube.com/watch*"] });
  const seen = new Set(), pending = [];
  for (const tab of tabs) {
    const vid = videoIdFromUrl(tab.url);
    if (!vid || seen.has(vid)) continue;
    seen.add(vid);

    let info = null;
    if (!tab.discarded) {
      try { info = await captureTab(tab.id); } catch (e) { /* not ready */ }
    }
    pending.push({
      tabId: tab.id,
      video_id: vid,
      title: (info && info.title) || cleanTabTitle(tab.title) || vid,
      channel: (info && info.channel) || null,
      url: (info && info.url) || (tab.url || "").split("&")[0],
      transcript: (info && info.transcript) || null,
      discarded: !!tab.discarded,
    });
    if (info) await sleep(120);
  }
  await chrome.storage.session.set({ pending });
  return pending.map((p) => ({
    video_id: p.video_id,
    title: p.title,
    channel: p.channel,
    hasTranscript: !!p.transcript,
    needsLoad: !p.transcript, // discarded or not-yet-loaded → will reload on ingest
    thumb: `https://i.ytimg.com/vi/${p.video_id}/mqdefault.jpg`,
  }));
  } finally {
    stopKeepAlive();
  }
}

// Phase 2: ingest only the checked ids. Discarded/uncaptured tabs are reloaded and
// captured on demand here (so we only ever force-load the ones you actually picked).
async function ingestSelected(ids) {
  const set = new Set(ids);
  const { pending = [] } = await chrome.storage.session.get("pending");
  const targets = pending.filter((p) => set.has(p.video_id));
  let ingested = 0, skipped = 0, failed = 0, done = 0;

  startKeepAlive(); // survive the popup closing / SW idle timeout
  try {
    for (const p of targets) {
      const payload = {
        video_id: p.video_id, title: p.title || "", channel: p.channel || null,
        url: p.url || null, transcript: p.transcript || null,
      };
      if (!payload.transcript && p.tabId != null) {
        const fresh = await loadAndCapture(p.tabId);
        if (fresh) {
          payload.transcript = fresh.transcript || null;
          if (fresh.title) payload.title = fresh.title;
          if (fresh.channel) payload.channel = fresh.channel;
          if (fresh.url) payload.url = fresh.url;
        }
      }
      try {
        const res = await ingest(payload);
        if (res.status === "ingested") ingested++; else skipped++;
      } catch (e) {
        failed++;
      }
      done++;
      setBadge(String(targets.length - done)); // remaining count on the toolbar icon
      await chrome.storage.session.set({
        batch: { done, total: targets.length, ingested, skipped, failed, running: true },
      });
      await sleep(200); // hits the backend, not YouTube
    }
  } finally {
    stopKeepAlive();
    setBadge("");
    await chrome.storage.session.set({
      batch: { done, total: targets.length, ingested, skipped, failed, running: false },
    });
  }
  return { selected: ids.length, ingested, skipped, failed };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "ingest") {
    ingest(msg.payload)
      .then(sendResponse)
      .catch((e) => sendResponse({ status: "error", detail: String(e) }));
    return true;
  }
  if (msg.type === "scanTabs") {
    scanTabs()
      .then((candidates) => sendResponse({ candidates }))
      .catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
  if (msg.type === "ingestSelected") {
    ingestSelected(msg.ids || [])
      .then(sendResponse)
      .catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
});

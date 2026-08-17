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

// Phase 1: scan every open watch tab and capture its transcript, but DO NOT ingest.
// Full payloads (incl. transcript) are stashed in storage.session; the popup gets back
// light metadata + a thumbnail to render a review list.
async function scanTabs() {
  const tabs = await chrome.tabs.query({ url: ["*://www.youtube.com/watch*", "*://youtube.com/watch*"] });
  const seen = new Set(), pending = [];
  for (const tab of tabs) {
    try {
      const info = await captureTab(tab.id);
      if (info && info.video_id && !seen.has(info.video_id)) {
        seen.add(info.video_id);
        pending.push(info);
      }
    } catch (e) {
      /* tab not injectable (not a real watch page yet) — skip */
    }
    await sleep(150);
  }
  await chrome.storage.session.set({ pending });
  return pending.map((p) => ({
    video_id: p.video_id,
    title: p.title || p.video_id,
    channel: p.channel || null,
    url: p.url || null,
    hasTranscript: !!(p.transcript && p.transcript.length),
    thumb: `https://i.ytimg.com/vi/${p.video_id}/mqdefault.jpg`,
  }));
}

// Phase 2: ingest only the video ids the user kept checked.
async function ingestSelected(ids) {
  const set = new Set(ids);
  const { pending = [] } = await chrome.storage.session.get("pending");
  let ingested = 0, skipped = 0, failed = 0;
  for (const p of pending) {
    if (!set.has(p.video_id)) continue;
    try {
      const res = await ingest({
        video_id: p.video_id, title: p.title || "", channel: p.channel || null,
        url: p.url || null, transcript: p.transcript || null,
      });
      if (res.status === "ingested") ingested++; else skipped++;
    } catch (e) {
      failed++;
    }
    await sleep(200); // hits the backend, not YouTube
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

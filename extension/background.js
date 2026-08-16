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

async function captureAll() {
  const tabs = await chrome.tabs.query({ url: ["*://www.youtube.com/watch*", "*://youtube.com/watch*"] });
  let ingested = 0, skipped = 0, failed = 0;
  for (const tab of tabs) {
    try {
      const info = await captureTab(tab.id);
      if (!info || !info.video_id) { failed++; continue; }
      const res = await ingest({
        video_id: info.video_id, title: info.title || "", channel: info.channel || null,
        url: info.url || null, transcript: info.transcript || null,
      });
      if (res.status === "ingested") ingested++; else skipped++;
    } catch (e) {
      failed++;
    }
    await sleep(250); // gentle pacing; these hit the backend, not YouTube
  }
  return { total: tabs.length, ingested, skipped, failed };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "ingest") {
    ingest(msg.payload)
      .then(sendResponse)
      .catch((e) => sendResponse({ status: "error", detail: String(e) }));
    return true;
  }
  if (msg.type === "captureAll") {
    captureAll()
      .then(sendResponse)
      .catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
});

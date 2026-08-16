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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "ingest") {
    ingest(msg.payload)
      .then(sendResponse)
      .catch((e) => sendResponse({ status: "error", detail: String(e) }));
    return true; // async response
  }
});

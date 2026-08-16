// Content script: on a YouTube watch page, capture the transcript using the LOGGED-IN
// SESSION (spec §4 — the extension reaches transcripts a backend cannot, and it runs on
// a residential IP, which matters because YouTube blocks datacenter IPs from the caption
// endpoint). Metadata + transcript are handed to the background worker, which POSTs to the
// backend (background fetch bypasses CORS via host_permissions).

const captured = new Set(); // per-page-session dedup

function injectPageScript() {
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = chrome.runtime.getURL("inject.js");
    s.onload = () => {
      s.remove();
      resolve();
    };
    (document.head || document.documentElement).appendChild(s);
  });
}

function requestPlayerData() {
  return new Promise((resolve) => {
    const handler = (e) => {
      if (e.source === window && e.data && e.data.__tubewiki === "response") {
        window.removeEventListener("message", handler);
        resolve(e.data.data);
      }
    };
    window.addEventListener("message", handler);
    window.postMessage({ __tubewiki: "request" }, "*");
    setTimeout(() => resolve(null), 4000);
  });
}

function pickTrack(tracks) {
  if (!tracks || !tracks.length) return null;
  // Prefer a manual English track; fall back to any manual; then auto-generated.
  return (
    tracks.find((t) => t.kind !== "asr" && (t.lang || "").startsWith("en")) ||
    tracks.find((t) => t.kind !== "asr") ||
    tracks.find((t) => (t.lang || "").startsWith("en")) ||
    tracks[0]
  );
}

async function fetchTranscript(track) {
  // json3 is the easiest caption format to parse. Same-origin fetch → carries cookies.
  const url = track.url + (track.url.includes("fmt=") ? "" : "&fmt=json3");
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("caption fetch " + res.status);
  const data = await res.json();
  const parts = [];
  for (const ev of data.events || []) {
    for (const seg of ev.segs || []) if (seg.utf8) parts.push(seg.utf8);
  }
  return parts.join("").replace(/\s+/g, " ").trim();
}

async function capture() {
  await injectPageScript();
  const info = await requestPlayerData();
  if (!info || !info.videoId || captured.has(info.videoId)) return;

  const track = pickTrack(info.tracks);
  let transcript = null;
  if (track) {
    try {
      transcript = await fetchTranscript(track);
    } catch (e) {
      console.warn("[TubeWiki] transcript fetch failed:", e);
    }
  }

  captured.add(info.videoId);
  const payload = {
    video_id: info.videoId,
    title: info.title || document.title.replace(/ - YouTube$/, ""),
    channel: info.channel || null,
    url: location.href.split("&")[0],
    transcript, // null → backend fallback chain (youtube-transcript-api / Whisper)
  };
  chrome.runtime.sendMessage({ type: "ingest", payload }, (resp) => {
    console.log("[TubeWiki] ingest:", resp);
  });
}

// Initial load + YouTube SPA navigations.
capture();
window.addEventListener("yt-navigate-finish", () => setTimeout(capture, 800));

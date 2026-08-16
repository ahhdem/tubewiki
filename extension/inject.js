// Runs in the PAGE context (not the isolated content-script world) so it can read
// YouTube's `ytInitialPlayerResponse`, which carries the caption track URLs. It replies
// to the content script over window.postMessage.
(function () {
  function grab() {
    try {
      const pr = window.ytInitialPlayerResponse;
      if (!pr) return null;
      const vd = pr.videoDetails || {};
      const tracks =
        (((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || [];
      return {
        videoId: vd.videoId,
        title: vd.title,
        channel: vd.author,
        tracks: tracks.map((t) => ({
          url: t.baseUrl,
          lang: t.languageCode,
          kind: t.kind, // "asr" for auto-generated
          name: (t.name || {}).simpleText,
        })),
      };
    } catch (e) {
      return null;
    }
  }

  window.addEventListener("message", (e) => {
    if (e.source === window && e.data && e.data.__tubewiki === "request") {
      window.postMessage({ __tubewiki: "response", data: grab() }, "*");
    }
  });
})();

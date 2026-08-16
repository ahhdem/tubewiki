// Session-scoped capture list — a minimal precursor to the Phase-5 sidebar (spec §8).
(async function () {
  const { backendUrl } = await chrome.storage.sync.get("backendUrl");
  const base = (backendUrl || "http://localhost:8000").replace(/\/$/, "");
  document.getElementById("open").href = base + "/";

  const btn = document.getElementById("captureAll");
  const status = document.getElementById("status");
  btn.addEventListener("click", () => {
    btn.disabled = true;
    status.textContent = "Capturing open tabs…";
    chrome.runtime.sendMessage({ type: "captureAll" }, (r) => {
      btn.disabled = false;
      if (!r || r.error) {
        status.textContent = "Error: " + (r ? r.error : "no response");
        return;
      }
      status.textContent =
        `${r.ingested} ingested · ${r.skipped} skipped · ${r.failed} failed (of ${r.total} tabs)`;
      setTimeout(() => location.reload(), 900); // refresh the session list
    });
  });

  const { session = [] } = await chrome.storage.session.get("session");
  const list = document.getElementById("list");
  if (!session.length) return;

  list.innerHTML = "";
  for (const e of session) {
    const div = document.createElement("div");
    div.className = "row";
    const concept = e.concept ? `→ ${e.concept}` : e.status;
    div.innerHTML =
      `<div class="t">${escapeHtml(e.title || e.video_id)}</div>` +
      `<div class="m"><span class="badge">${escapeHtml(e.status)}</span> ${escapeHtml(concept)}</div>`;
    list.appendChild(div);
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"]/g, (m) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m])
    );
  }
})();

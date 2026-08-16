// Session-scoped capture list — a minimal precursor to the Phase-5 sidebar (spec §8).
(async function () {
  const { backendUrl } = await chrome.storage.sync.get("backendUrl");
  const base = (backendUrl || "http://localhost:8000").replace(/\/$/, "");
  document.getElementById("open").href = base + "/";

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

// Two-step bulk seed: Scan (preview, no ingest) → review/uncheck → Ingest selected.
const $ = (id) => document.getElementById(id);
const listEl = $("list"), statusEl = $("status"), footEl = $("foot"), selEl = $("sel");
let candidates = [];

(async function init() {
  const { backendUrl } = await chrome.storage.sync.get("backendUrl");
  $("open").href = ((backendUrl || "http://localhost:8000").replace(/\/$/, "")) + "/";
})();

$("scan").addEventListener("click", async () => {
  $("scan").disabled = true;
  statusEl.textContent = "Scanning open tabs…";
  listEl.innerHTML = '<div class="empty">Reading transcripts from your open tabs…</div>';
  chrome.runtime.sendMessage({ type: "scanTabs" }, (r) => {
    $("scan").disabled = false;
    if (!r || r.error) {
      statusEl.textContent = "Error: " + (r ? r.error : "no response");
      return;
    }
    candidates = r.candidates || [];
    render();
  });
});

$("all").addEventListener("click", () => setAll(true));
$("none").addEventListener("click", () => setAll(false));

$("ingest").addEventListener("click", () => {
  const ids = [...listEl.querySelectorAll("input:checked")].map((c) => c.dataset.id);
  if (!ids.length) return;
  $("ingest").disabled = true;
  statusEl.textContent = `Ingesting ${ids.length}…`;
  chrome.runtime.sendMessage({ type: "ingestSelected", ids }, (r) => {
    $("ingest").disabled = false;
    if (!r || r.error) {
      statusEl.textContent = "Error: " + (r ? r.error : "no response");
      return;
    }
    statusEl.textContent =
      `${r.ingested} ingested · ${r.skipped} skipped · ${r.failed} failed`;
    // Drop the ones we just ingested from the list.
    const done = new Set(ids);
    candidates = candidates.filter((c) => !done.has(c.video_id));
    render();
  });
});

function setAll(checked) {
  listEl.querySelectorAll("input").forEach((c) => (c.checked = checked));
  updateCount();
}

function updateCount() {
  const n = listEl.querySelectorAll("input:checked").length;
  selEl.textContent = `${n} selected`;
  $("ingest").textContent = n ? `Ingest ${n} selected` : "Ingest selected";
  $("ingest").disabled = n === 0;
}

function render() {
  if (!candidates.length) {
    listEl.innerHTML = '<div class="empty">No open YouTube watch tabs found.</div>';
    footEl.classList.add("hidden");
    $("all").classList.add("hidden");
    $("none").classList.add("hidden");
    statusEl.textContent = statusEl.textContent || "";
    return;
  }
  const withT = candidates.filter((c) => c.hasTranscript).length;
  statusEl.textContent = `${candidates.length} videos · ${withT} with transcripts`;
  $("all").classList.remove("hidden");
  $("none").classList.remove("hidden");
  footEl.classList.remove("hidden");

  listEl.innerHTML = "";
  for (const c of candidates) {
    const card = document.createElement("label");
    card.className = "card";
    const noT = c.hasTranscript
      ? ""
      : '<span class="badge">no transcript</span>';
    card.innerHTML =
      `<input type="checkbox" data-id="${esc(c.video_id)}" ${c.hasTranscript ? "checked" : ""}>` +
      `<img src="${esc(c.thumb)}" loading="lazy">` +
      `<div class="meta"><div class="t">${esc(c.title)}</div>` +
      `<div class="m">${esc(c.channel || "")} ${noT}</div></div>`;
    // No inline handlers (MV3 CSP): wire events in JS.
    card.querySelector("img").addEventListener("error", (e) => (e.target.style.visibility = "hidden"));
    card.querySelector("input").addEventListener("change", updateCount);
    listEl.appendChild(card);
  }
  updateCount();
}

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m])
  );
}

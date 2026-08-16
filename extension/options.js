const input = document.getElementById("backend");
const saved = document.getElementById("saved");

chrome.storage.sync.get("backendUrl").then(({ backendUrl }) => {
  input.value = backendUrl || "http://localhost:8000";
});

document.getElementById("save").addEventListener("click", async () => {
  const url = input.value.trim().replace(/\/$/, "");
  await chrome.storage.sync.set({ backendUrl: url });
  saved.textContent = "Saved ✓";
  setTimeout(() => (saved.textContent = ""), 1500);
});

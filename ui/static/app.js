// A click on a task row opens its modal (`/task/ID/modal`); a link or the
// tree toggle inside the row keeps its own click. A page that reloads
// (`data-reload` on <body>, in seconds) waits while the modal is open.

const modal = document.querySelector("dialog");
const body = modal.querySelector("div");

document.addEventListener("click", async (event) => {
  const row = event.target.closest("tr[data-task]");
  if (!row || event.target.closest("a, label")) return;
  body.innerHTML = "<p class=muted style='padding:24px'>Loading…</p>";
  if (!modal.open) modal.showModal();
  const response = await fetch(`/task/${encodeURIComponent(row.dataset.task)}/modal`);
  body.innerHTML = await response.text();
});

modal.addEventListener("click", (event) => {
  if (event.target === modal) modal.close();
});
modal.querySelector("button").addEventListener("click", () => modal.close());

const reload = Number(document.body.dataset.reload);
if (reload) setInterval(() => modal.open || location.reload(), reload * 1000);

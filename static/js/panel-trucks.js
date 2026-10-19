// Truck card and the import grid: row click → cargo card, bulk selection bar,
// «Добавить из принятых» selection, toasts that arrive via HTMX, live «Сохранить N грузов».

const CONTROLS = "a, button, input, select, textarea, label, summary, .cell-check, .cell-action";

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (b === 1 && a !== 11) return one;
  if (b >= 2 && b <= 4 && (a < 12 || a > 14)) return few;
  return many;
}

// ---------- cargo table ----------

// A click on a cargo row (not on a control inside it) opens the cargo card.
document.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-href]");
  if (!row || event.target.closest(CONTROLS)) return;
  if (window.getSelection && String(window.getSelection()).length) return;
  window.location.href = row.dataset.href;
});

function updateBulk() {
  const bar = document.querySelector("[data-bulk-bar]");
  if (!bar) return;
  const items = [...document.querySelectorAll("[data-bulk-item]")];
  const checked = items.filter((box) => box.checked);
  bar.querySelectorAll("[data-bulk-count]").forEach((el) => {
    el.textContent = String(checked.length);
  });
  bar.hidden = checked.length === 0;
  items.forEach((box) => box.closest("tr")?.classList.toggle("is-selected", box.checked));
  const all = document.querySelector("[data-bulk-all]");
  if (all) {
    all.checked = items.length > 0 && checked.length === items.length;
    all.indeterminate = checked.length > 0 && checked.length < items.length;
  }
}

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-bulk-all]")) {
    document.querySelectorAll("[data-bulk-item]").forEach((box) => {
      box.checked = event.target.checked;
    });
  }
  if (event.target.matches("[data-bulk-all], [data-bulk-item]")) updateBulk();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-bulk-clear]")) return;
  document.querySelectorAll("[data-bulk-item]").forEach((box) => {
    box.checked = false;
  });
  updateBulk();
});

// ---------- «Добавить из принятых» ----------

function updateReceived() {
  const form = document.querySelector("[data-received-form]");
  if (!form) return;
  let count = 0;
  let places = 0;
  form.querySelectorAll("[data-received-item]").forEach((box) => {
    box.closest("tr")?.classList.toggle("is-selected", box.checked);
    if (!box.checked) return;
    count += 1;
    places += Number(box.dataset.places || 0);
  });
  const countEl = form.querySelector("[data-received-count]");
  const placesEl = form.querySelector("[data-received-places]");
  const submit = form.querySelector("[data-received-submit]");
  if (countEl) countEl.textContent = String(count);
  if (placesEl) placesEl.textContent = String(places);
  if (submit) submit.disabled = count === 0;
}

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-received-item]")) updateReceived();
});

// A click anywhere on a received row toggles its checkbox.
document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-check-row]");
  if (!row || event.target.closest(CONTROLS)) return;
  const box = row.querySelector("input[type=checkbox]");
  if (!box) return;
  box.checked = !box.checked;
  updateReceived();
});

// Enter in the search box searches; it must not load the selected cargo.
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.id === "received-q") event.preventDefault();
});

// ---------- after HTMX swaps ----------

document.addEventListener("htmx:afterSettle", () => {
  updateBulk();
  updateReceived();
  updateGrid();
});

// Toasts swapped in out of band: hide on click; success toasts also hide after a while.
document.addEventListener("htmx:load", (event) => {
  const root = event.detail.elt;
  const toasts = root.matches?.("[data-toast]")
    ? [root]
    : root.querySelectorAll?.("[data-toast]") || [];
  toasts.forEach((toast) => {
    toast.addEventListener("click", () => toast.remove());
    if (!toast.classList.contains("st-error")) setTimeout(() => toast.remove(), 5000);
  });
});

// ---------- import / manual entry grid ----------

function rowFilled(row) {
  return [...row.querySelectorAll("input[type=text], input:not([type]), textarea")].some(
    (input) => input.value.trim() !== "",
  );
}

function updateGrid() {
  const grid = document.querySelector("[data-grid]");
  if (!grid) return;
  const filled = [...grid.querySelectorAll("[data-grid-row]")].filter(rowFilled).length;
  grid.querySelectorAll("[data-grid-save]").forEach((button) => {
    button.textContent = `Сохранить ${filled} ${plural(filled, "груз", "груза", "грузов")}`;
  });
}

document.addEventListener("input", (event) => {
  if (event.target.closest("[data-grid]")) updateGrid();
});

document.addEventListener("DOMContentLoaded", () => {
  updateBulk();
  updateReceived();
  updateGrid();
});

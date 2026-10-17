// Ордер на день and Выдача: date picker auto-submit, toasts that arrive via HTMX,
// pickup totals that follow the checkboxes.

// Date picker opens the chosen day right away.
document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-autosubmit]");
  if (input && input.value && input.form) input.form.submit();
});

// Toasts swapped in out of band: hide on click; success toasts also hide after a while.
document.addEventListener("htmx:load", (event) => {
  const root = event.detail.elt;
  const toasts = root.matches?.("[data-toast]") ? [root] : root.querySelectorAll?.("[data-toast]") || [];
  toasts.forEach((toast) => {
    toast.addEventListener("click", () => toast.remove());
    if (!toast.classList.contains("st-error")) setTimeout(() => toast.remove(), 5000);
  });
  // The inline «Позвонили» form: focus the date field when it opens.
  const reach = root.querySelector?.(".order-reach input[type=date]");
  if (reach) reach.focus();
});

// Pickup: totals and the «Выдать (N)» label follow the ticked cargo.
const rub = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function updatePickup(form) {
  let count = 0;
  let places = 0;
  let amount = 0;
  form.querySelectorAll("input[name=cargo]").forEach((box) => {
    box.closest(".pickup-card")?.classList.toggle("is-off", !box.checked);
    if (!box.checked) return;
    count += 1;
    places += Number(box.dataset.places || 0);
    amount += Number.parseFloat(box.dataset.amount || "0");
  });
  const placesEl = form.querySelector("[data-total-places]");
  const amountEl = form.querySelector("[data-total-amount]");
  const submit = form.querySelector("[data-issue]");
  if (placesEl) placesEl.textContent = String(places);
  if (amountEl) amountEl.textContent = rub.format(amount) + " ₽";
  if (submit) {
    submit.textContent = `Выдать (${count})`;
    submit.disabled = count === 0;
  }
}

document.addEventListener("change", (event) => {
  const form = event.target.closest("[data-pickup]");
  if (form && event.target.name === "cargo") updatePickup(form);
});

// Double submit guard for the pickup form.
document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-pickup]");
  if (!form) return;
  const submit = form.querySelector("[data-issue]");
  if (submit?.dataset.sent) {
    event.preventDefault();
    return;
  }
  if (submit) submit.dataset.sent = "1";
});

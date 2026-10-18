// Staff login: the «Панель управления / Склад» segment sets the hidden `next` and the caption.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-login-form]");
  if (!form) return;
  const nextInput = form.querySelector("[data-next-input]");
  const caption = form.querySelector("[data-segment-caption]");
  form.querySelectorAll('input[name="segment"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      if (nextInput) nextInput.value = radio.dataset.next;
      if (caption) caption.textContent = radio.dataset.caption;
    });
  });
});

// Mobile burger toggle for the public header.
document.addEventListener("DOMContentLoaded", () => {
  const header = document.querySelector("[data-site-header]");
  const burger = document.querySelector("[data-burger]");
  if (!header || !burger) return;
  burger.addEventListener("click", () => {
    const open = header.classList.toggle("is-open");
    burger.setAttribute("aria-expanded", String(open));
  });
});

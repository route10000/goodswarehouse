// Home chapters: reveal on scroll. Content stays visible without JS or under reduced motion.
const setupReveal = () => {
  const sections = document.querySelectorAll("[data-reveal]");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!sections.length || reduced || !("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -10% 0px", threshold: 0.15 },
  );
  document.documentElement.classList.add("js-reveal");
  sections.forEach((section) => observer.observe(section));
};

// Public header: mobile burger toggle and the «Войти ▾» dropdown.
document.addEventListener("DOMContentLoaded", () => {
  setupReveal();

  const header = document.querySelector("[data-site-header]");
  const burger = document.querySelector("[data-burger]");
  if (header && burger) {
    burger.addEventListener("click", () => {
      const open = header.classList.toggle("is-open");
      burger.setAttribute("aria-expanded", String(open));
    });
  }

  const menu = document.querySelector("[data-login-menu]");
  const toggle = menu && menu.querySelector("[data-login-toggle]");
  if (!menu || !toggle) return;

  const setOpen = (open, { focusToggle = false } = {}) => {
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (!open && focusToggle) toggle.focus();
  };

  toggle.addEventListener("click", () => {
    const open = !menu.classList.contains("is-open");
    setOpen(open);
    if (open) {
      const first = menu.querySelector(".login-menu__item");
      if (first) first.focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (menu.classList.contains("is-open") && !menu.contains(event.target)) setOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.classList.contains("is-open")) {
      setOpen(false, { focusToggle: true });
    }
  });

  menu.addEventListener("focusout", (event) => {
    if (menu.classList.contains("is-open") && !menu.contains(event.relatedTarget)) {
      setOpen(false);
    }
  });
});

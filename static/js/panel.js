// Panel shell: live clock (Vladivostok), "/" search hotkey, dialogs, copy buttons, toasts, «Ещё» menu.
document.addEventListener("DOMContentLoaded", () => {
  const clock = document.querySelector("[data-live-clock]");
  if (clock) {
    const fmt = new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Vladivostok",
    });
    const tick = () => {
      clock.textContent = "LIVE " + fmt.format(new Date());
    };
    tick();
    setInterval(tick, 15000);
  }

  const search = document.querySelector("[data-cmd-search]");
  if (search) {
    document.addEventListener("keydown", (event) => {
      const tag = (event.target.tagName || "").toLowerCase();
      if (event.key !== "/" || ["input", "textarea", "select"].includes(tag)) return;
      if (event.target.isContentEditable) return;
      event.preventDefault();
      search.focus();
    });
  }
});

// Confirm dialogs: [data-dialog-open="<id>"] opens a native <dialog>, [data-dialog-close] closes it.
document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-dialog-open]");
  if (opener) {
    const dialog = document.getElementById(opener.dataset.dialogOpen);
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    return;
  }
  const closer = event.target.closest("[data-dialog-close]");
  if (closer) {
    const dialog = closer.closest("dialog");
    if (dialog) dialog.close();
    return;
  }
  // Click on the backdrop (outside the dialog box) closes it.
  if (event.target instanceof HTMLDialogElement && event.target.open) {
    const box = event.target.getBoundingClientRect();
    const inside =
      event.clientX >= box.left && event.clientX <= box.right &&
      event.clientY >= box.top && event.clientY <= box.bottom;
    if (!inside) event.target.close();
  }
});

// Copy buttons: [data-copy="<selector of an input>"]; the label changes for a moment.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.querySelector(button.dataset.copy);
  if (!source) return;
  const text = source.value || source.textContent || "";
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    source.select?.();
    document.execCommand("copy");
  }
  const label = button.textContent;
  button.textContent = button.dataset.copiedText || "Скопировано";
  setTimeout(() => {
    button.textContent = label;
  }, 1600);
});

// Toasts (Django messages): hide on click; success toasts also hide after a few seconds.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-toast]").forEach((toast, index) => {
    const hide = () => toast.remove();
    toast.addEventListener("click", hide);
    if (!toast.classList.contains("st-error")) setTimeout(hide, 6000 + index * 800);
  });
});

// Mobile «Ещё» menu (<details data-sidebar-more>): closes on an outside click and on Escape.
document.addEventListener("click", (event) => {
  const more = document.querySelector("[data-sidebar-more][open]");
  if (more && !more.contains(event.target)) more.open = false;
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const more = document.querySelector("[data-sidebar-more][open]");
  if (!more) return;
  more.open = false;
  more.querySelector("summary")?.focus();
});

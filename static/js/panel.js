// Panel shell: live clock (Vladivostok time) and "/" hotkey for the command search.
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

// Ops-центр page behaviour: truck selection (drawer + tracker via HTMX), tabs,
// the mobile bottom sheet and a guard for HTMX refreshes after the session expires.
//
// Event API (on `document`), see docs/SCENE_API.md:
//   in:  `ops:select-truck` {detail: {id}}  — select a truck, load its drawer and tracker
//   out: `ops:selected`     {detail: {id}}  — the selection changed (id may be null)
const root = document.querySelector("[data-ops]");

if (root) {
  const selectedInput = document.getElementById("ops-selected");
  const slot = document.getElementById("ops-drawer-slot");
  const backdrop = document.querySelector(".ops-backdrop");
  const mobile = window.matchMedia("(max-width: 899px)");
  let opener = null;

  const selectedId = () => {
    const value = parseInt(selectedInput.value, 10);
    return Number.isFinite(value) ? value : null;
  };

  const truckUrl = (id) => root.dataset.truckUrl.replace(/0\/$/, `${id}/`);

  const markFleet = () => {
    const id = String(selectedId() ?? "");
    document.querySelectorAll("[data-ops-truck]").forEach((row) => {
      if (row.dataset.opsTruck === id) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
  };

  const openSheet = () => {
    if (!mobile.matches) return;
    slot.classList.add("is-open");
    backdrop.hidden = false;
    document.documentElement.classList.add("ops-sheet-open");
    slot.querySelector("[data-ops-close]")?.focus({ preventScroll: true });
  };

  const closeSheet = () => {
    if (!slot.classList.contains("is-open")) return;
    slot.classList.remove("is-open");
    backdrop.hidden = true;
    document.documentElement.classList.remove("ops-sheet-open");
    opener?.focus?.({ preventScroll: true });
    opener = null;
  };

  const select = async (id, { open = true } = {}) => {
    if (!Number.isFinite(id)) return;
    selectedInput.value = String(id);
    markFleet();
    document.dispatchEvent(new CustomEvent("ops:selected", { detail: { id } }));
    if (window.htmx) {
      await window.htmx.ajax("GET", truckUrl(id), { target: "#ops-drawer", swap: "outerHTML" });
    }
    if (open) openSheet();
  };

  document.addEventListener("ops:select-truck", (event) => {
    const id = Number(event.detail?.id);
    select(id, { open: event.detail?.open !== false });
  });

  // Fleet rows are links to the truck card; a plain click selects the truck instead.
  document.addEventListener("click", (event) => {
    const row = event.target.closest("[data-ops-truck]");
    if (row) {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      event.preventDefault();
      opener = row;
      document.dispatchEvent(
        new CustomEvent("ops:select-truck", { detail: { id: Number(row.dataset.opsTruck) } }),
      );
      return;
    }
    if (event.target.closest("[data-ops-close]")) closeSheet();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSheet();
  });

  mobile.addEventListener("change", () => {
    if (!mobile.matches) closeSheet();
  });

  // Tabs: «Отслеживание машины» / «Таймлайн дня».
  const tabs = Array.from(root.querySelectorAll("[data-ops-tab]"));
  const activate = (tab, focus = false) => {
    tabs.forEach((other) => {
      const active = other === tab;
      other.setAttribute("aria-selected", String(active));
      other.tabIndex = active ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !active;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      activate(tabs[(index + step + tabs.length) % tabs.length], true);
    });
  });

  // A refresh after the session expired is redirected to the login page: reload instead
  // of swapping the login form into a tile.
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    const responseUrl = event.detail.xhr?.responseURL || "";
    if (responseUrl.includes("/panel/login/")) {
      event.detail.shouldSwap = false;
      window.location.reload();
    }
  });

  document.body.addEventListener("htmx:afterSettle", markFleet);
}

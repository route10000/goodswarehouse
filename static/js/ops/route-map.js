// Flat route map (`.route-map`): the fallback for the 3D scene in `#ops-scene`.
// Draws the road Уссурийск → Владивосток and a capsule per truck at its progress from
// every `ops:data` payload. Clicking a capsule dispatches `ops:select-truck`.
// It stays idle while `#ops-scene[data-mode="3d"]` or once its container is removed.
const SVG_NS = "http://www.w3.org/2000/svg";
const PAD_X = 56;
const LANE_HEIGHT = 34;
const CAPSULE_HEIGHT = 26;
const GAP = 8;

const scene = document.getElementById("ops-scene");
const host = scene?.querySelector("[data-route-map]");

const timeFormat = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Vladivostok",
});
const dayFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  timeZone: "Asia/Vladivostok",
});

let data = null;
let selectedId = null;

function shortNumber(number) {
  const parts = String(number).split("-");
  return parts.length === 3 ? `${parts[0]}-${parts[2]}` : String(number);
}

function when(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  const today = dayFormat.format(new Date());
  const day = dayFormat.format(date);
  return day === today ? timeFormat.format(date) : `${day} ${timeFormat.format(date)}`;
}

export function truckLabel(truck) {
  const head = shortNumber(truck.number);
  switch (truck.status) {
    case "loading":
      return `${head} · Погрузка ${truck.places_loaded}/${truck.places}`;
    case "in_transit":
      return `${head} · В пути · ETA ${when(truck.eta)}`;
    case "arrived":
      return `${head} · Прибыла ${when(truck.arrived_at)}`;
    default:
      return `${head} · Закрыта`;
  }
}

function position(truck) {
  if (truck.status === "loading") return 0;
  if (truck.status === "in_transit") return Math.min(0.96, Math.max(0.04, truck.progress || 0));
  return 1;
}

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function select(id) {
  document.dispatchEvent(new CustomEvent("ops:select-truck", { detail: { id } }));
}

function drawBlocks(blocks) {
  const strip = document.createElement("div");
  strip.className = "route-map__blocks";
  strip.setAttribute("aria-label", "Склад по сроку хранения");
  for (const block of blocks || []) {
    const item = document.createElement("span");
    item.className = `route-map__block route-map__block--${block.key.toLowerCase()}`;
    item.title = `${block.label} · ${block.caption} · ${block.places} мест`;
    const pct = Math.round((block.fill || 0) * 100);
    item.innerHTML =
      `<span class="route-map__block-key">${block.key}</span>` +
      `<span class="route-map__meter"><span style="width:${pct}%"></span></span>` +
      `<span class="route-map__block-pct">${pct}%</span>`;
    strip.appendChild(item);
  }
  return strip;
}

function render() {
  if (!host || !host.isConnected || !data) return;
  if (scene.dataset.mode === "3d") return;
  const width = host.clientWidth;
  const height = host.clientHeight;
  if (width < 120 || height < 120) return;

  host.replaceChildren();
  const svg = el("svg", {
    class: "route-map__svg",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    "aria-hidden": "true",
  });
  host.appendChild(svg);

  const x0 = PAD_X;
  const x1 = width - PAD_X;
  const roadY = Math.round(Math.max(height * 0.6, height - 96));

  // Road and its ends.
  el("line", { class: "route-map__road", x1: x0, y1: roadY, x2: x1, y2: roadY }, svg);
  for (const [x, city, note, anchor] of [
    [x0, data.route.from, "погрузка", "start"],
    [x1, data.route.to, "склад", "end"],
  ]) {
    el("circle", { class: "route-map__city", cx: x, cy: roadY, r: 7 }, svg);
    const dx = anchor === "start" ? -PAD_X + 12 : PAD_X - 12;
    const name = el("text", { class: "route-map__city-name", x: x + dx, y: roadY + 30, "text-anchor": anchor }, svg);
    name.textContent = city;
    const sub = el("text", { class: "route-map__city-note", x: x + dx, y: roadY + 46, "text-anchor": anchor }, svg);
    sub.textContent = note;
  }
  const km = el("text", { class: "route-map__km", x: (x0 + x1) / 2, y: roadY + 30, "text-anchor": "middle" }, svg);
  km.textContent = `≈ ${data.route.km} км`;

  const trucks = (data.trucks || []).map((truck) => ({
    truck,
    x: x0 + position(truck) * (x1 - x0),
  }));
  if (!trucks.length) {
    const empty = el("text", { class: "route-map__empty", x: width / 2, y: roadY - 40, "text-anchor": "middle" }, svg);
    empty.textContent = "Машин в работе нет";
  }

  // Capsules stack into lanes above the road so they never overlap. When the lanes do
  // not fit (small map, many trucks at one end) labels shrink to the trip number, and
  // what still does not fit collapses into a «+N» capsule per spot.
  const maxLanes = Math.max(1, Math.floor((roadY - 40 - CAPSULE_HEIGHT / 2 - 8) / LANE_HEIGHT) + 1);
  const rank = (t) => (t.id === selectedId ? 0 : { in_transit: 1, loading: 2, arrived: 3 }[t.status] ?? 4);
  trucks.sort((a, b) => a.x - b.x || rank(a.truck) - rank(b.truck) || a.truck.id - b.truck.id);

  const probe = el("text", { class: "route-map__label", x: -999, y: -999 }, svg);
  const measure = (label) => {
    probe.textContent = label;
    return Math.ceil(probe.getComputedTextLength()) + 34;
  };
  const place = (entries) => {
    const lanes = [];
    for (const entry of entries) {
      entry.left = Math.min(Math.max(4, entry.x - entry.width / 2), width - entry.width - 4);
      let lane = lanes.findIndex((right) => right + GAP <= entry.left);
      if (lane === -1) {
        lane = lanes.length;
        lanes.push(0);
      }
      lanes[lane] = entry.left + entry.width;
      entry.lane = lane;
    }
    return lanes.length;
  };

  let placed = trucks;
  for (const short of [false, true]) {
    for (const entry of trucks) {
      entry.label = short ? shortNumber(entry.truck.number) : truckLabel(entry.truck);
      entry.width = measure(entry.label);
    }
    if (place(trucks) <= maxLanes) break;
  }
  if (trucks.some((entry) => entry.lane >= maxLanes)) {
    const keep = trucks.filter((entry) => entry.lane < maxLanes - 1);
    const overflow = trucks.filter((entry) => entry.lane >= maxLanes - 1);
    const clusters = [];
    for (const entry of overflow) {
      const last = clusters[clusters.length - 1];
      if (last && entry.x - last[0].x < 48) last.push(entry);
      else clusters.push([entry]);
    }
    placed = keep.concat(
      clusters.map((group) => {
        const x = group.reduce((sum, e) => sum + e.x, 0) / group.length;
        const label = `+${group.length}`;
        const w = measure(label);
        const left = Math.min(Math.max(4, x - w / 2), width - w - 4);
        return { more: group, x, label, width: w, lane: maxLanes - 1, left };
      }),
    );
  }
  probe.remove();

  // Stems and road points go underneath every capsule so no line crosses a label.
  const under = el("g", { class: "route-map__stems" }, svg);
  for (const entry of placed) {
    const truck = entry.more ? null : entry.truck;
    const selected = entry.more
      ? entry.more.some((e) => e.truck.id === selectedId)
      : truck.id === selectedId;
    const status = truck ? truck.status : "more";
    const label = entry.more
      ? entry.more.map((e) => truckLabel(e.truck)).join("; ")
      : truckLabel(truck);
    const group = el("g", {
      class: `route-map__truck route-map__truck--${status}${selected ? " is-selected" : ""}`,
      tabindex: "0",
      role: "button",
      "aria-label": label,
    }, svg);
    if (entry.more) el("title", {}, group).textContent = label;
    const top = roadY - 40 - entry.lane * LANE_HEIGHT - CAPSULE_HEIGHT / 2;
    const left = Math.round(entry.left);
    const stemClass = `route-map__truck route-map__truck--${status}${selected ? " is-selected" : ""}`;
    const stem = el("g", { class: stemClass }, under);
    el("line", { class: "route-map__stem", x1: entry.x, x2: entry.x, y1: top + CAPSULE_HEIGHT, y2: roadY }, stem);
    el("circle", { class: "route-map__point", cx: entry.x, cy: roadY, r: selected ? 6 : 5 }, stem);
    el("rect", {
      class: "route-map__capsule",
      rx: CAPSULE_HEIGHT / 2,
      x: left,
      y: top,
      width: entry.width,
      height: CAPSULE_HEIGHT,
    }, group);
    el("circle", { class: "route-map__dot", r: 4, cx: left + 14, cy: top + CAPSULE_HEIGHT / 2 }, group);
    const text = el("text", { class: "route-map__label", x: left + 24, y: top + CAPSULE_HEIGHT / 2 + 4 }, group);
    text.textContent = entry.label;

    // «+N» selects the first hidden truck; the fleet list below shows all of them.
    const target = truck ? truck.id : entry.more[0].truck.id;
    group.addEventListener("click", () => select(target));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(target);
      }
    });
  }

  host.appendChild(drawBlocks(data.blocks));
}

if (host) {
  const input = document.getElementById("ops-selected");
  const initial = parseInt(input?.value ?? "", 10);
  selectedId = Number.isFinite(initial) ? initial : null;

  document.addEventListener("ops:data", (event) => {
    data = event.detail;
    render();
  });
  document.addEventListener("ops:selected", (event) => {
    selectedId = event.detail?.id ?? null;
    render();
  });
  new ResizeObserver(() => render()).observe(host);
  document.fonts?.ready.then(render);
}

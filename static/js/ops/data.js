// Ops-центр data feed. Publishes the scene JSON (docs/SCENE_API.md) as a `document`
// CustomEvent `ops:data` right away (from the payload embedded in the page) and then
// after every poll of GET /panel/api/scene/ (every 15 s while the tab is visible).
// Late subscribers dispatch `ops:request-data` to get the latest payload again.
const POLL_MS = 15000;

const scene = document.getElementById("ops-scene");
const url = scene ? scene.dataset.sceneUrl : null;
let latest = null;
let timer = null;

function publish(data) {
  latest = data;
  document.dispatchEvent(new CustomEvent("ops:data", { detail: data }));
}

function embedded() {
  const node = document.getElementById("ops-scene-data");
  if (!node) return null;
  try {
    return JSON.parse(node.textContent);
  } catch {
    return null;
  }
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

async function poll() {
  if (!url || document.hidden) return;
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
    });
    const type = response.headers.get("content-type") || "";
    if (!response.ok || !type.includes("application/json")) {
      // Session expired: the login redirect returns HTML. Stop until the page reloads.
      if (response.redirected || response.status === 403) stop();
      return;
    }
    publish(await response.json());
  } catch {
    // Network hiccup: keep the last data, try again on the next tick.
  }
}

document.addEventListener("ops:request-data", () => {
  if (latest) publish(latest);
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && timer) poll();
});

const first = embedded();
if (first) publish(first);
if (url) timer = setInterval(poll, POLL_MS);

# Ops-центр: scene data API and page events

Contract between the Ops-центр page (T8) and the 3D scene (T9). The scene only displays
data and never changes anything.

## `GET /panel/api/scene/`

- URL name `panel:ops_scene`. Login required, both staff groups («Менеджер», «Склад»).
  Anonymous requests get `302` to `/panel/login/`.
- Response `200`, `Content-Type: application/json`, UTF-8 (Cyrillic is not escaped).
- Built by `panel.ops_data.scene()`. No client personal data: no names, phones, client
  codes, articles or cargo descriptions.
- Types are plain JSON: integers, floats, booleans, strings, `null`. Datetimes are ISO 8601
  strings in local time with offset, e.g. `"2026-10-18T15:40:00+10:00"`.

```json
{
  "generated_at": "2026-10-18T12:00:00+10:00",
  "route": {"from": "Уссурийск", "to": "Владивосток", "km": 110},
  "trucks": [
    {
      "id": 41,
      "number": "R-2026-041",
      "status": "in_transit",
      "status_label": "В пути",
      "progress": 0.5,
      "places": 52,
      "places_loaded": 52,
      "capacity_places": 60,
      "clients": 23,
      "weight_kg": 812.4,
      "departed_at": "2026-10-18T11:00:00+10:00",
      "eta": "2026-10-18T13:00:00+10:00",
      "arrived_at": null,
      "url": "/panel/trucks/41/"
    }
  ],
  "blocks": [
    {"key": "A", "label": "Блок A", "caption": "≤ 2 дн", "places": 120, "cargo": 31, "fill": 0.3},
    {"key": "B", "label": "Блок B", "caption": "3–4 дн", "places": 64, "cargo": 12, "fill": 0.16},
    {"key": "C", "label": "Блок C", "caption": "5–6 дн", "places": 0, "cargo": 0, "fill": 0.0},
    {"key": "D", "label": "Блок D", "caption": "≥ 7 дн", "places": 9, "cargo": 2, "fill": 0.0225}
  ],
  "summary": {
    "awaiting_places": 193,
    "awaiting_cargo": 45,
    "awaiting_delta": 52,
    "trucks_in_transit": 1,
    "trucks_active": 3,
    "order_built": true,
    "order_pending": 18,
    "order_total": 42,
    "picked_up_today": 37,
    "picked_up_places": 61
  }
}
```

### `trucks[]`

Same set and order as the fleet list on the page: every truck that is not `closed`, plus
trucks closed within the last 24 h by `Truck.closed_at` (legacy rows closed before that
field existed use `arrived_at`). Order: `arrived` (latest arrival first), `in_transit`
(earliest `eta` first, no ETA last), `loading` (earliest `departure_date` first, undated
last), then `closed` (most recently closed first).

| field | type | meaning |
|---|---|---|
| `id` | int | Truck primary key; use it in `ops:select-truck` |
| `number` | string | Trip number `R-YYYY-NNN` |
| `status` | string | `loading` · `in_transit` · `arrived` · `closed` |
| `status_label` | string | Russian label: Грузится · В пути · Прибыла · Закрыта |
| `progress` | float 0..1 | see below, 4 decimals |
| `places` | int | places of all cargo in the truck |
| `places_loaded` | int | places of cargo already loaded (any status after `received`) |
| `capacity_places` | int ≥ 1 | truck capacity in places (`Truck.capacity_places`, default 60) |
| `clients` | int | number of distinct clients with cargo in the truck |
| `weight_kg` | float | total weight; cargo without weight counts as 0 |
| `departed_at` | string \| null | actual departure |
| `eta` | string \| null | expected arrival |
| `arrived_at` | string \| null | actual arrival |
| `url` | string | truck card in the panel |

`progress`:
- `loading`: `min(1, places_loaded / capacity_places)`; `0.0` when the truck is empty,
  `1.0` when it is loaded over capacity. Labels show `places_loaded/capacity_places`
  («Погрузка 38/60»).
- `in_transit`: share of `departed_at → eta` elapsed at `generated_at`, clamped to
  `0.0..1.0`; `0.0` when `eta` or `departed_at` is missing; `1.0` when `eta` is not after
  `departed_at`.
- `arrived`, `closed`: `1.0`.

### `blocks[]`

Ready cargo (`status = ready`) grouped by local days on the warehouse,
`days = today − local date of ready_at` (Asia/Vladivostok): A ≤ 2, B 3–4, C 5–6, D ≥ 7.
Always four items in order A, B, C, D. `places` and `cargo` are sums/counts; `fill` is
`places / WAREHOUSE_BLOCK_CAPACITY` (setting, default 400 places per block), rounded to
4 decimals and clamped to `0.0..1.0`.

### `summary`

Numbers of the KPI tiles (`panel.ops_data.kpis()`), «today» = local date:

| field | meaning |
|---|---|
| `awaiting_places`, `awaiting_cargo` | places / cargo with status `ready` |
| `awaiting_delta` | places that became ready today (still ready or already picked up) |
| `trucks_in_transit`, `trucks_active` | `in_transit` trucks / all trucks that are not `closed` |
| `order_built` | today's daily order exists |
| `order_pending`, `order_total` | lines not called yet (`pending`, cargo not picked up) / all lines; `0/0` if not built |
| `picked_up_today`, `picked_up_places` | cargo / places picked up since local midnight |

## Page events (`document` CustomEvents)

All events are dispatched on and listened to on `document`.

| event | direction | `detail` | meaning |
|---|---|---|---|
| `ops:data` | page → scene | the JSON above | Fired once right after load (payload embedded in the page as `#ops-scene-data`) and after every poll (`static/js/ops/data.js`, every 15 s while the tab is visible). |
| `ops:request-data` | scene → page | — | Ask `data.js` to fire `ops:data` again with the latest payload (for modules loaded later). |
| `ops:select-truck` | scene → page | `{id, open?}` | Select a truck: the page loads its drawer and tracker (`GET /panel/ops/truck/<id>/`, HTMX) and on mobile opens the bottom sheet unless `open: false`. |
| `ops:selected` | page → scene | `{id}` | The selected truck changed (fleet click, route map click or `ops:select-truck`). The initially selected id is the value of `#ops-selected`. |

## Mount point

```html
<div class="ops-scene" id="ops-scene" data-scene-url="/panel/api/scene/" data-mode="fallback">
  <div class="route-map" data-route-map>…</div>
</div>
```

- `#ops-scene` fills the stage between the KPI row and the bottom block; the pipeline
  (left, 280 px) and the drawer (right, 360 px) are laid over it on ≥ 900 px.
- The 3D scene (T9) sets `data-mode="3d"` and may replace the children of `#ops-scene`.
  The flat route map (`static/js/ops/route-map.js`) stops drawing when `data-mode="3d"` or
  when its `[data-route-map]` element is removed. Under 900 px or without WebGL keep the
  route map.

## Partials (HTMX)

| URL | name | returns |
|---|---|---|
| `GET /panel/ops/truck/<id>/` | `panel:ops_truck` | `#ops-drawer` (main swap, `outerHTML`) + `#ops-tracker` with `hx-swap-oob`; 404 for an unknown truck |
| `GET /panel/ops/live/?truck=<id>` | `panel:ops_live` | `#ops-kpis` (main swap) + `#ops-pipeline`, `#ops-fleet`, `#ops-timeline` out of band, and with a valid `truck` also `#ops-drawer` and `#ops-tracker`; polled every 30 s by the KPI row |

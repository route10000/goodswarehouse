"""Ops-центр: aggregations (``panel.ops_data``), scene JSON, page and HTMX partials."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from logistics import services
from logistics.models import Cargo, DailyOrder, DailyOrderLine, Truck
from panel import ops_data

pytestmark = pytest.mark.django_db

VL = ZoneInfo("Asia/Vladivostok")
CallStatus = DailyOrderLine.CallStatus


def local(*args) -> datetime:
    """Aware datetime in Vladivostok local time: ``local(2026, 10, 18, 0, 30)``."""
    return datetime(*args, tzinfo=VL)


# Half an hour after local midnight: in UTC it is still the previous day (14:30).
MIDNIGHT = local(2026, 10, 18, 0, 30)
NOON = local(2026, 10, 18, 12, 0)


def loading_truck(client, places=(), **fields):
    truck = services.create_truck(**fields)
    for index, count in enumerate(places):
        services.add_cargo(client, f"L{truck.pk}-{index}", places=count, truck=truck)
    return truck


def transit_truck(client, places=(), departed=None, **fields):
    truck = loading_truck(client, places, **fields)
    services.truck_depart(truck, now=departed or NOON - timedelta(hours=2))
    return truck


def arrived_truck(client, places=(), arrived=None, **fields):
    arrived = arrived or NOON - timedelta(hours=1)
    truck = transit_truck(client, places, departed=arrived - timedelta(hours=2), **fields)
    services.truck_arrive(truck, now=arrived)
    return truck


def ready_cargo(truck):
    return list(truck.cargos.filter(status=Cargo.Status.READY).order_by("id"))


def order_line(order, cargo, call_status=CallStatus.PENDING):
    """Test fixture: lines for a fixed (possibly past) date bypass the read-only check."""
    return DailyOrderLine.objects.create(
        order=order,
        cargo=cargo,
        article=cargo.article,
        phone=cargo.client.phone,
        places=cargo.places,
        call_status=call_status,
    )


# ---------- KPI ----------


def test_kpis_on_empty_db():
    kpis = ops_data.kpis(now=NOON)

    assert kpis == {
        "awaiting_places": 0,
        "awaiting_cargo": 0,
        "awaiting_delta": 0,
        "trucks_in_transit": 0,
        "trucks_active": 0,
        "order_built": False,
        "order_pending": 0,
        "order_total": 0,
        "picked_up_today": 0,
        "picked_up_places": 0,
    }


def test_awaiting_counts_places_of_ready_cargo_only(ivan, manager):
    arrived = arrived_truck(ivan, places=(3, 2, 4))
    loading_truck(ivan, places=(5,))
    services.cargo_pickup(ready_cargo(arrived)[2], manager, paid=True, now=NOON)

    kpis = ops_data.kpis(now=NOON)

    assert kpis["awaiting_places"] == 5
    assert kpis["awaiting_cargo"] == 2


def test_awaiting_delta_is_places_ready_since_local_midnight(ivan):
    arrived_truck(ivan, places=(2,), arrived=local(2026, 10, 17, 23, 50))
    arrived_truck(ivan, places=(3,), arrived=local(2026, 10, 18, 0, 10))

    kpis = ops_data.kpis(now=MIDNIGHT)

    assert kpis["awaiting_places"] == 5
    assert kpis["awaiting_delta"] == 3


def test_trucks_in_transit_of_non_closed(ivan, manager):
    loading_truck(ivan, places=(1,))
    transit_truck(ivan, places=(1,))
    transit_truck(ivan, places=(1,))
    arrived_truck(ivan, places=(1,))
    closed = arrived_truck(ivan, places=(1,))
    services.cargo_pickup(ready_cargo(closed)[0], manager, paid=True, now=NOON)

    kpis = ops_data.kpis(now=NOON)

    assert (kpis["trucks_in_transit"], kpis["trucks_active"]) == (2, 4)


def test_order_kpi_when_order_not_built(petr):
    arrived_truck(petr, places=(1,))

    kpis = ops_data.kpis(now=NOON)

    assert (kpis["order_built"], kpis["order_pending"], kpis["order_total"]) == (False, 0, 0)


def test_order_kpi_counts_pending_lines_of_total(petr, manager):
    cargos = ready_cargo(arrived_truck(petr, places=(1, 1, 1, 1, 1)))
    order = DailyOrder.objects.create(date=NOON.date())
    order_line(order, cargos[0])
    order_line(order, cargos[1])
    order_line(order, cargos[2], CallStatus.REACHED)
    order_line(order, cargos[3], CallStatus.NO_ANSWER)
    picked = order_line(order, cargos[4])
    services.cargo_pickup(picked.cargo, manager, paid=True, now=NOON)

    kpis = ops_data.kpis(now=NOON)

    # The picked-up cargo needs no call any more, but its line stays in the total.
    assert (kpis["order_built"], kpis["order_pending"], kpis["order_total"]) == (True, 2, 5)


def test_order_kpi_uses_local_date_after_midnight(petr):
    cargo = ready_cargo(arrived_truck(petr, places=(1,), arrived=local(2026, 10, 17, 10, 0)))[0]
    order_line(DailyOrder.objects.create(date=date(2026, 10, 17)), cargo)
    today = DailyOrder.objects.create(date=date(2026, 10, 18))
    order_line(today, cargo, CallStatus.REACHED)

    kpis = ops_data.kpis(now=MIDNIGHT)

    assert (kpis["order_pending"], kpis["order_total"]) == (0, 1)


def test_picked_up_today_counts_from_local_midnight(ivan, manager):
    first, second, third = ready_cargo(
        arrived_truck(ivan, places=(2, 3, 4), arrived=local(2026, 10, 17, 9, 0))
    )
    services.cargo_pickup(first, manager, paid=True, now=local(2026, 10, 17, 23, 50))
    services.cargo_pickup(second, manager, paid=True, now=local(2026, 10, 18, 0, 10))

    kpis = ops_data.kpis(now=MIDNIGHT)

    assert (kpis["picked_up_today"], kpis["picked_up_places"]) == (1, 3)
    assert third.status == Cargo.Status.READY


# ---------- pipeline ----------


def test_pipeline_stages_and_values(ivan, petr, manager):
    loading_truck(ivan, places=(2, 3))
    loading_truck(ivan, places=(4,))
    loading_truck(ivan)  # empty truck still counts as loading
    transit_truck(ivan, places=(6,))
    cargos = ready_cargo(arrived_truck(petr, places=(1, 2, 3, 5)))
    order = DailyOrder.objects.create(date=NOON.date())
    order_line(order, cargos[0])
    order_line(order, cargos[1], CallStatus.NO_ANSWER)
    order_line(order, cargos[2], CallStatus.REACHED)
    services.cargo_pickup(cargos[3], manager, paid=False, now=NOON)

    stages = ops_data.pipeline(now=NOON)

    assert [s["key"] for s in stages] == ["loading", "in_transit", "ready", "order", "picked_up"]
    assert [s["label"] for s in stages] == [
        "Грузятся в Уссурийске",
        "В пути",
        "На складе, ждут выдачи",
        "В ордере на звонок",
        "Выдано сегодня",
    ]
    by_key = {s["key"]: s for s in stages}
    assert by_key["loading"]["value"] == 9
    assert by_key["loading"]["caption"] == "3 машины"
    assert by_key["in_transit"]["value"] == 6
    assert by_key["in_transit"]["caption"] == "1 машина"
    assert by_key["ready"]["value"] == 6
    assert by_key["ready"]["caption"] == "3 груза"
    assert by_key["order"]["value"] == 2
    assert by_key["order"]["caption"] == "не дозвонились: 1"
    assert by_key["picked_up"]["value"] == 1
    assert by_key["picked_up"]["caption"] == "5 мест"


def test_pipeline_order_stage_without_order():
    stages = {s["key"]: s for s in ops_data.pipeline(now=NOON)}

    assert stages["order"]["value"] == 0
    assert stages["order"]["caption"] == "Ордер ещё не сформирован"


# ---------- progress ----------


def progress(status, **kwargs):
    defaults = {"now": NOON, "departed_at": None, "eta": None, "places_loaded": 0}
    defaults["capacity_places"] = 60
    return ops_data.truck_progress(status, **{**defaults, **kwargs})


def test_progress_loading_is_share_of_capacity():
    assert progress("loading", places_loaded=15, capacity_places=60) == 0.25


def test_progress_loading_without_cargo_is_zero():
    assert progress("loading") == 0.0


def test_progress_loading_over_capacity_is_clamped():
    assert progress("loading", places_loaded=70, capacity_places=60) == 1.0


def test_progress_in_transit_is_elapsed_share_of_trip():
    value = progress(
        "in_transit", departed_at=NOON - timedelta(hours=1), eta=NOON + timedelta(hours=3)
    )

    assert value == 0.25


def test_progress_in_transit_is_clamped():
    late = progress(
        "in_transit", departed_at=NOON - timedelta(hours=3), eta=NOON - timedelta(hours=1)
    )
    early = progress(
        "in_transit", departed_at=NOON + timedelta(hours=1), eta=NOON + timedelta(hours=3)
    )

    assert (late, early) == (1.0, 0.0)


def test_progress_in_transit_without_eta_or_departure_is_zero():
    assert progress("in_transit", departed_at=NOON - timedelta(hours=1)) == 0.0
    assert progress("in_transit", eta=NOON + timedelta(hours=1)) == 0.0


def test_progress_in_transit_with_eta_before_departure_is_done():
    assert progress("in_transit", departed_at=NOON, eta=NOON - timedelta(hours=1)) == 1.0


@pytest.mark.parametrize("status", ["arrived", "closed"])
def test_progress_arrived_and_closed_is_one(status):
    assert progress(status) == 1.0


# ---------- warehouse blocks ----------


@pytest.mark.parametrize(
    ("days", "key"),
    [(0, "A"), (2, "A"), (3, "B"), (4, "B"), (5, "C"), (6, "C"), (7, "D"), (30, "D")],
)
def test_block_by_days_waiting(days, key):
    assert ops_data.block_key(days) == key


def test_blocks_group_ready_places_by_local_days(ivan, settings):
    settings.WAREHOUSE_BLOCK_CAPACITY = 10
    # Local dates matter: 23:59 two days ago is still «2 days», 00:01 three days ago is «3».
    arrived_truck(ivan, places=(2, 3), arrived=local(2026, 10, 16, 23, 59))
    arrived_truck(ivan, places=(4,), arrived=local(2026, 10, 15, 0, 1))
    arrived_truck(ivan, places=(12,), arrived=local(2026, 10, 11, 8, 0))

    blocks = ops_data.blocks(now=MIDNIGHT)

    assert [(b["key"], b["places"], b["cargo"], b["fill"]) for b in blocks] == [
        ("A", 5, 2, 0.5),
        ("B", 4, 1, 0.4),
        ("C", 0, 0, 0.0),
        ("D", 12, 1, 1.0),
    ]
    assert [b["label"] for b in blocks] == ["Блок A", "Блок B", "Блок C", "Блок D"]
    assert [b["caption"] for b in blocks] == ["≤ 2 дн", "3–4 дн", "5–6 дн", "≥ 7 дн"]


def test_block_capacity_setting_defaults_to_400(settings):
    assert settings.WAREHOUSE_BLOCK_CAPACITY == 400


# ---------- fleet ----------


def test_fleet_order_and_closed_window(ivan, manager):
    later = transit_truck(ivan, places=(1,), eta=NOON + timedelta(hours=5))
    sooner = transit_truck(ivan, places=(1,), eta=NOON + timedelta(hours=1))
    no_eta = transit_truck(ivan, places=(1,))
    arrived = arrived_truck(ivan, places=(1,))
    load_late = loading_truck(ivan, departure_date=date(2026, 10, 20))
    load_soon = loading_truck(ivan, departure_date=date(2026, 10, 19))
    load_undated = loading_truck(ivan)
    fresh = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(hours=5))
    services.cargo_pickup(ready_cargo(fresh)[0], manager, paid=True, now=NOON - timedelta(hours=2))
    stale = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(days=3))
    services.cargo_pickup(ready_cargo(stale)[0], manager, paid=True, now=NOON - timedelta(hours=30))

    items = ops_data.fleet(now=NOON)

    assert [i.id for i in items] == [
        arrived.pk,
        sooner.pk,
        later.pk,
        no_eta.pk,
        load_soon.pk,
        load_late.pk,
        load_undated.pk,
        fresh.pk,
    ]
    assert stale.pk not in [i.id for i in items]


def test_fleet_item_numbers(ivan, petr):
    truck = loading_truck(ivan, places=(2, 3))
    services.add_cargo(petr, "P1", places=4, weight_kg="12.5", truck=truck)
    Cargo.objects.create(client=petr, truck=truck, article="R1", places=1)  # received, not loaded

    item = ops_data.fleet(now=NOON)[0]

    assert (item.places_total, item.places_loaded, item.clients) == (10, 9, 2)
    assert float(item.weight_kg) == 12.5
    assert item.capacity_places == 60
    assert item.progress == 0.15
    assert item.places_text == "9/60 мест"
    assert item.short_number == "R-" + truck.number.rsplit("-", 1)[1]


def test_fleet_closed_window_uses_closed_at(ivan):
    # Arrived long ago, closed by hand 2 h ago: still shown.
    recent = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(days=3))
    services.truck_close(recent, now=NOON - timedelta(hours=2))
    # Arrived 1 h ago but closed_at says 30 h: hidden (closed_at wins over arrived_at).
    old = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(hours=1))
    services.truck_close(old, now=NOON - timedelta(hours=30))
    # Legacy rows without closed_at fall back to arrived_at.
    legacy_fresh = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(hours=3))
    services.truck_close(legacy_fresh)
    legacy_stale = arrived_truck(ivan, places=(1,), arrived=NOON - timedelta(hours=26))
    services.truck_close(legacy_stale)
    Truck.objects.filter(pk__in=[legacy_fresh.pk, legacy_stale.pk]).update(closed_at=None)

    ids = [i.id for i in ops_data.fleet(now=NOON)]

    assert ids == [recent.pk, legacy_fresh.pk]


def test_fleet_item_texts(ivan):
    in_time = transit_truck(ivan, places=(1,), eta=NOON + timedelta(minutes=135))
    late = transit_truck(ivan, places=(1,), eta=NOON - timedelta(minutes=20))

    items = {i.id: i for i in ops_data.fleet(now=NOON)}

    assert items[in_time.pk].when_text == "через 2 ч 15 мин"
    assert items[late.pk].when_text == "опаздывает 20 мин"
    assert items[late.pk].is_late is True


def test_default_truck_is_nearest_to_arrival(ivan):
    arrived_truck(ivan, places=(1,))
    transit_truck(ivan, places=(1,), eta=NOON + timedelta(hours=4))
    soon = transit_truck(ivan, places=(1,), eta=NOON + timedelta(hours=1))

    assert ops_data.default_truck(ops_data.fleet(now=NOON)).id == soon.pk
    assert ops_data.default_truck([]) is None


# ---------- day timeline ----------


def test_day_events_with_local_times(ivan):
    departed = transit_truck(
        ivan, places=(1,), departed=local(2026, 10, 18, 9, 10), eta=local(2026, 10, 18, 15, 40)
    )
    arrived = arrived_truck(ivan, places=(1,), arrived=local(2026, 10, 18, 14, 20))
    planned = loading_truck(ivan, places=(1,), departure_date=date(2026, 10, 18))
    loading_truck(ivan, places=(1,), departure_date=date(2026, 10, 19))
    arrived_truck(ivan, places=(1,), arrived=local(2026, 10, 17, 23, 0))  # yesterday

    events = ops_data.day_timeline(now=local(2026, 10, 18, 16, 0))["events"]

    assert [(e["kind"], e["truck_id"], e["time"]) for e in events] == [
        ("departed", departed.pk, "09:10"),
        ("departed", arrived.pk, "12:20"),
        ("arrived", arrived.pk, "14:20"),
        ("eta", departed.pk, "15:40"),
        ("departure_planned", planned.pk, None),
    ]


def test_day_timeline_bars_on_hour_scale(ivan):
    truck = transit_truck(
        ivan, places=(1,), departed=local(2026, 10, 18, 10, 0), eta=local(2026, 10, 18, 14, 0)
    )

    timeline = ops_data.day_timeline(now=NOON)

    assert [h["label"] for h in timeline["hours"]] == [
        "06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"
    ]  # fmt: skip
    assert timeline["now_pct"] == 37.5
    (row,) = timeline["rows"]
    assert row["item"].id == truck.pk
    bars = {b["kind"]: b for b in row["bars"]}
    assert (bars["transit"]["left"], bars["transit"]["width"]) == (25.0, 25.0)
    assert bars["arrival"]["left"] == 50.0
    assert bars["arrival"]["planned"] is True


def test_day_timeline_hides_now_line_outside_scale():
    assert ops_data.day_timeline(now=MIDNIGHT)["now_pct"] is None
    assert ops_data.day_timeline(now=MIDNIGHT)["rows"] == []


# ---------- journey ----------


def test_journey_loading_caption_shows_capacity(ivan):
    loading_truck(ivan, places=(2, 3), capacity_places=40)

    steps = ops_data.journey(ops_data.fleet(now=NOON)[0], now=NOON)

    assert steps[0]["caption"] == "5/40 мест"
    assert steps[0]["state"] == "current"


def test_journey_states_follow_truck_status(ivan):
    truck = transit_truck(ivan, places=(2,), eta=NOON + timedelta(hours=1))
    item = ops_data.fleet(now=NOON)[0]

    steps = ops_data.journey(item, now=NOON)

    assert [s["label"] for s in steps] == [
        "Погрузка",
        "Отправлена",
        "В пути",
        "Разгрузка",
        "На складе",
    ]
    assert [s["state"] for s in steps] == ["done", "done", "current", "future", "future"]
    assert steps[2]["caption"] == "ETA 13:00"
    assert truck.status == Truck.Status.IN_TRANSIT


# ---------- scene JSON ----------


def test_scene_payload_shape_and_values(ivan, settings):
    settings.WAREHOUSE_BLOCK_CAPACITY = 100
    transit = transit_truck(
        ivan, places=(3, 2), departed=NOON - timedelta(hours=1), eta=NOON + timedelta(hours=1)
    )
    arrived_truck(ivan, places=(4,), arrived=NOON - timedelta(hours=1))

    payload = ops_data.scene(now=NOON)

    assert set(payload) == {"generated_at", "route", "trucks", "blocks", "summary"}
    assert payload["generated_at"] == "2026-10-18T12:00:00+10:00"
    assert payload["route"] == {"from": "Уссурийск", "to": "Владивосток", "km": 110}
    truck = next(t for t in payload["trucks"] if t["id"] == transit.pk)
    assert truck == {
        "id": transit.pk,
        "number": transit.number,
        "status": "in_transit",
        "status_label": "В пути",
        "progress": 0.5,
        "places": 5,
        "places_loaded": 5,
        "capacity_places": 60,
        "clients": 1,
        "weight_kg": 0.0,
        "departed_at": "2026-10-18T11:00:00+10:00",
        "eta": "2026-10-18T13:00:00+10:00",
        "arrived_at": None,
        "url": f"/panel/trucks/{transit.pk}/",
    }
    assert payload["blocks"][0] == {
        "key": "A",
        "label": "Блок A",
        "caption": "≤ 2 дн",
        "places": 4,
        "cargo": 1,
        "fill": 0.04,
    }
    assert payload["summary"]["awaiting_places"] == 4
    assert payload["summary"]["trucks_in_transit"] == 1
    json.dumps(payload)  # plain JSON types only


# ---------- views ----------


def test_scene_api_requires_login(client):
    response = client.get("/panel/api/scene/")

    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/")


@pytest.mark.parametrize("http", ["mgr", "wh"])
def test_scene_api_for_both_groups(http, request, ivan):
    arrived_truck(ivan, places=(2,), arrived=timezone.now() - timedelta(hours=1))

    response = request.getfixturevalue(http).get("/panel/api/scene/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    data = response.json()
    assert len(data["trucks"]) == 1
    assert [b["key"] for b in data["blocks"]] == ["A", "B", "C", "D"]


def test_scene_api_leaks_no_client_data(wh, ivan, petr):
    truck = loading_truck(ivan, places=(1,))
    services.add_cargo(petr, "SECRET-ART", places=1, description="Шуба норковая", truck=truck)

    response = wh.get("/panel/api/scene/")
    body = json.dumps(response.json(), ensure_ascii=False)

    assert "Уссурийск" in body  # decoded text, so Cyrillic secrets would show up too
    for secret in ["Иван", "Пётр", "914123456", "SECRET-ART", "Шуба", ivan.code, petr.code]:
        assert secret not in body


def test_ops_page_renders_with_empty_db(wh):
    response = wh.get("/panel/")

    assert response.status_code == 200
    page = response.content.decode()
    for text in ["Ждут выдачи", "Машин в пути", "Ордер на сегодня", "Выдано сегодня"]:
        assert text in page
    assert 'id="ops-scene"' in page
    assert "Машин пока нет" in page
    assert response.context["selected"] is None


def test_ops_page_renders_with_data_and_selects_nearest_truck(mgr, ivan):
    now = timezone.now()
    transit_truck(
        ivan, places=(2,), departed=now - timedelta(hours=1), eta=now + timedelta(hours=4)
    )
    soon = transit_truck(
        ivan, places=(3,), departed=now - timedelta(hours=1), eta=now + timedelta(hours=1)
    )
    loading_truck(ivan, places=(5,))

    response = mgr.get("/panel/")

    assert response.status_code == 200
    assert response.context["selected"].id == soon.pk
    page = response.content.decode()
    assert soon.number in page
    assert 'id="ops-drawer"' in page
    assert 'id="ops-tracker"' in page
    assert 'id="ops-scene-data"' in page


def test_truck_partial_returns_drawer_and_oob_tracker(wh, ivan):
    truck = loading_truck(ivan, places=(2, 3))

    response = wh.get(f"/panel/ops/truck/{truck.pk}/", HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    page = response.content.decode()
    assert page.lstrip().startswith('<div class="drawer" id="ops-drawer"')
    assert 'id="ops-tracker" hx-swap-oob="true"' in page
    assert truck.number in page
    assert "5/60 мест" in page


def test_truck_partial_404_for_missing_truck(wh):
    assert wh.get("/panel/ops/truck/999/").status_code == 404


def test_truck_partial_requires_login(client, truck):
    assert client.get(f"/panel/ops/truck/{truck.pk}/").status_code == 302


def test_live_partial_refreshes_kpis_pipeline_fleet(wh, ivan):
    truck = loading_truck(ivan, places=(2,))

    response = wh.get("/panel/ops/live/", {"truck": truck.pk}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    page = response.content.decode()
    assert page.lstrip().startswith('<section class="kpi-row" id="ops-kpis"')
    assert 'hx-trigger="every 30s"' in page
    assert 'id="ops-pipeline" hx-swap-oob="true"' in page
    assert 'id="ops-fleet" hx-swap-oob="true"' in page
    assert 'id="ops-drawer" hx-swap-oob="true"' in page
    assert 'id="ops-tracker" hx-swap-oob="true"' in page


def test_live_partial_ignores_bad_truck_param(wh):
    response = wh.get("/panel/ops/live/", {"truck": "x"})

    assert response.status_code == 200
    assert 'id="ops-drawer"' not in response.content.decode()

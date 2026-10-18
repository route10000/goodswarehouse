"""«Аналитика»: aggregations (``panel.analytics_data``), period parsing, page and access."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client as HttpClient

from logistics import services
from logistics.models import Cargo, ContactAttempt
from panel import analytics_data as ad

pytestmark = pytest.mark.django_db

VL = ZoneInfo("Asia/Vladivostok")


def local(*args) -> datetime:
    """Aware datetime in Vladivostok local time: ``local(2026, 10, 18, 0, 30)``."""
    return datetime(*args, tzinfo=VL)


# Noon of 18 October; a 7-day period is 12–18 October, the previous one 5–11 October.
NOW = local(2026, 10, 18, 12, 0)
START_7 = local(2026, 10, 12, 0, 0)


def arrived(client, places, at, capacity=60, departed=None):
    """A trip that arrived at ``at``; its cargo became ready then. Returns (truck, cargos)."""
    truck = services.create_truck(capacity_places=capacity)
    for index, count in enumerate(places):
        services.add_cargo(client, f"T{truck.pk}-{index}", places=count, truck=truck)
    services.truck_depart(truck, now=departed or at - timedelta(hours=2))
    services.truck_arrive(truck, now=at)
    truck.refresh_from_db()
    return truck, list(truck.cargos.order_by("id"))


def departed(client, places, at, capacity=60):
    truck = services.create_truck(capacity_places=capacity)
    for index, count in enumerate(places):
        services.add_cargo(client, f"D{truck.pk}-{index}", places=count, truck=truck)
    services.truck_depart(truck, now=at)
    truck.refresh_from_db()
    return truck


def pick(cargo, user, at):
    services.cargo_pickup(cargo, user, paid=True, now=at)


def call(client, user, outcome, at):
    """Test fixture: a journal entry at a fixed past moment (``created_at`` is auto)."""
    attempt = ContactAttempt.objects.create(client=client, staff=user, outcome=outcome)
    ContactAttempt.objects.filter(pk=attempt.pk).update(created_at=at)
    return attempt


def chart(data, chart_id):
    return next(c for c in data["charts"] if c["id"] == chart_id)


# ---------- period ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7", 7), ("30", 30), ("90", 90), (None, 30), ("", 30), ("14", 30), ("abc", 30), ("-7", 30)],
)
def test_parse_period(raw, expected):
    assert ad.parse_period(raw) == expected


def test_period_covers_local_days_ending_today():
    period = ad.period(7, now=NOW)

    assert period.days == 7
    assert period.start == START_7
    assert period.end == local(2026, 10, 19, 0, 0)
    assert period.prev_start == local(2026, 10, 5, 0, 0)
    assert period.dates[0] == date(2026, 10, 12)
    assert period.dates[-1] == date(2026, 10, 18)
    assert len(period.dates) == 7


def test_period_right_after_local_midnight_is_the_new_local_day():
    # 00:30 in Vladivostok is still 17 October in UTC.
    period = ad.period(7, now=local(2026, 10, 18, 0, 30))
    assert period.dates[-1] == date(2026, 10, 18)
    assert period.start == START_7


# ---------- flow: places received vs picked up ----------


def test_flow_counts_by_local_day_and_fills_gaps(ivan, manager):
    _t, early = arrived(ivan, [2], at=local(2026, 10, 11, 23, 59))  # previous period
    _t, first = arrived(ivan, [3, 1], at=START_7)  # exactly at the boundary: included
    _t, late = arrived(ivan, [4], at=local(2026, 10, 18, 0, 10))  # UTC: 17 October
    pick(first[0], manager, at=local(2026, 10, 18, 0, 20))

    flow = ad.flow(ad.period(7, now=NOW))

    assert flow["labels"] == ["12.10", "13.10", "14.10", "15.10", "16.10", "17.10", "18.10"]
    assert flow["titles"][0] == "12 октября"
    received, picked = flow["series"]
    assert received["key"] == "received" and received["color"] == "chart-1"
    assert picked["key"] == "picked" and picked["color"] == "chart-2"
    assert received["data"] == [4, 0, 0, 0, 0, 0, 4]
    assert picked["data"] == [0, 0, 0, 0, 0, 0, 3]
    assert flow["empty"] is False
    assert flow["table"]["rows"][0] == ["12 октября", "4", "0"]


def test_flow_empty():
    flow = ad.flow(ad.period(7, now=NOW))
    assert flow["empty"] is True
    assert flow["series"][0]["data"] == [0] * 7


# ---------- KPI ----------


def test_kpis_values_and_deltas(ivan, petr, manager):
    _t, prev = arrived(ivan, [5], at=local(2026, 10, 8, 10, 0))
    pick(prev[0], manager, at=local(2026, 10, 9, 10, 0))  # 1 day of storage, previous period
    _t, cur = arrived(petr, [3, 4], at=local(2026, 10, 12, 0, 0))
    pick(cur[0], manager, at=local(2026, 10, 14, 12, 0))  # 2.5 days
    pick(cur[1], manager, at=local(2026, 10, 13, 12, 0))  # 1.5 days
    call(ivan, manager, "reached", local(2026, 10, 13, 9, 0))
    call(ivan, manager, "reached", local(2026, 10, 14, 9, 0))
    call(petr, manager, "no_answer", local(2026, 10, 15, 9, 0))
    call(petr, manager, "no_answer", local(2026, 10, 10, 9, 0))  # previous: 0 %

    kpis = {k["key"]: k for k in ad.kpis(ad.period(7, now=NOW))}

    assert kpis["received"]["value"] == 7
    assert kpis["received"]["delta"] == 2
    assert kpis["received"]["delta_display"] == "↑ 2"
    assert kpis["received"]["unit"] == "мест"
    assert kpis["picked"]["value"] == 7
    assert kpis["picked"]["delta"] == 2
    assert kpis["storage"]["value"] == 2.0
    assert kpis["storage"]["display"] == "2"
    assert kpis["storage"]["delta"] == 1.0
    assert kpis["storage"]["delta_display"] == "↑ 1 дн"
    assert kpis["calls"]["value"] == 67
    assert kpis["calls"]["delta"] == 67
    assert kpis["calls"]["delta_display"] == "↑ 67 п.п."
    assert kpis["received"]["compare"] == "к прошлым 7 дням"
    assert [k["label"] for k in ad.kpis(ad.period(7, now=NOW))] == [
        "Принято мест",
        "Выдано мест",
        "Среднее хранение",
        "Дозвон",
    ]


def test_kpis_on_empty_db():
    kpis = {k["key"]: k for k in ad.kpis(ad.period(30, now=NOW))}

    assert kpis["received"]["value"] == 0
    assert kpis["received"]["delta"] == 0
    assert kpis["received"]["delta_display"] == "без изменений"
    assert kpis["storage"]["value"] is None
    assert kpis["storage"]["display"] == "—"
    assert kpis["storage"]["delta"] is None
    assert kpis["calls"]["value"] is None
    assert kpis["calls"]["display"] == "—"
    assert kpis["received"]["compare"] == "к прошлым 30 дням"


def test_kpi_delta_down_and_missing_previous(ivan, manager):
    _t, prev = arrived(ivan, [9], at=local(2026, 10, 6, 10, 0))
    arrived(ivan, [4], at=local(2026, 10, 16, 10, 0))
    call(ivan, manager, "reached", local(2026, 10, 16, 11, 0))

    kpis = {k["key"]: k for k in ad.kpis(ad.period(7, now=NOW))}

    assert kpis["received"]["delta"] == -5
    assert kpis["received"]["delta_display"] == "↓ 5"
    assert kpis["calls"]["value"] == 100
    assert kpis["calls"]["delta"] is None
    assert kpis["calls"]["delta_display"] == "нет данных за прошлый период"


def test_quarter_compare_caption():
    kpis = ad.kpis(ad.period(90, now=NOW))
    assert kpis[0]["compare"] == "к прошлому кварталу"


# ---------- stock by age ----------


def test_stock_by_age_uses_warehouse_blocks(ivan, manager):
    arrived(ivan, [2], at=local(2026, 10, 18, 0, 10))  # 0 days → A
    arrived(ivan, [3], at=local(2026, 10, 15, 23, 50))  # 3 days → B
    _t, gone = arrived(ivan, [5], at=local(2026, 10, 1, 10, 0))  # picked up: not in stock
    pick(gone[0], manager, at=local(2026, 10, 2, 10, 0))
    arrived(ivan, [1], at=local(2026, 10, 1, 10, 0))  # 17 days → D

    stock = ad.stock_by_age(now=NOW)

    assert stock["labels"] == ["A · ≤ 2 дн", "B · 3–4 дн", "C · 5–6 дн", "D · ≥ 7 дн"]
    assert stock["series"][0]["data"] == [2, 3, 0, 1]
    assert stock["series"][0]["colors"] == ["age-a", "age-b", "age-c", "age-d"]
    assert stock["empty"] is False
    assert stock["table"]["rows"][1] == ["B", "3–4 дн", "3", "1"]


def test_stock_by_age_empty():
    assert ad.stock_by_age(now=NOW)["empty"] is True


# ---------- truck fill ----------


def test_truck_fill_per_departed_trip(ivan):
    departed(ivan, [30], at=local(2026, 10, 11, 23, 0))  # before the period
    low = departed(ivan, [20, 10], at=START_7)
    full = departed(ivan, [39], at=local(2026, 10, 17, 9, 0), capacity=40)
    departed(ivan, [5], at=local(2026, 10, 18, 9, 0), capacity=8)
    services.create_truck()  # still loading: not a trip yet

    fill = ad.truck_fill(ad.period(7, now=NOW))

    assert fill["labels"][:2] == [low.number, full.number]
    assert fill["series"][0]["data"] == [50, 98, 63]
    assert fill["low"] == [True, False, False]
    assert fill["series"][0]["colors"] == ["age-a", "black", "black"]
    assert fill["threshold"] == 60
    assert fill["table"]["rows"][0] == [low.number, "12 октября", "30", "60", "50", "ниже 60 %"]


def test_truck_fill_empty():
    assert ad.truck_fill(ad.period(7, now=NOW))["empty"] is True


# ---------- calls ----------


def test_calls_per_local_day(ivan, manager):
    call(ivan, manager, "reached", local(2026, 10, 12, 0, 5))  # UTC: 11 October
    call(ivan, manager, "no_answer", local(2026, 10, 12, 18, 0))
    call(ivan, manager, "no_answer", local(2026, 10, 18, 11, 0))
    call(ivan, manager, "reached", local(2026, 10, 11, 23, 55))  # previous period

    calls = ad.calls(ad.period(7, now=NOW))

    reached, no_answer = calls["series"]
    assert (reached["key"], reached["color"]) == ("reached", "chart-1")
    assert (no_answer["key"], no_answer["color"]) == ("no_answer", "chart-2")
    assert reached["data"] == [1, 0, 0, 0, 0, 0, 0]
    assert no_answer["data"] == [1, 0, 0, 0, 0, 0, 1]
    assert calls["table"]["rows"][0] == ["12 октября", "1", "1", "50"]
    assert calls["table"]["rows"][1] == ["13 октября", "0", "0", "—"]


# ---------- transit ----------


def test_transit_hours_per_trip(ivan):
    first, _ = arrived(
        ivan, [1], at=local(2026, 10, 12, 12, 30), departed=local(2026, 10, 12, 10, 0)
    )
    second, _ = arrived(
        ivan, [1], at=local(2026, 10, 15, 13, 0), departed=local(2026, 10, 15, 9, 0)
    )
    departed(ivan, [1], at=local(2026, 10, 18, 9, 0))  # on the road: no transit time yet
    arrived(ivan, [1], at=local(2026, 10, 11, 12, 0), departed=local(2026, 10, 11, 9, 0))

    transit = ad.transit(ad.period(7, now=NOW))

    assert transit["labels"] == [first.number, second.number]
    assert transit["series"][0]["data"] == [2.5, 4.0]
    assert transit["series"][0]["color"] == "black"
    assert transit["table"]["rows"][0] == [
        first.number,
        "12 октября 10:00",
        "12 октября 12:30",
        "2,5",
    ]


# ---------- top clients ----------


def test_top_clients_by_places_received(ivan, petr):
    others = [
        services.get_or_create_client(f"+7914555{i:04d}", f"Клиент {i}")[0] for i in range(10)
    ]
    for index, client in enumerate(others):
        arrived(client, [index + 1], at=local(2026, 10, 14, 10, 0))
    arrived(ivan, [20, 5], at=local(2026, 10, 15, 10, 0))
    arrived(petr, [50], at=local(2026, 10, 5, 10, 0))  # previous period: not counted

    top = ad.top_clients(ad.period(7, now=NOW))

    assert len(top["labels"]) == 10
    assert top["labels"][0] == f"Иван · {ivan.code}"
    assert top["series"][0]["data"][:3] == [25, 10, 9]
    assert top["table"]["rows"][0] == ["1", "Иван", ivan.code, "25", "2"]
    dumped = json.dumps(top, ensure_ascii=False)
    assert "+7914" not in dumped and "+7914555" not in dumped and "phone" not in dumped


# ---------- whole page data ----------


def test_analytics_shape_and_query_budget(ivan, petr, manager, django_assert_max_num_queries):
    for day in range(1, 8):
        _t, cargos = arrived(ivan, [day, 2], at=local(2026, 10, 10 + day, 10, 0))
        pick(cargos[0], manager, at=local(2026, 10, 11 + day, 10, 0))
        call(petr, manager, "reached", local(2026, 10, 10 + day, 11, 0))

    with django_assert_max_num_queries(12):
        data = ad.analytics(30, now=NOW)

    assert data["period"]["days"] == 30
    assert [o["days"] for o in data["period"]["options"]] == [7, 30, 90]
    assert [o["label"] for o in data["period"]["options"]] == ["7 дней", "30 дней", "Квартал"]
    assert [c["id"] for c in data["charts"]] == [
        "flow",
        "stock",
        "fill",
        "calls",
        "transit",
        "top",
    ]
    for item in data["charts"]:
        assert {"id", "title", "type", "labels", "series", "empty", "table"} <= set(item)
        for series in item["series"]:
            assert len(series["data"]) == len(item["labels"])
    assert len(data["kpis"]) == 4


def test_analytics_on_empty_db():
    data = ad.analytics(90, now=NOW)
    assert all(c["empty"] for c in data["charts"])
    assert len(chart(data, "flow")["labels"]) == 90


def test_chart_json_drops_tables():
    data = ad.analytics(7, now=NOW)
    payload = ad.chart_json(data)
    assert [c["id"] for c in payload] == [c["id"] for c in data["charts"]]
    assert all("table" not in c for c in payload)


# ---------- page ----------


def test_page_requires_login():
    response = HttpClient().get("/panel/analytics/")
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/?next=")


@pytest.mark.parametrize("who", ["mgr", "wh"])
def test_page_open_to_both_groups(request, who):
    response = request.getfixturevalue(who).get("/panel/analytics/")
    assert response.status_code == 200
    assert "panel/analytics/index.html" in [t.name for t in response.templates]
    assert response.context["analytics"]["period"]["days"] == 30


def test_page_empty_db(mgr):
    response = mgr.get("/panel/analytics/?period=7")
    page = response.content.decode()

    assert response.context["analytics"]["period"]["days"] == 7
    assert page.count("Нет данных за период") == 6
    assert 'id="analytics-data"' in page
    assert "vendor/chart.umd.min.js" in page
    assert "js/analytics.js" in page
    assert "css/panel-analytics.css" in page
    for label in ("Принято мест", "Выдано мест", "Среднее хранение", "Дозвон"):
        assert label in page


def test_page_embeds_chart_json(mgr, ivan, pinned_now):
    arrived(ivan, [3], at=pinned_now)

    response = mgr.get("/panel/analytics/?period=30")
    page = response.content.decode()
    raw = page.split('id="analytics-data" type="application/json">')[1].split("</script>")[0]
    payload = json.loads(raw)

    assert [c["id"] for c in payload] == ["flow", "stock", "fill", "calls", "transit", "top"]
    assert payload[0]["series"][0]["data"][-1] == 3
    assert "table" not in payload[0]
    assert ivan.phone not in page
    assert "Показать таблицей" in page
    assert page.count("<table") == 6 - sum(1 for c in payload if c["empty"])


@pytest.mark.parametrize(("query", "days"), [("?period=90", 90), ("?period=5", 30), ("", 30)])
def test_page_period_filter(mgr, query, days):
    page = mgr.get(f"/panel/analytics/{query}").content.decode()
    active = [
        line for line in page.splitlines() if 'aria-current="page"' in line and "period=" in line
    ]
    assert len(active) == 1
    assert f"period={days}" in active[0]


def test_page_rejects_post(mgr):
    assert mgr.post("/panel/analytics/").status_code == 405


def test_cargo_status_untouched_by_page(mgr, ivan):
    cargo = services.add_cargo(ivan, "Z1")
    mgr.get("/panel/analytics/")
    cargo.refresh_from_db()
    assert cargo.status == Cargo.Status.RECEIVED


def test_storage_days_display_has_one_decimal(ivan, manager):
    _t, cur = arrived(ivan, [1], at=local(2026, 10, 12, 10, 0))
    pick(cur[0], manager, at=local(2026, 10, 14, 19, 36))  # 2.4 days

    storage = next(k for k in ad.kpis(ad.period(7, now=NOW)) if k["key"] == "storage")

    assert storage["value"] == 2.4
    assert storage["display"] == "2,4"


def test_storage_delta_display_has_one_decimal(ivan, manager):
    _t, prev = arrived(ivan, [1], at=local(2026, 10, 6, 10, 0))
    pick(prev[0], manager, at=local(2026, 10, 7, 10, 0))  # 1 day
    _t, cur = arrived(ivan, [1], at=local(2026, 10, 12, 10, 0))
    pick(cur[0], manager, at=local(2026, 10, 14, 19, 36))  # 2.4 days

    storage = next(k for k in ad.kpis(ad.period(7, now=NOW)) if k["key"] == "storage")

    assert storage["delta"] == 1.4
    assert storage["delta_display"] == "↑ 1,4 дн"

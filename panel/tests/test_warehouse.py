"""«Склад» (SPEC §14.6): tabs «Выдача · Ордер на звонок · На складе» on one page."""

import re
from datetime import timedelta

import pytest
from django.test import Client as HttpClient
from django.utils import timezone

from logistics import services
from logistics.models import Cargo, DailyOrderLine
from logistics.orders import build_daily_order
from panel.tests.helpers import page_without_csrf

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pinned_now")]

HX = {"HTTP_HX_REQUEST": "true"}
WAREHOUSE = "/panel/warehouse/"


def today():
    return timezone.localdate()


@pytest.fixture
def arrived(loaded_truck):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)
    return loaded_truck


def tabs(page: str) -> str:
    start = page.index('class="wh-tabs"')
    return page[start : page.index("</nav>", start)]


def current_tab(page: str) -> str:
    match = re.search(r'<a [^>]*data-tab="(\w+)"[^>]*aria-current="page"', tabs(page))
    assert match, tabs(page)
    return match.group(1)


def block(page: str, key: str) -> str:
    start = page.index(f'data-block="{key}"')
    return page[start : page.index("</section>", start)]


# ---------- tabs ----------


def test_requires_login():
    response = HttpClient().get(WAREHOUSE)
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/?next=")


@pytest.mark.parametrize("who", ["mgr", "wh"])
def test_both_groups_see_three_tabs_with_pickup_by_default(request, who):
    response = request.getfixturevalue(who).get(WAREHOUSE)
    assert response.status_code == 200
    page = response.content.decode()
    assert '<h1 class="page-title">Склад</h1>' in page
    bar = tabs(page)
    assert bar.index("Выдача") < bar.index("Ордер на звонок") < bar.index("На складе")
    assert current_tab(page) == "pickup"
    assert 'placeholder="Артикул, телефон или код клиента"' in page


@pytest.mark.parametrize("tab", ["pickup", "order", "stock"])
def test_tab_query_selects_tab(mgr, arrived, tab):
    page = mgr.get(f"{WAREHOUSE}?tab={tab}").content.decode()
    assert current_tab(page) == tab


def test_unknown_tab_falls_back_to_pickup(mgr):
    page = mgr.get(f"{WAREHOUSE}?tab=nope").content.decode()
    assert current_tab(page) == "pickup"


def test_last_tab_is_remembered_in_session(wh, arrived):
    wh.get(f"{WAREHOUSE}?tab=stock")
    assert current_tab(wh.get(WAREHOUSE).content.decode()) == "stock"
    wh.get(f"{WAREHOUSE}?tab=order")
    assert current_tab(wh.get(WAREHOUSE).content.decode()) == "order"


def test_sidebar_warehouse_item_is_active(mgr):
    page = mgr.get(WAREHOUSE).content.decode()
    link = re.search(r'<a [^>]*data-nav="warehouse"[^>]*>', page).group(0)
    assert 'aria-current="page"' in link


# ---------- old URLs ----------


def test_old_pickup_url_redirects_with_query(wh):
    response = wh.get("/panel/pickup/?q=A1")
    assert response.status_code == 302
    assert response["Location"] == f"{WAREHOUSE}?tab=pickup&q=A1"
    response = wh.get("/panel/pickup/?client=5")
    assert response["Location"] == f"{WAREHOUSE}?tab=pickup&client=5"
    assert wh.get("/panel/pickup/")["Location"] == f"{WAREHOUSE}?tab=pickup"


def test_old_order_urls_redirect_with_date_and_status(mgr):
    assert mgr.get("/panel/order/")["Location"] == f"{WAREHOUSE}?tab=order"
    response = mgr.get("/panel/order/2026-10-01/?status=pending")
    assert response.status_code == 302
    assert response["Location"] == f"{WAREHOUSE}?tab=order&date=2026-10-01&status=pending"
    response = mgr.get("/panel/order/?date=2026-10-01")
    assert response["Location"] == f"{WAREHOUSE}?tab=order&date=2026-10-01"


def test_pickup_htmx_endpoint_still_returns_fragment(wh, arrived):
    response = wh.get("/panel/pickup/?q=A1", **HX)
    assert response.status_code == 200
    assert "<html" not in response.content.decode()
    assert response["HX-Replace-Url"] == f"{WAREHOUSE}?tab=pickup&q=A1"


# ---------- order tab ----------


def test_order_tab_shows_day_and_date_navigation(mgr, arrived, manager):
    build_daily_order(today(), manager)
    page = mgr.get(f"{WAREHOUSE}?tab=order").content.decode()
    assert "Обновить ордер" in page and "B1" in page
    nxt = today() + timedelta(days=1)
    assert f'href="{WAREHOUSE}?tab=order&amp;date={nxt:%Y-%m-%d}"' in page
    # The date picker submits to the warehouse page and stays on the order tab.
    assert f'action="{WAREHOUSE}"' in page and 'name="tab" value="order"' in page


def test_order_tab_for_another_day(mgr):
    page = mgr.get(f"{WAREHOUSE}?tab=order&date=2026-10-01").content.decode()
    assert "Ордера за 1 октября" in page or "Ордер на 1 октября" in page


def test_order_tab_bad_date_shows_today(mgr):
    response = mgr.get(f"{WAREHOUSE}?tab=order&date=2026-13-45", follow=True)
    page = response.content.decode()
    assert "Неверная дата" in page
    assert current_tab(page) == "order"


def test_order_status_filter_links_stay_in_tab(mgr, arrived, manager):
    build_daily_order(today(), manager)
    page = mgr.get(f"{WAREHOUSE}?tab=order").content.decode()
    day = f"{today():%Y-%m-%d}"
    assert f'href="{WAREHOUSE}?tab=order&amp;date={day}&amp;status=pending"' in page
    page = mgr.get(f"{WAREHOUSE}?tab=order&status=reached").content.decode()
    assert 'id="line-' not in page  # nobody reached yet


def test_order_actions_from_tab_return_to_tab(wh, arrived):
    day = f"{today():%Y-%m-%d}"
    response = wh.post(f"/panel/order/{day}/build/")
    assert response["Location"] == f"{WAREHOUSE}?tab=order&date={day}"
    line = DailyOrderLine.objects.get(article="B1")
    response = wh.post(f"/panel/order/{day}/line/{line.pk}/mark/", {"status": "reached"})
    assert response["Location"] == f"{WAREHOUSE}?tab=order&date={day}"
    line.refresh_from_db()
    assert line.call_status == DailyOrderLine.CallStatus.REACHED


def test_order_htmx_mark_still_works(mgr, arrived, manager):
    build_daily_order(today(), manager)
    line = DailyOrderLine.objects.get(article="B1")
    day = f"{today():%Y-%m-%d}"
    page = mgr.post(f"/panel/order/{day}/line/{line.pk}/mark/", {"status": "no_answer"}, **HX)
    page = page.content.decode()
    assert 'id="order-counters"' in page
    assert f"{WAREHOUSE}?tab=order&amp;date={day}&amp;status=no_answer" in page


def test_manager_only_order_controls_unchanged(wh, mgr, arrived, manager):
    build_daily_order(today(), manager)
    assert "Добавить груз" in mgr.get(f"{WAREHOUSE}?tab=order").content.decode()
    page = wh.get(f"{WAREHOUSE}?tab=order").content.decode()
    assert "Добавить груз" not in page and "Убрать" not in page
    line = DailyOrderLine.objects.get(article="B1")
    day = f"{today():%Y-%m-%d}"
    assert wh.post(f"/panel/order/{day}/line/{line.pk}/remove/", **HX).status_code == 403


# ---------- pickup tab ----------


def test_pickup_tab_with_query_shows_client(wh, arrived, ivan):
    page = page_without_csrf(wh.get(f"{WAREHOUSE}?tab=pickup&q=A1"))
    assert ivan.code in page and "Выдать (2)" in page
    assert 'name="q" value="A1"' in page


def test_pickup_issue_returns_to_pickup_tab(wh, arrived, ivan):
    a1 = Cargo.objects.get(article="A1")
    response = wh.post("/panel/pickup/issue/", {"client": ivan.pk, "cargo": [a1.pk]})
    assert response["Location"] == f"{WAREHOUSE}?tab=pickup"
    response = wh.post("/panel/pickup/issue/", {"client": ivan.pk})
    assert response["Location"] == f"{WAREHOUSE}?tab=pickup&client={ivan.pk}"


# ---------- stock tab ----------


def set_days(article: str, days: int, **extra):
    Cargo.objects.filter(article=article).update(
        ready_at=timezone.now() - timedelta(days=days), **extra
    )


def test_stock_blocks_by_age_with_counts(wh, arrived, ivan, petr):
    set_days("A1", 0)  # A, 2 places
    set_days("A2", 3)  # B, 1 place
    set_days("B1", 9)  # D, 3 places
    services.add_cargo(petr, "B2", places=4)  # not on the warehouse yet
    page = wh.get(f"{WAREHOUSE}?tab=stock").content.decode()
    a, b, c, d = (block(page, key) for key in "ABCD")
    assert "Блок A" in a and "2 места" in a and "1 груз" in a and "A1" in a
    assert "Блок B · 3–4 дня" in b and "1 место" in b and "A2" in b
    assert "Блок C" in c and "0 мест" in c and "0 грузов" in c
    assert "Блок D" in d and "3 места" in d and "B1" in d
    assert "B2" not in page_without_csrf(wh.get(f"{WAREHOUSE}?tab=stock"))
    for key in "ABCD":
        assert f"--age-{key.lower()}" in page or f"wh-block--{key.lower()}" in page


def test_stock_row_shows_client_days_and_storage(wh, arrived, ivan):
    set_days("A1", 3, free_storage_until=today() - timedelta(days=1))
    set_days("A2", 4, free_storage_until=today() + timedelta(days=5))
    b = block(wh.get(f"{WAREHOUSE}?tab=stock").content.decode(), "B")
    assert ivan.name in b and ivan.code in b
    a1 = b[b.index('data-article="a1"') :]
    a1 = a1[: a1.index("</li>")]
    assert "3 дн" in a1 and "is-overdue" in a1
    a2 = b[b.index('data-article="a2"') :]
    a2 = a2[: a2.index("</li>")]
    assert "is-overdue" not in a2


def test_stock_sorted_oldest_first(wh, arrived):
    set_days("A1", 3)
    set_days("A2", 4)
    b = block(wh.get(f"{WAREHOUSE}?tab=stock").content.decode(), "B")
    assert b.index('data-article="a2"') < b.index('data-article="a1"')


def test_stock_rows_carry_filter_data_and_pickup_link(wh, arrived, ivan):
    page = wh.get(f"{WAREHOUSE}?tab=stock").content.decode()
    assert "data-stock-filter" in page
    row = page[page.index('data-article="a1"') :]
    row = row[: row.index("</li>")]
    assert f'data-code="{ivan.code.lower()}"' in row
    assert 'data-phone="79141234567"' in row
    assert f'href="{WAREHOUSE}?tab=pickup&amp;q=A1"' in row
    assert "Выдать" in row


def test_pickup_prefilled_from_stock_row(wh, arrived, ivan):
    page = page_without_csrf(wh.get(f"{WAREHOUSE}?tab=pickup&q=A1"))
    assert 'name="q" value="A1"' in page
    assert f'value="{Cargo.objects.get(article="A1").pk}" checked' in page


def test_stock_empty_state(mgr):
    page = mgr.get(f"{WAREHOUSE}?tab=stock").content.decode()
    assert "На складе нет готовых грузов" in page


# ---------- ops KPI link ----------


def test_ops_order_kpi_links_to_order_tab(mgr):
    page = mgr.get("/panel/").content.decode()
    assert f'href="{WAREHOUSE}?tab=order"' in page
    assert 'href="/panel/order/"' not in page

from datetime import timedelta

import pytest
from django.utils import timezone

from logistics import services
from logistics.models import Cargo, Truck
from panel.tests.helpers import page_without_csrf

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def arrived(loaded_truck):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)
    return loaded_truck


def cargo(article):
    return Cargo.objects.get(article=article)


def test_page_has_big_autofocus_search(wh):
    page = wh.get("/panel/pickup/").content.decode()
    assert 'placeholder="Артикул, телефон или код клиента"' in page
    assert "autofocus" in page
    assert 'hx-trigger="input changed delay:300ms' in page


def test_search_by_article_shows_client_and_ready_cargo(wh, arrived, ivan):
    page = page_without_csrf(wh.get("/panel/pickup/?q=a1", **HX))
    assert ivan.name in page and ivan.code in page
    # All of the client's ready cargo, checked by default.
    assert f'value="{cargo("A1").pk}" checked' in page
    assert f'value="{cargo("A2").pk}" checked' in page
    assert "B1" not in page
    assert "Выдать (2)" in page
    assert "<html" not in page  # HTMX gets the results fragment only


def test_search_by_phone_and_code(wh, arrived, ivan, petr):
    page = wh.get("/panel/pickup/?q=8 914 123-45-68").content.decode()
    assert petr.code in page and "B1" in page
    page = wh.get(f"/panel/pickup/?q={ivan.code.lower()}", **HX).content.decode()
    assert "A1" in page and "A2" in page


def test_nothing_found(wh, arrived):
    page = wh.get("/panel/pickup/?q=ZZZ999", **HX).content.decode()
    assert "Ничего не нашли" in page


def test_article_of_several_clients_lists_clients(wh, arrived, ivan, petr):
    services.add_cargo(petr, "A1", places=1)
    page = wh.get("/panel/pickup/?q=A1", **HX).content.decode()
    assert "Выдать (" not in page
    assert f"?client={ivan.pk}" in page and f"?client={petr.pk}" in page
    page = wh.get(f"/panel/pickup/?client={petr.pk}", **HX).content.decode()
    assert "B1" in page


def test_not_ready_cargo_is_shown_without_checkbox(wh, arrived, ivan):
    other = services.create_truck()
    pending = services.add_cargo(ivan, "C9", truck=other, places=1)
    page = wh.get("/panel/pickup/?q=C9", **HX).content.decode()
    assert "C9" in page
    assert f'value="{pending.pk}"' not in page
    assert "ещё не" in page  # explanation of why it cannot be handed out


def test_storage_overdue_is_marked(wh, arrived):
    Cargo.objects.filter(article="A1").update(
        free_storage_until=timezone.localdate() - timedelta(days=1)
    )
    page = wh.get("/panel/pickup/?q=A1", **HX).content.decode()
    assert "is-overdue" in page


def test_pickup_selected_cargo(wh, arrived, keeper, ivan):
    a1, a2 = cargo("A1"), cargo("A2")
    response = wh.post(
        "/panel/pickup/issue/",
        {"client": ivan.pk, "cargo": [a1.pk], "paid": "on"},
        follow=True,
    )
    page = response.content.decode()
    a1.refresh_from_db()
    a2.refresh_from_db()
    assert a1.status == Cargo.Status.PICKED_UP and a1.paid and a1.picked_up_by == keeper
    assert a2.status == Cargo.Status.READY
    assert "Выдано 1 груз" in page
    assert 'name="q" value=""' in page  # a clear screen for the next client


def test_pickup_everything_closes_truck(mgr, arrived, ivan, petr):
    mgr.post("/panel/pickup/issue/", {"client": ivan.pk, "cargo": [cargo("A1").pk, cargo("A2").pk]})
    arrived.refresh_from_db()
    assert arrived.status == Truck.Status.ARRIVED
    page = mgr.post(
        "/panel/pickup/issue/", {"client": petr.pk, "cargo": [cargo("B1").pk]}, follow=True
    )
    assert "Выдано 1 груз" in page.content.decode()
    arrived.refresh_from_db()
    assert arrived.status == Truck.Status.CLOSED
    assert not cargo("B1").paid


def test_pickup_is_all_or_nothing(wh, arrived, ivan, manager):
    a1, a2 = cargo("A1"), cargo("A2")
    services.cargo_pickup(a2, manager, paid=False)
    page = wh.post(
        "/panel/pickup/issue/", {"client": ivan.pk, "cargo": [a1.pk, a2.pk]}, follow=True
    ).content.decode()
    assert "нельзя выдать" in page
    a1.refresh_from_db()
    assert a1.status == Cargo.Status.READY


def test_pickup_requires_selection(wh, arrived, ivan):
    page = wh.post("/panel/pickup/issue/", {"client": ivan.pk}, follow=True).content.decode()
    assert "Отметьте грузы" in page


def test_pickup_rejects_other_clients_cargo(wh, arrived, ivan):
    page = wh.post(
        "/panel/pickup/issue/", {"client": ivan.pk, "cargo": [cargo("B1").pk]}, follow=True
    ).content.decode()
    assert cargo("B1").status == Cargo.Status.READY
    assert "toast st-error" in page


def test_totals_in_footer(wh, arrived):
    page = wh.get("/panel/pickup/?q=A1", **HX).content.decode()
    # A1: 2 places, 100 ₽; A2: 1 place, 200 ₽
    assert "data-total-places>3<" in page
    assert "300" in page.split("data-total-amount")[1][:40]

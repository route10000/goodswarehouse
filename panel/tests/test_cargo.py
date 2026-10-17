from decimal import Decimal

import pytest

from logistics import services
from logistics.models import Cargo, Client

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def add(http, truck, **data):
    payload = {"phone": "", "name": "", "article": "", "places": "1", "amount_due": "0"}
    payload.update(data)
    return http.post(f"/panel/trucks/{truck.pk}/cargo/add/", payload, **HX)


# ---------- add cargo into a truck (HTMX) ----------


def test_add_cargo_for_existing_client(mgr, truck, ivan):
    response = add(
        mgr,
        truck,
        phone="8 914 123 45 67",
        name="Не меняем",
        article="X-1",
        description="Куртки",
        places="2",
        weight_kg="12,5",
        volume_m3="0,3",
        amount_due="1 500",
    )

    assert response.status_code == 200
    cargo = Cargo.objects.get(article="X-1")
    assert cargo.client == ivan
    assert cargo.truck == truck
    assert cargo.status == Cargo.Status.LOADED
    assert cargo.weight_kg == Decimal("12.5")
    assert cargo.volume_m3 == Decimal("0.3")
    assert cargo.amount_due == Decimal("1500")
    ivan.refresh_from_db()
    assert ivan.name == "Иван"
    page = response.content.decode()
    assert 'hx-swap-oob="beforeend:#cargo-rows"' in page
    assert "X-1" in page


def test_add_cargo_creates_new_client(mgr, truck):
    response = add(mgr, truck, phone="+7 914 999 88 77", name="Ольга", article="N-1")

    assert response.status_code == 200
    client = Client.objects.get(phone="+79149998877")
    assert client.name == "Ольга"
    assert Cargo.objects.get(article="N-1").client == client


def test_add_cargo_invalid_phone(mgr, truck):
    response = add(mgr, truck, phone="12345", article="N-1")

    assert response.status_code == 200
    assert Cargo.objects.count() == 0
    assert Client.objects.count() == 0
    assert "Неверный номер телефона" in response.content.decode()


def test_add_cargo_requires_article(mgr, truck, ivan):
    response = add(mgr, truck, phone=ivan.phone, article="  ")

    assert Cargo.objects.count() == 0
    assert "Обязательное поле" in response.content.decode()


def test_add_cargo_rejects_places_below_one(mgr, truck, ivan):
    add(mgr, truck, phone=ivan.phone, article="Z", places="0")

    assert Cargo.objects.count() == 0


def test_add_cargo_rejects_non_loading_truck(mgr, truck, ivan):
    services.truck_depart(truck)

    response = add(mgr, truck, phone=ivan.phone, article="LATE")

    assert Cargo.objects.count() == 0
    assert "уже не грузится" in response.content.decode()


# ---------- client lookup ----------


def test_lookup_finds_client(mgr, ivan):
    response = mgr.get("/panel/clients/lookup/", {"phone": "89141234567"}, **HX)

    page = response.content.decode()
    assert "Найден: Иван" in page
    assert ivan.code in page


def test_lookup_unknown_phone_offers_name_field(mgr):
    page = mgr.get("/panel/clients/lookup/", {"phone": "89140000000"}, **HX).content.decode()

    assert 'name="name"' in page
    assert "Новый клиент" in page


def test_lookup_incomplete_phone(mgr):
    page = mgr.get("/panel/clients/lookup/", {"phone": "8914"}, **HX).content.decode()

    assert "Найден" not in page


# ---------- /panel/cargo/new/ ----------


def test_new_cargo_without_truck(mgr, ivan):
    response = mgr.post(
        "/panel/cargo/new/",
        {"phone": ivan.phone, "article": "FREE-1", "places": "1", "amount_due": "0"},
    )

    assert response.status_code == 302
    cargo = Cargo.objects.get(article="FREE-1")
    assert cargo.truck is None
    assert cargo.status == Cargo.Status.RECEIVED


def test_new_cargo_into_loading_truck(mgr, truck, ivan):
    mgr.post(
        "/panel/cargo/new/",
        {
            "phone": ivan.phone,
            "article": "IN-1",
            "places": "1",
            "amount_due": "0",
            "truck": truck.pk,
        },
    )

    assert Cargo.objects.get(article="IN-1").truck == truck


def test_new_cargo_truck_choices_are_loading_only(mgr, truck):
    gone = services.create_truck()
    services.truck_depart(gone)

    response = mgr.get("/panel/cargo/new/")

    choices = list(response.context["form"].fields["truck"].queryset)
    assert choices == [truck]


# ---------- /panel/cargo/ list ----------


@pytest.fixture
def mixed(loaded_truck, ivan, petr):
    other = services.create_truck()
    services.add_cargo(petr, "C-9", truck=other)
    services.add_cargo(petr, "LOOSE")
    services.truck_depart(loaded_truck)
    return loaded_truck, other


def articles(response):
    return sorted(c.article for c in response.context["page"].object_list)


def test_cargo_list_all(mgr, mixed):
    assert articles(mgr.get("/panel/cargo/")) == ["A1", "A2", "B1", "C-9", "LOOSE"]


def test_cargo_list_filter_status(mgr, mixed):
    assert articles(mgr.get("/panel/cargo/", {"status": "in_transit"})) == ["A1", "A2", "B1"]
    assert articles(mgr.get("/panel/cargo/", {"status": "received"})) == ["LOOSE"]


def test_cargo_list_filter_truck(mgr, mixed):
    _, other = mixed
    assert articles(mgr.get("/panel/cargo/", {"truck": other.pk})) == ["C-9"]


def test_cargo_list_search_article_phone_code(mgr, mixed, ivan, petr):
    assert articles(mgr.get("/panel/cargo/", {"q": "c-9"})) == ["C-9"]
    assert articles(mgr.get("/panel/cargo/", {"q": "8 914 123-45-67"})) == ["A1", "A2"]
    assert articles(mgr.get("/panel/cargo/", {"q": "4568"})) == ["B1", "C-9", "LOOSE"]
    assert articles(mgr.get("/panel/cargo/", {"q": petr.code})) == ["B1", "C-9", "LOOSE"]


def test_cargo_list_combined_filters(mgr, mixed):
    loaded_truck, _ = mixed
    response = mgr.get(
        "/panel/cargo/", {"status": "in_transit", "truck": loaded_truck.pk, "q": "a"}
    )
    assert articles(response) == ["A1", "A2"]


def test_cargo_list_paginates_by_50(mgr, ivan):
    for n in range(55):
        services.add_cargo(ivan, f"P{n:02d}")

    first = mgr.get("/panel/cargo/")
    second = mgr.get("/panel/cargo/", {"page": 2})

    assert len(first.context["page"].object_list) == 50
    assert len(second.context["page"].object_list) == 5


def test_add_cargo_refreshes_dialog_counts(mgr, truck, ivan):
    response = add(mgr, truck, phone="+7 914 999 88 77", name="Ольга", article="N-1")

    page = response.content.decode()
    assert 'id="truck-action" class="truck-action" hx-swap-oob="true"' in page
    assert "0 клиентов получат сообщение в Telegram, 1 груз попадёт в ордер на звонок" in page

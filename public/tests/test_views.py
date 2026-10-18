from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client as HttpClient
from django.utils import timezone

from logistics import services
from logistics.models import Truck

pytestmark = pytest.mark.django_db

NOT_FOUND = "Не нашли груз. Проверьте код или артикул и цифры телефона."


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _site_settings(settings):
    settings.WAREHOUSE_ADDRESS = "ул. Складская, 7"
    settings.WAREHOUSE_HOURS = "Пн–Сб 9:00–18:00"
    settings.WAREHOUSE_PHONE = "+7 (423) 200-00-00"
    settings.WAREHOUSE_CITY = "Владивосток"
    settings.TELEGRAM_BOT_USERNAME = "gw_test_bot"


@pytest.fixture
def staff():
    return get_user_model().objects.create_user("sklad", password="x")


@pytest.fixture
def ivan():
    client, _ = services.get_or_create_client("+79141234567", "Иван Петров")
    return client


def make_loading(**kwargs):
    return services.create_truck(**kwargs)


def make_in_transit(departed_ago, eta_in, **kwargs):
    now = timezone.now()
    truck = services.create_truck(eta=now + eta_in, **kwargs)
    services.truck_depart(truck, now=now - departed_ago)
    return truck


def make_arrived(arrived_ago, **kwargs):
    now = timezone.now()
    truck = services.create_truck(eta=now - arrived_ago, **kwargs)
    services.truck_depart(truck, now=now - arrived_ago - timedelta(hours=3))
    services.truck_arrive(truck, now=now - arrived_ago)
    return truck


# ---------- home ----------


def test_home_renders_hero_chapters_and_contacts(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Склад, машины и клиенты — в одном окне." in html
    assert "Где мой груз" in html
    assert "01 / 04 · КЛИЕНТУ" in html
    assert "Как это работает" not in html  # replaced by the four chapters
    assert "ул. Складская, 7" in html
    assert "https://t.me/gw_test_bot" in html
    assert 'action="/track/"' in html


def test_home_hides_bot_button_without_username(client, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    html = client.get("/").content.decode()
    assert "Подключить Telegram" not in html
    assert "t.me/" not in html


def test_home_lists_nearest_trucks_up_to_five(client):
    trucks = [make_loading() for _ in range(6)]
    hidden = make_loading(is_public=False)
    response = client.get("/")
    rows = response.context["trucks"]
    assert len(rows) == 5
    html = response.content.decode()
    assert trucks[0].number in html
    assert hidden.number not in html
    assert "Ближайшие машины" in html


# ---------- schedule ----------


def test_schedule_filters_orders_and_computes_progress(client):
    now = timezone.now()
    loading_late = make_loading(departure_date=(now + timedelta(days=3)).date())
    loading_soon = make_loading(departure_date=(now + timedelta(days=1)).date())
    transit = make_in_transit(departed_ago=timedelta(hours=1), eta_in=timedelta(hours=3))
    overdue = make_in_transit(departed_ago=timedelta(hours=5), eta_in=-timedelta(hours=1))
    arrived_recent = make_arrived(arrived_ago=timedelta(days=2))
    arrived_old = make_arrived(arrived_ago=timedelta(days=4))
    hidden = make_loading(is_public=False)
    closed = make_arrived(arrived_ago=timedelta(hours=1))
    Truck.objects.filter(pk=closed.pk).update(status=Truck.Status.CLOSED)

    response = client.get("/schedule/")
    assert response.status_code == 200
    rows = response.context["trucks"]
    numbers = [row.truck.number for row in rows]
    assert numbers == [
        loading_soon.number,
        loading_late.number,
        overdue.number,
        transit.number,
        arrived_recent.number,
    ]
    progress = {row.truck.number: row.percent for row in rows}
    assert progress[loading_soon.number] == 8
    assert progress[transit.number] == 25
    assert progress[overdue.number] == 100
    assert progress[arrived_recent.number] == 100

    html = response.content.decode()
    for truck in (arrived_old, hidden, closed):
        assert truck.number not in html


def test_schedule_shows_no_client_data(client, ivan):
    truck = make_loading()
    services.add_cargo(ivan, "SECRET-ART", truck=truck, amount_due=Decimal("500"))
    html = client.get("/schedule/").content.decode()
    assert truck.number in html
    assert "SECRET-ART" not in html
    assert "Иван" not in html
    assert ivan.code not in html


def test_schedule_empty_state(client):
    html = client.get("/schedule/").content.decode()
    assert "Сейчас машин в пути нет. Загляните позже или подпишитесь на бота." in html


# ---------- track ----------


@pytest.fixture
def ivan_cargo(ivan, staff):
    truck = services.create_truck(eta=timezone.now() + timedelta(hours=4))
    in_transit = services.add_cargo(
        ivan,
        "ART-100",
        description="Кроссовки",
        places=3,
        weight_kg=Decimal("12.5"),
        amount_due=Decimal("8400"),
        truck=truck,
    )
    services.truck_depart(truck)
    arrived_truck = services.create_truck()
    ready = services.add_cargo(ivan, "ART-200", description="Чайник", truck=arrived_truck)
    old = services.add_cargo(ivan, "ART-300", description="Старый", truck=arrived_truck)
    recent = services.add_cargo(ivan, "ART-400", description="Недавний", truck=arrived_truck)
    services.truck_depart(arrived_truck)
    services.truck_arrive(arrived_truck)
    services.cargo_pickup(old, staff, paid=True, now=timezone.now() - timedelta(days=15))
    services.cargo_pickup(recent, staff, paid=True, now=timezone.now() - timedelta(days=2))
    return {"in_transit": in_transit, "ready": ready, "old": old, "recent": recent}


def test_track_get_shows_form(client):
    response = client.get("/track/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Код клиента или артикул" in html
    assert "Последние 4 цифры телефона" in html
    assert "csrfmiddlewaretoken" in html


def test_track_by_code(client, ivan, ivan_cargo):
    response = client.post("/track/", {"query": ivan.code, "last4": "4567"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "Иван" in html
    assert "Петров" not in html
    assert "ART-100" in html
    assert "ART-200" in html
    assert "ART-400" in html
    assert "ART-300" not in html
    assert "Кроссовки" in html
    assert "8 400 ₽" in html
    assert "бесплатное хранение до" in html
    assert NOT_FOUND not in html


def test_track_lists_ready_first_and_picked_up_last(client, ivan, ivan_cargo):
    response = client.post("/track/", {"query": ivan.code, "last4": "4567"})
    articles = [row.cargo.article for row in response.context["cargos"]]
    assert articles == ["ART-200", "ART-100", "ART-400"]


def test_track_code_without_dash_and_lowercase(client, ivan, ivan_cargo):
    query = ivan.code.lower().replace("-", "")
    response = client.post("/track/", {"query": query, "last4": "4567"})
    assert "ART-100" in response.content.decode()


def test_track_by_article(client, ivan, ivan_cargo):
    response = client.post("/track/", {"query": "art-200", "last4": "4567"})
    html = response.content.decode()
    assert "ART-100" in html
    assert "ART-200" in html


def test_track_wrong_digits(client, ivan, ivan_cargo):
    for query in (ivan.code, "ART-100"):
        response = client.post("/track/", {"query": query, "last4": "0000"})
        html = response.content.decode()
        assert response.status_code == 200
        assert NOT_FOUND in html
        assert "Кроссовки" not in html
        assert "cargo-card" not in html


def test_track_unknown_code(client, ivan, ivan_cargo):
    response = client.post("/track/", {"query": "GW-9999", "last4": "4567"})
    assert NOT_FOUND in response.content.decode()


def test_track_invalid_input_gives_same_error(client):
    response = client.post("/track/", {"query": "", "last4": "12"})
    assert NOT_FOUND in response.content.decode()


def test_track_rate_limit(client, ivan, ivan_cargo):
    for _ in range(10):
        response = client.post("/track/", {"query": "GW-9999", "last4": "4567"})
        assert response.status_code == 200
    response = client.post("/track/", {"query": ivan.code, "last4": "4567"})
    assert response.status_code == 429
    html = response.content.decode()
    assert "Слишком много попыток. Подождите минуту." in html
    assert "cargo-card" not in html


def test_track_rate_limit_is_per_ip(client, ivan, ivan_cargo):
    for _ in range(10):
        client.post("/track/", {"query": "x", "last4": "0000"}, REMOTE_ADDR="10.0.0.1")
    response = client.post("/track/", {"query": ivan.code, "last4": "4567"}, REMOTE_ADDR="10.0.0.2")
    assert response.status_code == 200


def test_track_requires_csrf(ivan):
    strict = HttpClient(enforce_csrf_checks=True)
    response = strict.post("/track/", {"query": ivan.code, "last4": "4567"})
    assert response.status_code == 403


# ---------- contacts ----------


def test_contacts_renders_settings(client):
    response = client.get("/contacts/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "ул. Складская, 7" in html
    assert "Пн–Сб 9:00–18:00" in html
    assert "+7 (423) 200-00-00" in html
    assert 'href="tel:+74232000000"' in html
    assert "yandex.ru/maps/?text=" in html
    assert "https://t.me/gw_test_bot" in html

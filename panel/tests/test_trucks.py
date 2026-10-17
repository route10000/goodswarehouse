from datetime import date

import pytest

from logistics import services
from logistics.models import DEFAULT_ROUTE, Cargo, Truck
from notify.models import Notification

pytestmark = pytest.mark.django_db


def messages_of(response):
    return [str(m) for m in response.context["messages"]]


# ---------- list ----------


def test_list_shows_trucks_with_counts(mgr, loaded_truck):
    response = mgr.get("/panel/trucks/")

    assert response.status_code == 200
    row = next(t for t in response.context["trucks"] if t.pk == loaded_truck.pk)
    assert row.places_total == 6
    assert row.clients_total == 2
    page = response.content.decode()
    assert loaded_truck.number in page
    assert "Грузится" in page


def test_list_filters_by_status(mgr, truck):
    other = services.create_truck()
    services.truck_depart(other)

    response = mgr.get("/panel/trucks/", {"status": "in_transit"})

    assert [t.pk for t in response.context["trucks"]] == [other.pk]
    assert response.context["status"] == "in_transit"


def test_list_ignores_unknown_status(mgr, truck):
    response = mgr.get("/panel/trucks/", {"status": "bogus"})

    assert [t.pk for t in response.context["trucks"]] == [truck.pk]


# ---------- create / edit ----------


def test_create_form_has_default_route(mgr):
    response = mgr.get("/panel/trucks/new/")

    assert response.status_code == 200
    assert DEFAULT_ROUTE in response.content.decode()


def test_create_truck(mgr):
    response = mgr.post(
        "/panel/trucks/new/",
        {
            "route": DEFAULT_ROUTE,
            "plate": "В777ОР125",
            "driver_phone": "8 914 000 11 22",
            "loading_starts": "2026-10-17T09:00",
            "departure_date": "2026-10-18",
            "eta": "2026-10-19T15:40",
            "is_public": "on",
            "comment": "Тент",
        },
    )

    truck = Truck.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/panel/trucks/{truck.pk}/"
    assert truck.status == Truck.Status.LOADING
    assert truck.plate == "В777ОР125"
    assert truck.driver_phone == "+79140001122"
    assert truck.departure_date == date(2026, 10, 18)
    assert truck.is_public is True
    assert truck.number.startswith("R-")


def test_create_truck_invalid_driver_phone(mgr):
    response = mgr.post("/panel/trucks/new/", {"route": DEFAULT_ROUTE, "driver_phone": "123"})

    assert response.status_code == 200
    assert Truck.objects.count() == 0
    assert "Неверный номер телефона" in response.content.decode()


def test_edit_truck_changes_only_plain_fields(mgr, truck):
    services.truck_depart(truck)

    response = mgr.post(
        f"/panel/trucks/{truck.pk}/edit/",
        {"route": "Уссурийск → Находка", "plate": "Е001КХ125", "comment": "x", "status": "closed"},
    )

    assert response.status_code == 302
    truck.refresh_from_db()
    assert truck.route == "Уссурийск → Находка"
    assert truck.plate == "Е001КХ125"
    assert truck.is_public is False
    assert truck.status == Truck.Status.IN_TRANSIT


# ---------- card ----------


def test_card_shows_cargo_totals_and_channels(mgr, loaded_truck):
    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    assert response.status_code == 200
    totals = response.context["totals"]
    assert totals["places"] == 6
    assert str(totals["weight"]) == "12.50"
    assert str(totals["amount"]) == "350.00"
    page = response.content.decode()
    assert "Telegram" in page and "Звонок" in page
    assert [s["state"] for s in response.context["steps"]] == [
        "current",
        "future",
        "future",
        "future",
    ]


def test_card_dialog_counts(mgr, loaded_truck):
    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    action = response.context["action"]
    assert action["telegram_clients"] == 1
    assert action["call_cargo"] == 1
    assert action["label"] == "Отправить машину"
    assert (
        "1 клиент получит сообщение в Telegram, 1 груз попадёт в ордер на звонок"
        in response.content.decode()
    )


def test_dialog_counts_pluralize(mgr, truck):
    for n in range(5):
        client, _ = services.get_or_create_client(f"+7914555{n:04d}", "")
        client.telegram_chat_id = 500 + n
        client.save()
        services.add_cargo(client, f"T{n}", truck=truck)
    silent, _ = services.get_or_create_client("+79146660000", "")
    services.add_cargo(silent, "S1", truck=truck)
    services.add_cargo(silent, "S2", truck=truck)

    page = mgr.get(f"/panel/trucks/{truck.pk}/").content.decode()

    assert "5 клиентов получат сообщение в Telegram, 2 груза попадут в ордер на звонок" in page


def test_card_stepper_after_arrival(mgr, loaded_truck):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)

    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    assert [s["state"] for s in response.context["steps"]] == ["done", "done", "current", "future"]
    assert response.context["action"]["label"] == "Закрыть рейс"


def test_closed_truck_has_no_action(mgr, loaded_truck):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)
    services.truck_close(loaded_truck)

    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    assert response.context["action"] is None
    assert [s["state"] for s in response.context["steps"]] == ["done", "done", "done", "current"]


def test_card_404(mgr):
    assert mgr.get("/panel/trucks/999/").status_code == 404


# ---------- status actions ----------


def test_depart_action(mgr, loaded_truck):
    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/status/depart/", follow=True)

    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.IN_TRANSIT
    assert response.redirect_chain[-1][0] == f"/panel/trucks/{loaded_truck.pk}/"
    assert any("отправлена" in m for m in messages_of(response))
    assert Notification.objects.filter(kind="departed").count() == 2
    assert 'class="toast' in response.content.decode()


def test_arrive_action(mgr, loaded_truck):
    services.truck_depart(loaded_truck)

    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/status/arrive/", follow=True)

    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.ARRIVED
    assert set(loaded_truck.cargos.values_list("status", flat=True)) == {Cargo.Status.READY}
    assert any("прибыла" in m for m in messages_of(response))


def test_close_action(mgr, loaded_truck):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)

    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/status/close/", follow=True)

    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.CLOSED
    assert set(loaded_truck.cargos.values_list("status", flat=True)) == {Cargo.Status.READY}
    assert any("закрыт" in m for m in messages_of(response))


def test_invalid_transition_shows_error_and_changes_nothing(mgr, loaded_truck):
    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/status/arrive/", follow=True)

    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.LOADING
    assert Notification.objects.count() == 0
    errors = [m for m in response.context["messages"] if m.level_tag == "error"]
    assert errors
    assert "toast st-error" in response.content.decode()


def test_status_action_requires_post(mgr, loaded_truck):
    assert mgr.get(f"/panel/trucks/{loaded_truck.pk}/status/depart/").status_code == 405


def test_unknown_status_action_404(mgr, loaded_truck):
    assert mgr.post(f"/panel/trucks/{loaded_truck.pk}/status/fly/").status_code == 404

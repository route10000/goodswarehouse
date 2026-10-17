from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from logistics import services
from logistics.models import Cargo, Client, Truck
from logistics.phones import InvalidPhone
from logistics.services import InvalidTransition
from notify.models import Notification

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")
T_DEPART = datetime(2026, 10, 17, 9, 0, tzinfo=VLAT)
T_ARRIVE = datetime(2026, 10, 17, 15, 40, tzinfo=VLAT)
T_PICKUP = datetime(2026, 10, 18, 11, 0, tzinfo=VLAT)


@pytest.fixture
def staff():
    return get_user_model().objects.create_user("sklad", password="x")


@pytest.fixture
def ivan():
    client, _ = services.get_or_create_client("+79141234567", "Иван")
    client.telegram_chat_id = 111
    client.save()
    return client


@pytest.fixture
def petr():
    client, _ = services.get_or_create_client("8 914 123 45 68", "Пётр")
    return client


@pytest.fixture
def truck():
    return services.create_truck(plate="А123ВС125")


@pytest.fixture
def loaded_truck(truck, ivan, petr):
    services.add_cargo(ivan, "A1", truck=truck, amount_due=Decimal("100"))
    services.add_cargo(ivan, "A2", truck=truck, amount_due=Decimal("200"))
    services.add_cargo(petr, "B1", truck=truck)
    return truck


def snapshot(truck):
    """DB state of a truck, its cargo and the notification count (reloads a copy)."""
    fresh = Truck.objects.get(pk=truck.pk)
    return (
        fresh.status,
        fresh.departed_at,
        fresh.arrived_at,
        sorted(fresh.cargos.values_list("id", "status", "ready_at", "picked_up_at")),
        Notification.objects.count(),
    )


# --- clients ---------------------------------------------------------------


def test_get_or_create_client_normalizes_and_reuses():
    a, created_a = services.get_or_create_client("8 914 123 45 67", "Иван")
    b, created_b = services.get_or_create_client("+7(914)123-45-67", "Другое имя")
    assert created_a is True and created_b is False
    assert a.pk == b.pk
    assert b.name == "Иван"
    assert a.phone == "+79141234567"


def test_get_or_create_client_rejects_bad_phone():
    with pytest.raises(InvalidPhone):
        services.get_or_create_client("123", "Иван")
    assert not Client.objects.exists()


# --- trucks and cargo ------------------------------------------------------


def test_create_truck_starts_loading(truck):
    assert truck.status == Truck.Status.LOADING
    assert truck.number.startswith("R-")
    assert truck.plate == "А123ВС125"


def test_add_cargo_without_truck_is_received(ivan):
    cargo = services.add_cargo(ivan, " 88123 ", places=3, weight_kg=Decimal("12.4"))
    assert cargo.status == Cargo.Status.RECEIVED
    assert cargo.truck is None
    assert cargo.article == "88123"


def test_add_cargo_into_loading_truck_is_loaded(ivan, truck):
    cargo = services.add_cargo(ivan, "88123", truck=truck)
    assert cargo.status == Cargo.Status.LOADED
    assert cargo.truck == truck


def test_add_cargo_validates(ivan):
    with pytest.raises(ValidationError):
        services.add_cargo(ivan, "")
    with pytest.raises(ValidationError):
        services.add_cargo(ivan, "88123", places=0)
    assert not Cargo.objects.exists()


def test_add_cargo_into_non_loading_truck_creates_nothing(ivan, loaded_truck):
    services.truck_depart(loaded_truck, now=T_DEPART)
    with pytest.raises(InvalidTransition):
        services.add_cargo(ivan, "NEW", truck=loaded_truck)
    assert not Cargo.objects.filter(article="NEW").exists()


def test_assign_to_truck(ivan, truck):
    cargo = services.add_cargo(ivan, "88123")
    services.assign_to_truck(cargo, truck)
    assert cargo.status == Cargo.Status.LOADED
    cargo.refresh_from_db()
    assert cargo.truck == truck
    assert cargo.status == Cargo.Status.LOADED


@pytest.mark.parametrize("truck_status", ["in_transit", "arrived", "closed"])
def test_assign_to_truck_rejects_non_loading_truck(ivan, truck_status):
    cargo = services.add_cargo(ivan, "88123")
    other = Truck.objects.create(status=truck_status)
    with pytest.raises(InvalidTransition):
        services.assign_to_truck(cargo, other)
    assert cargo.truck is None
    cargo.refresh_from_db()
    assert cargo.truck is None
    assert cargo.status == Cargo.Status.RECEIVED


def test_assign_to_truck_rejects_already_loaded_cargo(ivan, truck):
    cargo = services.add_cargo(ivan, "88123", truck=truck)
    other = services.create_truck()
    with pytest.raises(InvalidTransition):
        services.assign_to_truck(cargo, other)
    cargo.refresh_from_db()
    assert cargo.truck == truck
    assert cargo.status == Cargo.Status.LOADED


# --- depart ----------------------------------------------------------------


def test_truck_depart_cascades_and_notifies(loaded_truck, ivan, petr):
    services.truck_depart(loaded_truck, now=T_DEPART)

    assert loaded_truck.status == Truck.Status.IN_TRANSIT
    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.IN_TRANSIT
    assert loaded_truck.departed_at == T_DEPART
    assert set(loaded_truck.cargos.values_list("status", flat=True)) == {"in_transit"}

    notes = Notification.objects.filter(kind=Notification.Kind.DEPARTED)
    assert notes.count() == 2
    assert notes.get(client=ivan).status == Notification.Status.PENDING
    assert notes.get(client=petr).status == Notification.Status.SKIPPED
    assert "A1" in notes.get(client=ivan).text and "A2" in notes.get(client=ivan).text


@pytest.mark.parametrize("status", ["in_transit", "arrived", "closed"])
def test_truck_depart_invalid_changes_nothing(status, ivan):
    truck = Truck.objects.create(status=status)
    Cargo.objects.create(client=ivan, truck=truck, article="X", status=Cargo.Status.LOADED)
    before = snapshot(truck)
    with pytest.raises(InvalidTransition):
        services.truck_depart(truck, now=T_DEPART)
    assert truck.status == status
    assert snapshot(truck) == before


# --- arrive ----------------------------------------------------------------


def test_truck_arrive_cascades_and_notifies(loaded_truck, ivan, settings):
    settings.FREE_STORAGE_DAYS = 7
    services.truck_depart(loaded_truck, now=T_DEPART)
    services.truck_arrive(loaded_truck, now=T_ARRIVE)

    assert loaded_truck.status == Truck.Status.ARRIVED
    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.ARRIVED
    assert loaded_truck.arrived_at == T_ARRIVE
    for cargo in loaded_truck.cargos.all():
        assert cargo.status == Cargo.Status.READY
        assert cargo.ready_at == T_ARRIVE
        assert cargo.free_storage_until == date(2026, 10, 24)

    arrived = Notification.objects.filter(kind=Notification.Kind.ARRIVED)
    assert arrived.count() == 2
    assert "300 ₽" in arrived.get(client=ivan).text


def test_free_storage_uses_local_date(loaded_truck, settings):
    settings.FREE_STORAGE_DAYS = 7
    # 16:00 UTC on 17 Oct (what timezone.now() returns) is already 18 Oct in Vladivostok.
    arrive = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    services.truck_depart(loaded_truck, now=T_DEPART)
    services.truck_arrive(loaded_truck, now=arrive)
    assert set(loaded_truck.cargos.values_list("free_storage_until", flat=True)) == {
        date(2026, 10, 25)
    }


@pytest.mark.parametrize("status", ["loading", "arrived", "closed"])
def test_truck_arrive_invalid_changes_nothing(status, ivan):
    truck = Truck.objects.create(status=status)
    Cargo.objects.create(client=ivan, truck=truck, article="X", status=Cargo.Status.LOADED)
    before = snapshot(truck)
    with pytest.raises(InvalidTransition):
        services.truck_arrive(truck, now=T_ARRIVE)
    assert truck.status == status
    assert snapshot(truck) == before


# --- pickup and auto-close ---------------------------------------------------


@pytest.fixture
def arrived_truck(loaded_truck):
    services.truck_depart(loaded_truck, now=T_DEPART)
    services.truck_arrive(loaded_truck, now=T_ARRIVE)
    return loaded_truck


def test_cargo_pickup(arrived_truck, staff):
    cargo = arrived_truck.cargos.get(article="A1")
    services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP)
    assert cargo.status == Cargo.Status.PICKED_UP
    cargo.refresh_from_db()
    assert cargo.status == Cargo.Status.PICKED_UP
    assert cargo.picked_up_at == T_PICKUP
    assert cargo.picked_up_by == staff
    assert cargo.paid is True


def test_truck_closes_only_after_last_pickup(arrived_truck, staff):
    cargos = list(arrived_truck.cargos.order_by("id"))
    for cargo in cargos[:-1]:
        services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP)
        arrived_truck.refresh_from_db()
        assert arrived_truck.status == Truck.Status.ARRIVED

    services.cargo_pickup(cargos[-1], staff, paid=False, now=T_PICKUP)
    arrived_truck.refresh_from_db()
    assert arrived_truck.status == Truck.Status.CLOSED


def test_pickup_of_ready_cargo_without_truck(ivan, staff):
    cargo = Cargo.objects.create(client=ivan, article="X", status=Cargo.Status.READY)
    services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP)
    cargo.refresh_from_db()
    assert cargo.status == Cargo.Status.PICKED_UP


@pytest.mark.parametrize("status", ["received", "loaded", "in_transit", "picked_up"])
def test_cargo_pickup_invalid_changes_nothing(status, ivan, staff):
    truck = Truck.objects.create(status=Truck.Status.ARRIVED)
    cargo = Cargo.objects.create(client=ivan, truck=truck, article="X", status=status)
    with pytest.raises(InvalidTransition):
        services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP)
    assert cargo.status == status
    cargo.refresh_from_db()
    truck.refresh_from_db()
    assert cargo.status == status
    assert cargo.picked_up_at is None and cargo.picked_up_by is None and cargo.paid is False
    assert truck.status == Truck.Status.ARRIVED


def test_full_flow_notifications_once_per_kind(arrived_truck, staff):
    assert Notification.objects.count() == 4  # 2 clients x (departed, arrived)
    for cargo in arrived_truck.cargos.all():
        services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP + timedelta(minutes=5))
    assert Notification.objects.count() == 4


# ---------- capacity and closed_at ----------


def test_create_truck_capacity_defaults_to_60():
    assert services.create_truck().capacity_places == 60
    assert services.create_truck(capacity_places=82).capacity_places == 82


def test_create_truck_rejects_zero_capacity():
    with pytest.raises(ValidationError):
        services.create_truck(capacity_places=0)
    assert not Truck.objects.exists()


def test_auto_close_on_last_pickup_sets_closed_at(arrived_truck, staff):
    cargos = list(arrived_truck.cargos.order_by("id"))
    for cargo in cargos[:-1]:
        services.cargo_pickup(cargo, staff, paid=True, now=T_PICKUP)
    assert Truck.objects.get(pk=arrived_truck.pk).closed_at is None

    last = T_PICKUP + timedelta(minutes=5)
    services.cargo_pickup(cargos[-1], staff, paid=True, now=last)

    assert Truck.objects.get(pk=arrived_truck.pk).closed_at == last

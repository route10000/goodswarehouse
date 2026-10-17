from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from logistics import services
from logistics.models import Cargo, Truck
from logistics.services import InvalidTransition

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")
T_DEPART = datetime(2026, 10, 17, 9, 0, tzinfo=VLAT)
T_ARRIVE = datetime(2026, 10, 17, 15, 40, tzinfo=VLAT)


@pytest.fixture
def truck():
    truck = services.create_truck(plate="А123ВС125")
    client, _ = services.get_or_create_client("+79141234567", "Иван")
    services.add_cargo(client, "A1", truck=truck)
    services.add_cargo(client, "A2", truck=truck)
    return truck


def test_close_arrived_truck_keeps_ready_cargo_ready(truck):
    services.truck_depart(truck, now=T_DEPART)
    services.truck_arrive(truck, now=T_ARRIVE)

    services.truck_close(truck)

    assert truck.status == Truck.Status.CLOSED
    assert Truck.objects.get(pk=truck.pk).status == Truck.Status.CLOSED
    statuses = set(truck.cargos.values_list("status", flat=True))
    assert statuses == {Cargo.Status.READY}


def _advance(truck, target):
    if target in (Truck.Status.IN_TRANSIT, Truck.Status.ARRIVED, Truck.Status.CLOSED):
        services.truck_depart(truck, now=T_DEPART)
    if target in (Truck.Status.ARRIVED, Truck.Status.CLOSED):
        services.truck_arrive(truck, now=T_ARRIVE)
    if target == Truck.Status.CLOSED:
        services.truck_close(truck)


@pytest.mark.parametrize(
    "status", [Truck.Status.LOADING, Truck.Status.IN_TRANSIT, Truck.Status.CLOSED]
)
def test_close_rejects_truck_not_arrived(truck, status):
    _advance(truck, status)
    cargo_before = sorted(truck.cargos.values_list("status", flat=True))

    with pytest.raises(InvalidTransition):
        services.truck_close(truck)

    assert Truck.objects.get(pk=truck.pk).status == status
    assert sorted(truck.cargos.values_list("status", flat=True)) == cargo_before

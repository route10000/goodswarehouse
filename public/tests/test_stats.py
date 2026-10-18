"""Chapter «04 Цифры»: aggregate platform numbers, no personal data."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from logistics import services
from logistics.models import Client
from public.stats import PlatformStats, platform_stats

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return get_user_model().objects.create_user("sklad", password="x")


def arrived_cargo(now, *, departed_ago, arrived_ago, places=1, phone="+79990000001"):
    client, _ = services.get_or_create_client(phone)
    truck = services.create_truck()
    cargo = services.add_cargo(client, f"A-{truck.pk}-{places}", places=places, truck=truck)
    services.truck_depart(truck, now=now - departed_ago)
    services.truck_arrive(truck, now=now - arrived_ago)
    cargo.refresh_from_db()
    return truck, cargo


def test_empty_database_gives_zeros():
    stats = platform_stats(timezone.now())
    assert stats == PlatformStats(
        trips_30d=0, places_picked_30d=0, avg_storage_days=0.0, telegram_clients=0
    )


def test_trips_count_departures_within_30_days():
    now = timezone.now()
    for ago in (timedelta(days=1), timedelta(days=29)):
        services.truck_depart(services.create_truck(), now=now - ago)
    services.truck_depart(services.create_truck(), now=now - timedelta(days=31))
    services.create_truck()  # still loading, never departed
    services.truck_depart(services.create_truck(is_public=False), now=now - timedelta(days=2))
    assert platform_stats(now).trips_30d == 3


def test_places_and_average_storage_over_last_30_days(staff):
    now = timezone.now()
    _, two_days = arrived_cargo(
        now, departed_ago=timedelta(days=6), arrived_ago=timedelta(days=5), places=3
    )
    services.cargo_pickup(two_days, staff, paid=True, now=now - timedelta(days=3))
    _, five_days = arrived_cargo(
        now,
        departed_ago=timedelta(days=11),
        arrived_ago=timedelta(days=10),
        places=2,
        phone="+79990000002",
    )
    services.cargo_pickup(
        five_days, staff, paid=True, now=now - timedelta(days=4, hours=12)
    )  # 5.5 days in storage
    _, old = arrived_cargo(
        now,
        departed_ago=timedelta(days=50),
        arrived_ago=timedelta(days=49),
        places=7,
        phone="+79990000003",
    )
    services.cargo_pickup(old, staff, paid=True, now=now - timedelta(days=40))
    arrived_cargo(  # still on the shelf: not counted
        now,
        departed_ago=timedelta(days=3),
        arrived_ago=timedelta(days=2),
        places=4,
        phone="+79990000004",
    )
    stats = platform_stats(now)
    assert stats.places_picked_30d == 5
    assert stats.avg_storage_days == 3.8  # (2 + 5.5) / 2 = 3.75 → 1 decimal


def test_clients_with_telegram():
    services.get_or_create_client("+79990000001")
    for n, chat in ((2, 1001), (3, 1002)):
        client, _ = services.get_or_create_client(f"+7999000000{n}")
        Client.objects.filter(pk=client.pk).update(telegram_chat_id=chat)
    assert platform_stats(timezone.now()).telegram_clients == 2

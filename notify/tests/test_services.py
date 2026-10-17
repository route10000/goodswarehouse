from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction

from logistics.models import Cargo, Client, Truck
from notify.models import Notification
from notify.services import enqueue_arrived, enqueue_departed

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=VLAT)


@pytest.fixture
def truck():
    return Truck.objects.create(status=Truck.Status.IN_TRANSIT)


@pytest.fixture
def tg_client():
    return Client.objects.create(name="Иван", phone="+79141234567", telegram_chat_id=111)


@pytest.fixture
def plain_client():
    return Client.objects.create(name="Пётр", phone="+79141234568")


def add(client, truck, article, **kw):
    return Cargo.objects.create(
        client=client, truck=truck, article=article, status=Cargo.Status.IN_TRANSIT, **kw
    )


def test_one_aggregated_notification_per_client(truck, tg_client, plain_client):
    add(tg_client, truck, "A1")
    add(tg_client, truck, "A2")
    add(tg_client, truck, "A3")
    add(plain_client, truck, "B1")

    created = enqueue_departed(truck, now=NOW)

    assert len(created) == 2
    assert Notification.objects.count() == 2
    n = Notification.objects.get(client=tg_client)
    assert n.kind == Notification.Kind.DEPARTED
    assert n.truck == truck
    assert n.cargo is None
    assert n.channel == Notification.Channel.TELEGRAM
    for article in ("A1", "A2", "A3"):
        assert article in n.text
    assert "B1" not in n.text


def test_pending_with_telegram_skipped_without(truck, tg_client, plain_client):
    add(tg_client, truck, "A1")
    add(plain_client, truck, "B1")

    enqueue_departed(truck, now=NOW)

    assert Notification.objects.get(client=tg_client).status == Notification.Status.PENDING
    assert Notification.objects.get(client=plain_client).status == Notification.Status.SKIPPED


def test_dedupe_same_local_day(truck, tg_client):
    add(tg_client, truck, "A1")
    enqueue_departed(truck, now=NOW)

    # 19:00 the same local day, passed in UTC (a different UTC date than 09:00 local).
    again = enqueue_departed(truck, now=(NOW + timedelta(hours=10)).astimezone(UTC))

    assert again == []
    assert Notification.objects.count() == 1


def test_dedupe_is_per_kind_and_per_day(truck, tg_client):
    add(tg_client, truck, "A1")
    enqueue_departed(truck, now=NOW)
    enqueue_arrived(truck, now=NOW)
    enqueue_departed(truck, now=NOW + timedelta(days=1))

    assert Notification.objects.filter(kind=Notification.Kind.DEPARTED).count() == 2
    assert Notification.objects.filter(kind=Notification.Kind.ARRIVED).count() == 1


def test_db_constraint_blocks_duplicate(truck, tg_client):
    Notification.objects.create(
        client=tg_client, truck=truck, kind=Notification.Kind.ARRIVED, created_on=NOW.date()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.create(
            client=tg_client, truck=truck, kind=Notification.Kind.ARRIVED, created_on=NOW.date()
        )


def test_arrived_text_has_total_and_storage(truck, tg_client, settings):
    settings.WAREHOUSE_ADDRESS = "ул. Складская, 1"
    settings.WAREHOUSE_HOURS = "9:00–18:00"
    for article, amount in (("A1", "8400"), ("A2", "1600.50")):
        cargo = add(tg_client, truck, article, places=3, weight_kg=Decimal("12.4"))
        cargo.amount_due = Decimal(amount)
        cargo.status = Cargo.Status.READY
        cargo.free_storage_until = NOW.date() + timedelta(days=7)
        cargo.save()

    enqueue_arrived(truck, now=NOW)

    text = Notification.objects.get().text
    assert "10 000,50 ₽" in text
    assert "ул. Складская, 1" in text
    assert "9:00–18:00" in text
    assert "24.10.2026" in text
    assert "12,4 кг" in text


def test_no_cargo_no_notifications(truck):
    assert enqueue_departed(truck, now=NOW) == []

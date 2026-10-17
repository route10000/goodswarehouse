from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from logistics.models import Cargo, Client, Truck
from notify.models import Notification
from notify.services import enqueue_reminders

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")
TODAY = date(2026, 10, 17)
REMINDER = Notification.Kind.REMINDER
ENDING = Notification.Kind.STORAGE_ENDING


def at(day: date, hour=15) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=VLAT)


@pytest.fixture
def truck():
    return Truck.objects.create(status=Truck.Status.ARRIVED)


@pytest.fixture
def ivan():
    return Client.objects.create(name="Иван", phone="+79141234567", telegram_chat_id=111)


@pytest.fixture
def petr():
    return Client.objects.create(name="Пётр", phone="+79141234568")


def ready(client, truck, article, days_ago, free_days=7, status=Cargo.Status.READY):
    ready_day = TODAY - timedelta(days=days_ago)
    return Cargo.objects.create(
        client=client,
        truck=truck,
        article=article,
        status=status,
        ready_at=at(ready_day),
        free_storage_until=ready_day + timedelta(days=free_days),
    )


def kinds():
    return sorted(Notification.objects.values_list("kind", flat=True))


def test_reminder_for_telegram_client_ready_two_days(truck, ivan):
    ready(ivan, truck, "A1", days_ago=2)

    created = enqueue_reminders(TODAY)

    assert [n.kind for n in created] == [REMINDER]
    n = created[0]
    assert n.client == ivan
    assert n.status == Notification.Status.PENDING
    assert n.created_on == TODAY
    assert "уже 2 дн." in n.text
    assert "A1" in n.text


def test_no_reminder_before_two_local_days(truck, ivan):
    # Ready late yesterday evening local time (still "yesterday" though < 48h).
    c = ready(ivan, truck, "A1", days_ago=1)
    c.ready_at = at(TODAY - timedelta(days=1), hour=23)
    c.save()
    assert enqueue_reminders(TODAY) == []


def test_two_days_counted_in_local_dates(truck, ivan):
    # 16:30 UTC on the 14th is 02:30 local on the 15th: only 2 local days by the 17th.
    c = ready(ivan, truck, "A1", days_ago=0)
    c.ready_at = datetime(2026, 10, 14, 16, 30, tzinfo=ZoneInfo("UTC"))
    c.save()
    created = enqueue_reminders(TODAY)
    assert "уже 2 дн." in created[0].text


def test_no_reminder_without_telegram_or_not_ready(truck, ivan, petr):
    ready(petr, truck, "B1", days_ago=5)
    ready(ivan, truck, "A1", days_ago=5, status=Cargo.Status.PICKED_UP)
    assert enqueue_reminders(TODAY) == []
    assert Notification.objects.count() == 0


def test_one_reminder_per_client_listing_only_old_enough_cargo(truck, ivan):
    other_truck = Truck.objects.create(status=Truck.Status.ARRIVED)
    ready(ivan, truck, "A1", days_ago=3)
    ready(ivan, other_truck, "A2", days_ago=2)
    ready(ivan, other_truck, "A3", days_ago=1)

    created = enqueue_reminders(TODAY)

    assert len(created) == 1
    assert "A1" in created[0].text
    assert "A2" in created[0].text
    assert "A3" not in created[0].text
    assert "уже 3 дн." in created[0].text


def test_reminder_idempotent_same_day(truck, ivan):
    ready(ivan, truck, "A1", days_ago=2, free_days=30)
    enqueue_reminders(TODAY)
    assert enqueue_reminders(TODAY) == []
    assert Notification.objects.filter(kind=REMINDER).count() == 1


def test_reminder_not_more_often_than_every_two_days(truck, ivan):
    ready(ivan, truck, "A1", days_ago=2, free_days=30)

    sent_days = []
    for offset in range(6):
        day = TODAY + timedelta(days=offset)
        for _ in range(2):  # the timer may fire twice a day
            if enqueue_reminders(day):
                sent_days.append(offset)

    assert sent_days == [0, 2, 4]


def test_cadence_counts_any_reminder_status(truck, ivan):
    ready(ivan, truck, "A1", days_ago=4, free_days=30)
    Notification.objects.create(
        client=ivan,
        kind=REMINDER,
        status=Notification.Status.FAILED,
        created_on=TODAY - timedelta(days=1),
    )
    assert enqueue_reminders(TODAY) == []


def test_storage_ending_the_day_before_only(truck, ivan):
    ready(ivan, truck, "A1", days_ago=1, free_days=3)  # free until TODAY + 2

    assert enqueue_reminders(TODAY) == []
    created = enqueue_reminders(TODAY + timedelta(days=1))  # the day before
    assert [n.kind for n in created] == [ENDING]
    assert created[0].truck == truck
    assert created[0].text.startswith("Завтра заканчивается бесплатное хранение груза")
    last_free_day = enqueue_reminders(TODAY + timedelta(days=2))
    assert ENDING not in [n.kind for n in last_free_day]
    assert Notification.objects.filter(kind=ENDING).count() == 1


def test_storage_ending_only_once_even_if_run_twice(truck, ivan):
    ready(ivan, truck, "A1", days_ago=0, free_days=1)
    ready(ivan, truck, "A2", days_ago=0, free_days=1)

    first = enqueue_reminders(TODAY)
    second = enqueue_reminders(TODAY)

    assert [n.kind for n in first] == [ENDING]
    assert "A1" in first[0].text and "A2" in first[0].text
    assert second == []


def test_storage_ending_not_for_client_without_telegram(truck, petr):
    ready(petr, truck, "B1", days_ago=0, free_days=1)
    assert enqueue_reminders(TODAY) == []


def test_storage_ending_replaces_reminder_that_day(truck, ivan):
    # Ready 6 days ago, 7 free days: today is the day before the end.
    ready(ivan, truck, "A1", days_ago=6, free_days=7)

    created = enqueue_reminders(TODAY)

    assert [n.kind for n in created] == [ENDING]
    assert kinds() == [ENDING]


def test_default_today_is_local_date(truck, ivan):
    from django.utils import timezone

    today = timezone.localdate()
    Cargo.objects.create(
        client=ivan,
        truck=truck,
        article="A1",
        status=Cargo.Status.READY,
        ready_at=at(today - timedelta(days=2)),
        free_storage_until=today + timedelta(days=30),
    )
    created = enqueue_reminders()
    assert [n.created_on for n in created] == [today]

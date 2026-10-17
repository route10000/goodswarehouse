from datetime import date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command

from logistics.models import Cargo, Client, Truck
from notify.models import Notification

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")


def run(*args):
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


@pytest.fixture
def pending(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    client = Client.objects.create(name="Иван", phone="+79141234567", telegram_chat_id=111)
    return [
        Notification.objects.create(client=client, kind=Notification.Kind.CUSTOM, text=f"t{i}")
        for i in range(3)
    ]


def test_send_notifications_one_pass_by_default(pending):
    out = run("send_notifications")
    assert Notification.objects.filter(status=Notification.Status.SENT).count() == 3
    assert "отправлено 3" in out


def test_send_notifications_limit(pending):
    run("send_notifications", "--limit", "2")
    assert Notification.objects.filter(status=Notification.Status.PENDING).count() == 1


def test_send_notifications_loop_until_interrupted(pending, monkeypatch):
    from notify.management.commands import send_notifications as command

    passes, sleeps = [], []
    monkeypatch.setattr(command, "send_pending", lambda limit: passes.append(limit) or {})

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(command.time, "sleep", sleep)

    run("send_notifications", "--loop", "--interval", "7")

    assert len(passes) == 3
    assert sleeps == [7, 7, 7]


def test_send_notifications_loop_survives_a_failing_pass(pending, monkeypatch):
    from notify.management.commands import send_notifications as command

    calls = []

    def flaky(limit):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db is down")
        return {}

    monkeypatch.setattr(command, "send_pending", flaky)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(command.time, "sleep", sleep)

    run("send_notifications", "--loop")

    assert len(calls) == 2
    assert sleeps == [10, 10]


def test_daily_reminders_with_date():
    client = Client.objects.create(name="Иван", phone="+79141234567", telegram_chat_id=111)
    truck = Truck.objects.create(status=Truck.Status.ARRIVED)
    day = date(2026, 10, 17)
    Cargo.objects.create(
        client=client,
        truck=truck,
        article="A1",
        status=Cargo.Status.READY,
        ready_at=datetime.combine(day - timedelta(days=3), time(10), tzinfo=VLAT),
        free_storage_until=day + timedelta(days=4),
    )

    out = run("daily_reminders", "--date", "2026-10-17")
    again = run("daily_reminders", "--date", "2026-10-17")

    assert Notification.objects.get().kind == Notification.Kind.REMINDER
    assert "напоминаний 1" in out
    assert "напоминаний 0" in again


def test_daily_reminders_rejects_bad_date():
    with pytest.raises(CommandError):
        run("daily_reminders", "--date", "17.10.2026")

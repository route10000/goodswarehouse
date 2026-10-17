from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.utils import timezone

from logistics.models import Cargo, Client, ContactAttempt, DailyOrder, DailyOrderLine, Truck
from notify.models import Notification

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def truck_counts():
    return {s: Truck.objects.filter(status=s).count() for s in Truck.Status.values}


def test_seeds_empty_database():
    out = run()
    assert truck_counts() == {"loading": 2, "in_transit": 2, "arrived": 2, "closed": 1}
    assert 20 <= Client.objects.count() <= 30
    with_tg = Client.objects.filter(telegram_chat_id__isnull=False).count()
    assert 0 < with_tg < Client.objects.count()
    for status in Cargo.Status.values:
        assert Cargo.objects.filter(status=status).exists(), status
    assert Notification.objects.filter(status=Notification.Status.SENT).exists()
    assert Notification.objects.filter(status=Notification.Status.FAILED).exists()
    order = DailyOrder.objects.get(date=timezone.localdate())
    assert order.lines.filter(call_status=DailyOrderLine.CallStatus.REACHED).exists()
    assert order.lines.filter(call_status=DailyOrderLine.CallStatus.NO_ANSWER).exists()
    assert order.lines.filter(promised_date__isnull=False).exists()
    assert ContactAttempt.objects.exists()
    assert User.objects.get(username="manager").groups.filter(name="Менеджер").exists()
    assert User.objects.get(username="sklad").groups.filter(name="Склад").exists()
    assert not User.objects.filter(is_superuser=True).exists()
    assert "demo" in out.lower() or "демо" in out.lower()


def test_no_statuses_outside_allowed_values():
    run()
    assert not Truck.objects.exclude(status__in=Truck.Status.values).exists()
    assert not Cargo.objects.exclude(status__in=Cargo.Status.values).exists()
    assert not Notification.objects.exclude(status__in=Notification.Status.values).exists()
    # Closed truck: all its cargo picked up; arrived trucks only partly.
    closed = Truck.objects.get(status=Truck.Status.CLOSED)
    assert not closed.cargos.exclude(status=Cargo.Status.PICKED_UP).exists()
    for truck in Truck.objects.filter(status=Truck.Status.ARRIVED):
        assert truck.cargos.filter(status=Cargo.Status.PICKED_UP).exists()
        assert truck.cargos.filter(status=Cargo.Status.READY).exists()


def test_refuses_second_run_without_reset():
    run()
    with pytest.raises(CommandError):
        run()
    assert Truck.objects.count() == 7


def test_reset_recreates_data_and_keeps_users():
    run()
    manager_pk = User.objects.get(username="manager").pk
    run("--reset")
    assert truck_counts() == {"loading": 2, "in_transit": 2, "arrived": 2, "closed": 1}
    assert User.objects.get(username="manager").pk == manager_pk
    assert DailyOrder.objects.filter(date=timezone.localdate()).count() == 1

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from logistics.models import Cargo, Client, DailyOrder, Truck

# «Today» is taken here and again by the command: pin the clock so midnight cannot split them.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pinned_now")]


def run(*args):
    out = StringIO()
    call_command("build_daily_order", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def ready_cargo():
    client = Client.objects.create(name="Пётр", phone="+79141234568")
    truck = Truck.objects.create(status=Truck.Status.ARRIVED)
    return Cargo.objects.create(
        client=client,
        truck=truck,
        article="B1",
        status=Cargo.Status.READY,
        ready_at=timezone.now(),
    )


def test_builds_todays_order_by_default(ready_cargo):
    out = run()
    order = DailyOrder.objects.get()
    assert order.date == timezone.localdate()
    assert order.created_by is None
    assert order.lines.get().cargo == ready_cargo
    assert "добавлено 1" in out
    assert "всего 1" in out


def test_rerun_reports_zero_added(ready_cargo):
    run()
    out = run()
    assert "добавлено 0" in out
    assert "всего 1" in out


def test_date_option(ready_cargo):
    tomorrow = timezone.localdate() + timedelta(days=1)
    run("--date", tomorrow.isoformat())
    assert DailyOrder.objects.get().date == tomorrow


def test_bad_and_past_dates_are_errors():
    with pytest.raises(CommandError):
        run("--date", "2026-13-01")
    yesterday = timezone.localdate() - timedelta(days=1)
    with pytest.raises(CommandError, match="только для чтения"):
        run("--date", yesterday.isoformat())

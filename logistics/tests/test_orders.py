from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from logistics.models import Cargo, Client, ContactAttempt, DailyOrder, DailyOrderLine, Truck
from logistics.orders import (
    CargoNotReady,
    DuplicateLine,
    LineLocked,
    OrderError,
    OrderReadOnly,
    add_line,
    build_daily_order,
    mark_line,
    remove_line,
)
from notify.models import Notification

# «Today» is taken here and again inside orders.py: pin the clock so midnight cannot split them.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pinned_now")]

VLAT = ZoneInfo("Asia/Vladivostok")
L = DailyOrderLine.CallStatus


def today() -> date:
    return timezone.localdate()


def at(day: date, hour=10, minute=0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=VLAT)


@pytest.fixture
def staff():
    return get_user_model().objects.create_user("sklad", password="x")


@pytest.fixture
def truck():
    return Truck.objects.create(status=Truck.Status.ARRIVED)


_phones = iter(range(10, 99))


def client(telegram=True, name="Клиент"):
    return Client.objects.create(
        name=name,
        phone=f"+791412345{next(_phones):02d}",
        telegram_chat_id=1000 + next(_phones) if telegram else None,
    )


def cargo(owner, truck, article, days=0, status=Cargo.Status.READY, **kw):
    ready_day = today() - timedelta(days=days)
    return Cargo.objects.create(
        client=owner,
        truck=truck,
        article=article,
        status=status,
        ready_at=at(ready_day),
        free_storage_until=ready_day + timedelta(days=7),
        **kw,
    )


def notified(owner, status, minutes=0):
    return Notification.objects.create(
        client=owner,
        kind=Notification.Kind.CUSTOM,
        status=status,
        created_at=timezone.now() - timedelta(hours=1) + timedelta(minutes=minutes),
    )


def articles(order):
    return sorted(order.lines.values_list("article", flat=True))


def past_order(days_ago, lines):
    """A previous order as it would have been left: ``lines`` is [(cargo, status)]."""
    order = DailyOrder.objects.create(date=today() - timedelta(days=days_ago))
    for c, status in lines:
        DailyOrderLine.objects.create(
            order=order, cargo=c, article=c.article, phone=c.client.phone, call_status=status
        )
    return order


def attempt(c, staff, outcome, promised=None, minutes=0):
    a = ContactAttempt.objects.create(
        client=c.client, cargo=c, staff=staff, outcome=outcome, promised_date=promised
    )
    ContactAttempt.objects.filter(pk=a.pk).update(
        created_at=timezone.now() - timedelta(days=1) + timedelta(minutes=minutes)
    )
    return a


# --- inclusion ---


def test_client_without_telegram_is_included_on_day_one(truck):
    cargo(client(telegram=False), truck, "NO-TG", days=0)
    order, added = build_daily_order(today())
    assert added == 1
    assert articles(order) == ["NO-TG"]


def test_telegram_client_recently_notified_is_not_included(truck):
    owner = client()
    cargo(owner, truck, "TG", days=1)
    notified(owner, Notification.Status.SENT)
    order, added = build_daily_order(today())
    assert added == 0
    assert articles(order) == []


@pytest.mark.parametrize("status", [Notification.Status.SKIPPED, Notification.Status.FAILED])
def test_last_notification_skipped_or_failed_is_included(truck, status):
    owner = client()
    cargo(owner, truck, "TG", days=0)
    notified(owner, Notification.Status.SENT, minutes=0)
    notified(owner, status, minutes=5)
    order, _ = build_daily_order(today())
    assert articles(order) == ["TG"]


@pytest.mark.parametrize("status", [Notification.Status.SENT, Notification.Status.PENDING])
def test_only_the_last_notification_counts(truck, status):
    owner = client()
    cargo(owner, truck, "TG", days=0)
    notified(owner, Notification.Status.FAILED, minutes=0)
    notified(owner, status, minutes=5)
    order, _ = build_daily_order(today())
    assert articles(order) == []


def test_telegram_client_without_any_notification_waits_for_call_after_days(truck):
    cargo(client(), truck, "TG", days=1)
    assert articles(build_daily_order(today())[0]) == []


def test_ready_call_after_days_is_included(truck, settings):
    settings.CALL_AFTER_DAYS = 3
    owner = client()
    notified(owner, Notification.Status.SENT)
    cargo(owner, truck, "OLD", days=3)
    cargo(owner, truck, "NEW", days=2)
    order, _ = build_daily_order(today())
    assert articles(order) == ["OLD"]


def test_call_after_days_comes_from_settings(truck, settings):
    settings.CALL_AFTER_DAYS = 5
    owner = client()
    notified(owner, Notification.Status.SENT)
    cargo(owner, truck, "D4", days=4)
    cargo(owner, truck, "D5", days=5)
    assert articles(build_daily_order(today())[0]) == ["D5"]


def test_days_are_counted_in_local_dates(truck, settings):
    settings.CALL_AFTER_DAYS = 3
    owner = client()
    notified(owner, Notification.Status.SENT)
    c = cargo(owner, truck, "EDGE", days=0)
    # 23:30 local three days ago is fewer than 72 hours ago but three local days.
    three_days_ago = today() - timedelta(days=3)
    c.ready_at = at(three_days_ago, 23, 30).astimezone(UTC)
    c.save()
    order, _ = build_daily_order(today())
    assert articles(order) == ["EDGE"]
    assert order.lines.get().days_waiting == 3


def test_only_ready_cargo_is_included(truck):
    owner = client(telegram=False)
    cargo(owner, truck, "READY", days=0)
    cargo(owner, truck, "TRANSIT", status=Cargo.Status.IN_TRANSIT)
    cargo(owner, truck, "GONE", days=5, status=Cargo.Status.PICKED_UP)
    assert articles(build_daily_order(today())[0]) == ["READY"]


def test_carry_over_pending_and_no_answer_from_previous_order(truck):
    owner = client()
    notified(owner, Notification.Status.SENT)
    pending = cargo(owner, truck, "PENDING", days=1)
    no_answer = cargo(owner, truck, "NOANS", days=1)
    reached = cargo(owner, truck, "REACHED", days=1)
    picked = cargo(owner, truck, "PICKED", days=1, status=Cargo.Status.PICKED_UP)
    past_order(
        1,
        [
            (pending, L.PENDING),
            (no_answer, L.NO_ANSWER),
            (reached, L.REACHED),
            (picked, L.NO_ANSWER),
        ],
    )

    order, added = build_daily_order(today())

    assert articles(order) == ["NOANS", "PENDING"]
    assert added == 2


def test_carry_over_only_from_the_most_recent_previous_order(truck):
    owner = client()
    notified(owner, Notification.Status.SENT)
    old = cargo(owner, truck, "OLD", days=1)
    recent = cargo(owner, truck, "RECENT", days=1)
    past_order(3, [(old, L.NO_ANSWER)])
    past_order(2, [(recent, L.NO_ANSWER)])  # the most recent previous one (no order yesterday)
    assert articles(build_daily_order(today())[0]) == ["RECENT"]


def test_one_line_per_cargo_even_if_several_rules_match(truck):
    owner = client(telegram=False)
    c = cargo(owner, truck, "MANY", days=10)
    past_order(1, [(c, L.NO_ANSWER)])
    order, added = build_daily_order(today())
    assert added == 1
    assert order.lines.count() == 1


# --- exclusion ---


def test_reached_with_promised_date_today_or_later_is_excluded(truck, staff):
    owner = client(telegram=False)
    promised_today = cargo(owner, truck, "TODAY", days=5)
    promised_later = cargo(owner, truck, "LATER", days=5)
    promised_past = cargo(owner, truck, "PAST", days=5)
    attempt(promised_today, staff, L.REACHED, promised=today())
    attempt(promised_later, staff, L.REACHED, promised=today() + timedelta(days=2))
    attempt(promised_past, staff, L.REACHED, promised=today() - timedelta(days=1))

    assert articles(build_daily_order(today())[0]) == ["PAST"]


def test_promise_excludes_carried_over_line(truck, staff):
    owner = client()
    notified(owner, Notification.Status.SENT)
    c = cargo(owner, truck, "C1", days=1)
    past_order(1, [(c, L.NO_ANSWER)])
    attempt(c, staff, L.REACHED, promised=today())
    assert articles(build_daily_order(today())[0]) == []


def test_the_latest_reached_attempt_decides(truck, staff):
    owner = client(telegram=False)
    renewed = cargo(owner, truck, "RENEWED", days=5)
    attempt(renewed, staff, L.REACHED, promised=today() + timedelta(days=3), minutes=0)
    attempt(renewed, staff, L.REACHED, promised=None, minutes=5)  # promise withdrawn
    kept = cargo(owner, truck, "KEPT", days=5)
    attempt(kept, staff, L.REACHED, promised=today() + timedelta(days=1), minutes=0)
    attempt(kept, staff, L.NO_ANSWER, minutes=5)  # a later miss does not cancel the promise

    assert articles(build_daily_order(today())[0]) == ["RENEWED"]


def test_promise_of_another_cargo_does_not_exclude(truck, staff):
    owner = client(telegram=False)
    a = cargo(owner, truck, "A", days=5)
    cargo(owner, truck, "B", days=5)
    attempt(a, staff, L.REACHED, promised=today())
    assert articles(build_daily_order(today())[0]) == ["B"]


# --- snapshot, rerun, ownership ---


def test_line_snapshot_fields(truck, staff):
    owner = client(telegram=False, name="Иван Петров")
    cargo(
        owner,
        truck,
        "88123",
        days=4,
        places=3,
        weight_kg=Decimal("12.40"),
        amount_due=Decimal("8400"),
    )

    order, _ = build_daily_order(today(), user=staff)

    line = order.lines.get()
    assert order.date == today()
    assert order.created_by == staff
    assert line.article == "88123"
    assert line.client_name == "Иван Петров"
    assert line.phone == owner.phone
    assert line.weight_kg == Decimal("12.40")
    assert line.places == 3
    assert line.amount_due == Decimal("8400")
    assert line.days_waiting == 4
    assert line.call_status == L.PENDING
    assert line.promised_date is None


def test_lines_ordered_by_waiting_then_client(truck):
    first = client(telegram=False)
    second = client(telegram=False)
    cargo(second, truck, "S-NEW", days=0)
    cargo(first, truck, "F-NEW", days=0)
    cargo(second, truck, "S-OLD", days=6)
    order, _ = build_daily_order(today())
    assert list(order.lines.values_list("article", flat=True)) == ["S-OLD", "F-NEW", "S-NEW"]


def test_rerun_adds_only_missing_lines(truck):
    owner = client(telegram=False)
    cargo(owner, truck, "A", days=0)
    order, added = build_daily_order(today())
    assert added == 1

    cargo(owner, truck, "B", days=0)
    again, added = build_daily_order(today())

    assert again.pk == order.pk
    assert added == 1
    assert articles(order) == ["A", "B"]
    assert build_daily_order(today())[1] == 0


def test_rerun_never_touches_existing_lines(truck, staff):
    owner = client(telegram=False)
    promised = cargo(owner, truck, "PROMISED", days=0)
    picked = cargo(owner, truck, "PICKED", days=0, amount_due=Decimal("100"))
    order, _ = build_daily_order(today())
    line = order.lines.get(cargo=promised)
    mark_line(line, L.REACHED, staff, promised_date=today(), comment="приедет вечером")
    before = DailyOrderLine.objects.get(pk=line.pk)
    picked_line = order.lines.get(cargo=picked)
    Cargo.objects.filter(pk=picked.pk).update(
        status=Cargo.Status.PICKED_UP, amount_due=Decimal("999")
    )

    _, added = build_daily_order(today())

    after = DailyOrderLine.objects.get(pk=line.pk)
    assert added == 0
    assert order.lines.count() == 2
    assert (after.call_status, after.promised_date, after.comment, after.updated_at) == (
        before.call_status,
        before.promised_date,
        before.comment,
        before.updated_at,
    )
    picked_line = DailyOrderLine.objects.get(pk=picked_line.pk)
    assert picked_line.amount_due == Decimal("100")
    assert picked_line.is_picked_up is True


def test_rerun_keeps_created_by(truck, staff):
    order, _ = build_daily_order(today())
    other = get_user_model().objects.create_user("manager", password="x")
    build_daily_order(today(), user=other)
    order.refresh_from_db()
    assert order.created_by is None


def test_future_date_counts_days_to_that_date(truck):
    cargo(client(telegram=False), truck, "A", days=0)
    order, _ = build_daily_order(today() + timedelta(days=1))
    assert order.lines.get().days_waiting == 1


def test_build_for_past_date_is_refused(truck):
    cargo(client(telegram=False), truck, "A", days=3)
    with pytest.raises(OrderReadOnly):
        build_daily_order(today() - timedelta(days=1))
    assert not DailyOrder.objects.exists()


def test_order_read_only_flag():
    assert DailyOrder(date=today()).is_read_only is False
    assert DailyOrder(date=today() + timedelta(days=1)).is_read_only is False
    assert DailyOrder(date=today() - timedelta(days=1)).is_read_only is True


# --- manual add / remove ---


def test_add_line_manually(truck, staff):
    owner = client()
    notified(owner, Notification.Status.SENT)
    c = cargo(owner, truck, "MANUAL", days=1, places=2)
    order, _ = build_daily_order(today())

    line = add_line(order, c, staff)

    assert line.order == order
    assert line.article == "MANUAL"
    assert line.places == 2
    assert line.days_waiting == 1
    assert line.updated_by == staff
    assert line.call_status == L.PENDING


def test_add_line_requires_ready_cargo(truck, staff):
    c = cargo(client(), truck, "TRANSIT", status=Cargo.Status.IN_TRANSIT)
    order, _ = build_daily_order(today())
    with pytest.raises(CargoNotReady):
        add_line(order, c, staff)


def test_add_line_duplicate_is_an_error(truck, staff):
    c = cargo(client(telegram=False), truck, "DUP", days=0)
    order, _ = build_daily_order(today())
    with pytest.raises(DuplicateLine):
        add_line(order, c, staff)
    assert order.lines.count() == 1


def test_remove_pending_line(truck):
    c = cargo(client(telegram=False), truck, "X", days=0)
    order, _ = build_daily_order(today())
    remove_line(order.lines.get(cargo=c))
    assert order.lines.count() == 0


def test_remove_marked_line_is_refused(truck, staff):
    c = cargo(client(telegram=False), truck, "X", days=0)
    order, _ = build_daily_order(today())
    line = order.lines.get(cargo=c)
    mark_line(line, L.NO_ANSWER, staff)
    with pytest.raises(LineLocked):
        remove_line(line)
    assert order.lines.count() == 1


def test_past_order_rejects_add_and_remove(truck, staff):
    c = cargo(client(telegram=False), truck, "X", days=2)
    old = past_order(1, [(c, L.PENDING)])
    other = cargo(client(telegram=False), truck, "Y", days=2)
    with pytest.raises(OrderReadOnly):
        add_line(old, other, staff)
    with pytest.raises(OrderReadOnly):
        remove_line(old.lines.get())
    assert old.lines.count() == 1


# --- marking ---


def test_mark_reached_writes_line_and_contact_attempt(truck, staff):
    c = cargo(client(telegram=False), truck, "M", days=0)
    order, _ = build_daily_order(today())
    line = order.lines.get()
    promised = today() + timedelta(days=1)

    result = mark_line(line, L.REACHED, staff, promised_date=promised, comment="после обеда")

    line.refresh_from_db()
    assert result.pk == line.pk
    assert line.call_status == L.REACHED
    assert line.promised_date == promised
    assert line.comment == "после обеда"
    assert line.updated_by == staff
    a = ContactAttempt.objects.get()
    assert (a.client, a.cargo, a.staff, a.outcome) == (c.client, c, staff, "reached")
    assert a.promised_date == promised
    assert a.comment == "после обеда"


def test_mark_can_be_changed_repeatedly_the_same_day(truck, staff):
    cargo(client(telegram=False), truck, "M", days=0)
    order, _ = build_daily_order(today())
    line = order.lines.get()

    mark_line(line, L.NO_ANSWER, staff)
    mark_line(line, L.REACHED, staff, promised_date=today(), comment="перезвонил")
    mark_line(line, L.NO_ANSWER, staff)

    line.refresh_from_db()
    assert line.call_status == L.NO_ANSWER
    assert line.promised_date is None
    assert line.comment == ""
    assert list(ContactAttempt.objects.order_by("id").values_list("outcome", flat=True)) == [
        "no_answer",
        "reached",
        "no_answer",
    ]


@pytest.mark.parametrize("status", ["pending", "picked_up", ""])
def test_mark_rejects_unknown_status(truck, staff, status):
    cargo(client(telegram=False), truck, "M", days=0)
    line = build_daily_order(today())[0].lines.get()
    with pytest.raises(OrderError):
        mark_line(line, status, staff)
    assert not ContactAttempt.objects.exists()


def test_mark_no_answer_with_promised_date_is_rejected(truck, staff):
    cargo(client(telegram=False), truck, "M", days=0)
    line = build_daily_order(today())[0].lines.get()
    with pytest.raises(OrderError):
        mark_line(line, L.NO_ANSWER, staff, promised_date=today())
    assert not ContactAttempt.objects.exists()


def test_mark_on_past_order_is_rejected(truck, staff):
    c = cargo(client(telegram=False), truck, "OLD", days=2)
    line = past_order(1, [(c, L.PENDING)]).lines.get()
    with pytest.raises(OrderReadOnly, match="только для чтения"):
        mark_line(line, L.REACHED, staff)
    line.refresh_from_db()
    assert line.call_status == L.PENDING
    assert not ContactAttempt.objects.exists()


def test_mark_today_then_tomorrows_order_respects_promise(truck, staff):
    c = cargo(client(telegram=False), truck, "P", days=0)
    line = build_daily_order(today())[0].lines.get()
    mark_line(line, L.REACHED, staff, promised_date=today() + timedelta(days=1))

    tomorrow, _ = build_daily_order(today() + timedelta(days=1))
    day_after, _ = build_daily_order(today() + timedelta(days=2))

    assert articles(tomorrow) == []
    assert articles(day_after) == ["P"]
    assert c.pk == day_after.lines.get().cargo_id


def test_is_picked_up_follows_cargo_status(truck, staff):
    c = cargo(client(telegram=False), truck, "P", days=0)
    line = build_daily_order(today())[0].lines.get()
    assert line.is_picked_up is False
    Cargo.objects.filter(pk=c.pk).update(status=Cargo.Status.PICKED_UP)
    assert DailyOrderLine.objects.select_related("cargo").get(pk=line.pk).is_picked_up is True
    assert line.call_status == L.PENDING

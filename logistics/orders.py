"""Daily call order (SPEC §7). The only place that writes DailyOrderLine and ContactAttempt.

``build_daily_order`` creates the order for a date (or adds what is missing to it);
``add_line`` / ``remove_line`` are the manager's manual edits; ``mark_line`` records a
call result and writes the ContactAttempt journal. Orders for past dates are read-only.
"""

from datetime import date as Date
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.utils import timezone

from logistics.models import Cargo, ContactAttempt, DailyOrder, DailyOrderLine
from notify.models import Notification

CallStatus = DailyOrderLine.CallStatus


class OrderError(Exception):
    """A daily order operation the rules do not allow. The message is user-facing."""


class OrderReadOnly(OrderError):
    """The order is for a past date."""


class DuplicateLine(OrderError):
    """The cargo already has a line in this order."""


class CargoNotReady(OrderError):
    """Only cargo in status ``ready`` can be put into an order."""


class LineLocked(OrderError):
    """The line was already marked and cannot be removed."""


def _local_midnight(day: Date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def _ensure_editable(order: DailyOrder) -> None:
    if order.is_read_only:
        raise OrderReadOnly(f"Ордер за {order.date:%d.%m.%Y} только для чтения")


def _days_waiting(cargo: Cargo, day: Date) -> int:
    if cargo.ready_at is None:
        return 0
    return max(0, (day - timezone.localdate(cargo.ready_at)).days)


def _new_line(order: DailyOrder, cargo: Cargo, user=None) -> DailyOrderLine:
    return DailyOrderLine(
        order=order,
        cargo=cargo,
        article=cargo.article,
        client_name=cargo.client.name,
        phone=cargo.client.phone,
        weight_kg=cargo.weight_kg,
        places=cargo.places,
        amount_due=cargo.amount_due,
        days_waiting=_days_waiting(cargo, order.date),
        updated_by=user,
    )


def eligible_cargos(day: Date):
    """Ready cargo that belongs in the order for ``day`` (SPEC §7), oldest first.

    Included if any of: the client has no Telegram or their latest notification is
    ``skipped``/``failed``; ready for ≥ ``CALL_AFTER_DAYS`` local days; or its line in
    the most recent previous order is still ``pending``/``no_answer``. Excluded if the
    latest ``reached`` call about the cargo promised a visit on or after ``day``.
    """
    last_notification = Notification.objects.filter(client=OuterRef("client_id")).order_by(
        "-created_at", "-id"
    )
    latest_promise = ContactAttempt.objects.filter(
        cargo=OuterRef("pk"), outcome=ContactAttempt.Outcome.REACHED
    ).order_by("-created_at", "-id")
    # localdate(ready_at) <= day - N  <=>  ready_at < local midnight of (day - N + 1).
    waited_since = _local_midnight(day - timedelta(days=settings.CALL_AFTER_DAYS - 1))

    rules = (
        Q(client__telegram_chat_id__isnull=True)
        | Q(last_notification_status__in=[Notification.Status.SKIPPED, Notification.Status.FAILED])
        | Q(ready_at__lt=waited_since)
    )
    previous = DailyOrder.objects.filter(date__lt=day).order_by("-date").first()
    if previous is not None:
        carried = previous.lines.filter(
            call_status__in=[CallStatus.PENDING, CallStatus.NO_ANSWER]
        ).values("cargo_id")
        rules |= Q(pk__in=carried)

    return (
        Cargo.objects.filter(status=Cargo.Status.READY)
        .select_related("client")
        .annotate(
            last_notification_status=Subquery(last_notification.values("status")[:1]),
            latest_promise=Subquery(latest_promise.values("promised_date")[:1]),
        )
        .filter(rules)
        .filter(Q(latest_promise__isnull=True) | Q(latest_promise__lt=day))
        .order_by(TruncDate("ready_at"), "client__code", "id")
    )


def build_daily_order(date: Date, user=None) -> tuple[DailyOrder, int]:
    """Create the order for ``date`` or add the lines it is missing.

    Existing lines are never changed or removed, even if their cargo no longer
    qualifies. ``user`` is recorded as ``created_by`` only when the order is created.
    Returns ``(order, number_of_lines_added)``.
    """
    if date < timezone.localdate():
        raise OrderReadOnly(f"Ордер за {date:%d.%m.%Y} только для чтения")
    with transaction.atomic():
        order, _ = DailyOrder.objects.get_or_create(date=date, defaults={"created_by": user})
        # Serialize concurrent builds of the same order (timer + button).
        order = DailyOrder.objects.select_for_update().get(pk=order.pk)
        present = order.lines.values("cargo_id")
        cargos = eligible_cargos(date).exclude(pk__in=present)
        lines = DailyOrderLine.objects.bulk_create([_new_line(order, c) for c in cargos])
    return order, len(lines)


def add_line(order: DailyOrder, cargo: Cargo, user) -> DailyOrderLine:
    """Manager adds a ready cargo to the order by hand."""
    _ensure_editable(order)
    with transaction.atomic():
        locked = Cargo.objects.select_for_update().select_related("client").get(pk=cargo.pk)
        if locked.status != Cargo.Status.READY:
            raise CargoNotReady(f"Груз {locked.article} не на складе: нельзя добавить в ордер")
        if order.lines.filter(cargo=locked).exists():
            raise DuplicateLine(f"Груз {locked.article} уже есть в ордере")
        line = _new_line(order, locked, user)
        try:
            with transaction.atomic():
                line.save()
        except IntegrityError:  # a parallel add won the race
            raise DuplicateLine(f"Груз {locked.article} уже есть в ордере") from None
    return line


def remove_line(line: DailyOrderLine) -> None:
    """Manager removes a line that was not called yet."""
    with transaction.atomic():
        locked = DailyOrderLine.objects.select_for_update().select_related("order").get(pk=line.pk)
        _ensure_editable(locked.order)
        if locked.call_status != CallStatus.PENDING:
            raise LineLocked("Строку с отметкой о звонке убрать нельзя")
        locked.delete()


def mark_line(
    line: DailyOrderLine,
    status: str,
    user,
    promised_date: Date | None = None,
    comment: str = "",
) -> DailyOrderLine:
    """Record a call result on the line and in the ContactAttempt journal.

    ``status`` is ``reached`` or ``no_answer``; it may be changed again the same day.
    ``promised_date`` («приедет …») is only for ``reached``.
    """
    if status not in (CallStatus.REACHED, CallStatus.NO_ANSWER):
        raise OrderError("Отметьте «Позвонили» или «Не дозвонились»")
    if status == CallStatus.NO_ANSWER and promised_date is not None:
        raise OrderError("Дату приезда можно указать только для «Позвонили»")
    if user is None:
        raise OrderError("Отметку ставит сотрудник")
    comment = (comment or "").strip()
    with transaction.atomic():
        locked = (
            DailyOrderLine.objects.select_for_update()
            .select_related("order", "cargo")
            .get(pk=line.pk)
        )
        _ensure_editable(locked.order)
        locked.call_status = status
        locked.promised_date = promised_date
        locked.comment = comment
        locked.updated_by = user
        locked.save(
            update_fields=["call_status", "promised_date", "comment", "updated_by", "updated_at"]
        )
        ContactAttempt.objects.create(
            client_id=locked.cargo.client_id,
            cargo=locked.cargo,
            staff=user,
            outcome=status,
            promised_date=promised_date,
            comment=comment,
        )
    line.refresh_from_db()
    return line

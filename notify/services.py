"""Notification queue and sender.

Queueing (``enqueue_*``) runs inside the caller's transaction and never talks to
Telegram (SPEC §6). Only ``send_pending`` — called by ``send_notifications`` — sends.
"""

import logging
from datetime import datetime, time, timedelta
from itertools import groupby

from django.db import connection, transaction
from django.utils import timezone

from logistics.models import Cargo, Client
from notify import messages
from notify.models import Notification
from notify.telegram import (
    BotBlocked,
    PermanentError,
    RetryLater,
    TelegramClient,
    TemporaryError,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
REMINDER_AFTER_DAYS = 2
REMINDER_EVERY_DAYS = 2


# --- queueing ---


def _enqueue_for_truck(truck, kind: str, now=None) -> list[Notification]:
    """One notification per client of ``truck``, listing all that client's cargo in it."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    created = []
    with transaction.atomic():
        cargos = truck.cargos.select_related("client").order_by("client_id", "id")
        for _client_id, group in groupby(cargos, key=lambda c: c.client_id):
            client_cargos = list(group)
            client = client_cargos[0].client
            already = Notification.objects.filter(
                client=client, truck=truck, kind=kind, created_on=today
            ).exists()
            if already:
                continue
            status = (
                Notification.Status.PENDING if client.has_telegram else Notification.Status.SKIPPED
            )
            created.append(
                Notification.objects.create(
                    client=client,
                    truck=truck,
                    kind=kind,
                    status=status,
                    text=messages.render(kind, client, client_cargos, truck, today=today),
                    created_at=now,
                    created_on=today,
                )
            )
    return created


def enqueue_departed(truck, now=None) -> list[Notification]:
    return _enqueue_for_truck(truck, Notification.Kind.DEPARTED, now=now)


def enqueue_arrived(truck, now=None) -> list[Notification]:
    return _enqueue_for_truck(truck, Notification.Kind.ARRIVED, now=now)


def _ready_with_telegram():
    return Cargo.objects.filter(
        status=Cargo.Status.READY, client__telegram_chat_id__isnull=False
    ).select_related("client", "truck")


def _enqueue_storage_ending(today, now) -> list[Notification]:
    """``storage_ending`` the day before ``free_storage_until``, once per client and truck."""
    kind = Notification.Kind.STORAGE_ENDING
    cargos = _ready_with_telegram().filter(free_storage_until=today + timedelta(days=1))
    created = []
    key = lambda c: (c.client_id, c.truck_id)  # noqa: E731
    for (_client_id, truck_id), group in groupby(
        cargos.order_by("client_id", "truck_id", "id"), key
    ):
        group = list(group)
        client, truck = group[0].client, group[0].truck
        if Notification.objects.filter(client=client, truck_id=truck_id, kind=kind).exists():
            continue
        created.append(
            Notification.objects.create(
                client=client,
                truck=truck,
                kind=kind,
                status=Notification.Status.PENDING,
                text=messages.render(kind, client, group, truck, today=today),
                created_at=now,
                created_on=today,
            )
        )
    return created


def _enqueue_reminder(today, now) -> list[Notification]:
    """``reminder`` per client for cargo ready ≥ 2 local days, at most every 2 days.

    A client who gets ``storage_ending`` today gets no reminder the same day.
    """
    kind = Notification.Kind.REMINDER
    # localdate(ready_at) <= today - 2  <=>  ready_at < local midnight of (today - 1).
    cutoff = timezone.make_aware(
        datetime.combine(today - timedelta(days=REMINDER_AFTER_DAYS - 1), time.min)
    )
    ending_today = Notification.objects.filter(
        kind=Notification.Kind.STORAGE_ENDING, created_on=today
    ).values("client_id")
    cargos = (
        _ready_with_telegram()
        .filter(ready_at__lt=cutoff)
        .exclude(client_id__in=ending_today)
        .order_by("client_id", "ready_at", "id")
    )
    recent_since = today - timedelta(days=REMINDER_EVERY_DAYS - 1)
    created = []
    for _client_id, group in groupby(cargos, key=lambda c: c.client_id):
        group = list(group)
        client = group[0].client
        if Notification.objects.filter(
            client=client, kind=kind, created_on__gte=recent_since
        ).exists():
            continue
        created.append(
            Notification.objects.create(
                client=client,
                kind=kind,
                status=Notification.Status.PENDING,
                text=messages.render(kind, client, group, None, today=today),
                created_at=now,
                created_on=today,
            )
        )
    return created


def enqueue_reminders(today=None) -> list[Notification]:
    """Daily job (SPEC §7): ``storage_ending`` and ``reminder``. Safe to run twice a day."""
    today = today or timezone.localdate()
    now = timezone.now()
    with transaction.atomic():
        created = _enqueue_storage_ending(today, now)
        created += _enqueue_reminder(today, now)
    return created


# --- sending ---


def _locked_pending(pk):
    qs = Notification.objects.filter(pk=pk, status=Notification.Status.PENDING).select_related(
        "client"
    )
    features = connection.features
    if features.has_select_for_update_skip_locked:
        kwargs = {"skip_locked": True}
        if features.has_select_for_update_of:
            kwargs["of"] = ("self",)
        qs = qs.select_for_update(**kwargs)
    return qs.first()


def _retry_or_fail(n: Notification, error: str) -> str:
    n.last_error = error
    if n.attempts >= MAX_ATTEMPTS:
        n.status = Notification.Status.FAILED
        return "failed"
    return "retry"


def _send_one(pk, telegram: TelegramClient, now) -> str | None:
    """Send one pending notification; returns the stats bucket or ``None`` if gone."""
    with transaction.atomic():
        n = _locked_pending(pk)
        if n is None:  # sent by a parallel sender meanwhile, or locked by it
            return None
        chat_id = n.client.telegram_chat_id
        if chat_id is None:
            n.status = Notification.Status.SKIPPED
            n.last_error = "no telegram chat"
            n.save(update_fields=["status", "last_error"])
            return "skipped"

        n.attempts += 1
        try:
            result = telegram.send_message(chat_id, n.text)
        except BotBlocked as exc:
            n.status = Notification.Status.FAILED
            n.last_error = str(exc)
            # Only unlink the chat we tried, not one the client re-linked meanwhile.
            Client.objects.filter(pk=n.client_id, telegram_chat_id=chat_id).update(
                telegram_chat_id=None
            )
            outcome = "failed"
        except RetryLater as exc:
            note = f" (retry_after={exc.retry_after}s)" if exc.retry_after else ""
            outcome = _retry_or_fail(n, f"{exc}{note}")
        except TemporaryError as exc:
            outcome = _retry_or_fail(n, str(exc))
        except PermanentError as exc:
            n.status = Notification.Status.FAILED
            n.last_error = str(exc)
            outcome = "failed"
        except Exception as exc:  # a bug must not stop the batch; retried like a 5xx
            logger.exception("Unexpected error sending notification %s", n.pk)
            outcome = _retry_or_fail(n, f"{type(exc).__name__}: {exc}")
        else:
            n.status = Notification.Status.SENT
            n.sent_at = now or timezone.now()
            n.last_error = "dry-run" if result.dry_run else ""
            outcome = "sent"
        n.save(update_fields=["status", "attempts", "last_error", "sent_at"])
    if outcome != "sent":
        logger.warning("Notification %s: %s (%s)", n.pk, outcome, n.last_error)
    return outcome


def send_pending(limit: int = 100, now=None, telegram: TelegramClient | None = None) -> dict:
    """Send up to ``limit`` pending notifications, oldest first.

    Each notification is handled in its own transaction under a row lock
    (``SKIP LOCKED`` where the database supports it), so parallel senders do not
    double-send and one failure never stops the batch.
    """
    stats = {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    own_client = telegram is None
    telegram = telegram or TelegramClient()
    try:
        ids = list(
            Notification.objects.filter(status=Notification.Status.PENDING)
            .order_by("created_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        for pk in ids:
            try:
                outcome = _send_one(pk, telegram, now)
            except Exception:
                logger.exception("Could not process notification %s", pk)
                continue
            if outcome:
                stats[outcome] += 1
    finally:
        if own_client:
            telegram.close()
    return stats

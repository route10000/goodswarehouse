"""Queueing notifications. Nothing here talks to Telegram (see SPEC §6)."""

from itertools import groupby

from django.db import transaction
from django.utils import timezone

from notify import messages
from notify.models import Notification


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
                    text=messages.render(kind, client, client_cargos, truck),
                    created_at=now,
                    created_on=today,
                )
            )
    return created


def enqueue_departed(truck, now=None) -> list[Notification]:
    return _enqueue_for_truck(truck, Notification.Kind.DEPARTED, now=now)


def enqueue_arrived(truck, now=None) -> list[Notification]:
    return _enqueue_for_truck(truck, Notification.Kind.ARRIVED, now=now)

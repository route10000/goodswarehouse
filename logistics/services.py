"""Domain services (SPEC §6). The only place where statuses change.

Every service runs in ``transaction.atomic`` and works on a row-locked copy of the
objects; an invalid transition raises ``InvalidTransition`` before anything is written,
so neither the database nor the caller's instances change. On success the caller's
instances are refreshed from the database.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from logistics.models import DEFAULT_ROUTE, Cargo, Client, Truck
from logistics.phones import normalize_phone
from notify import services as notify_services


class InvalidTransition(Exception):
    """A status change that the rules in SPEC §6 do not allow."""


def _locked(model, pk):
    return model.objects.select_for_update().get(pk=pk)


def get_or_create_client(phone: str, name: str = "") -> tuple[Client, bool]:
    """Find a client by phone (any accepted format) or create one. Never renames."""
    normalized = normalize_phone(phone)
    return Client.objects.get_or_create(phone=normalized, defaults={"name": name.strip()})


def create_truck(
    *,
    plate: str = "",
    route: str = DEFAULT_ROUTE,
    driver_phone: str = "",
    loading_starts=None,
    departure_date=None,
    eta=None,
    is_public: bool = True,
    comment: str = "",
) -> Truck:
    truck = Truck(
        plate=plate,
        route=route,
        driver_phone=driver_phone,
        loading_starts=loading_starts,
        departure_date=departure_date,
        eta=eta,
        is_public=is_public,
        comment=comment,
        status=Truck.Status.LOADING,
    )
    truck.full_clean()
    truck.save()
    return truck


def add_cargo(
    client: Client,
    article: str,
    *,
    description: str = "",
    places: int = 1,
    weight_kg: Decimal | None = None,
    volume_m3: Decimal | None = None,
    amount_due: Decimal = Decimal("0"),
    truck: Truck | None = None,
) -> Cargo:
    """Register a received cargo; with ``truck`` it is loaded into it right away."""
    with transaction.atomic():
        cargo = Cargo(
            client=client,
            article=(article or "").strip(),
            description=description,
            places=places,
            weight_kg=weight_kg,
            volume_m3=volume_m3,
            amount_due=amount_due,
            status=Cargo.Status.RECEIVED,
        )
        cargo.full_clean()
        cargo.save()
        if truck is not None:
            assign_to_truck(cargo, truck)
    return cargo


def assign_to_truck(cargo: Cargo, truck: Truck) -> Cargo:
    """Put a ``received`` cargo into a ``loading`` truck: cargo becomes ``loaded``."""
    with transaction.atomic():
        locked_truck = _locked(Truck, truck.pk)
        locked_cargo = _locked(Cargo, cargo.pk)
        if locked_truck.status != Truck.Status.LOADING:
            raise InvalidTransition(f"Машина {locked_truck.number} уже не грузится")
        if locked_cargo.status != Cargo.Status.RECEIVED:
            raise InvalidTransition(
                f"Груз {locked_cargo.article} нельзя погрузить: {locked_cargo.get_status_display()}"
            )
        locked_cargo.truck = locked_truck
        locked_cargo.status = Cargo.Status.LOADED
        locked_cargo.save(update_fields=["truck", "status"])
    cargo.refresh_from_db()
    return cargo


def truck_depart(truck: Truck, user=None, now=None) -> Truck:
    """``loading`` → ``in_transit``; its cargo → ``in_transit``; queue ``departed``.

    ``user`` is accepted for the panel's call signature; v1.0 stores no audit for it.
    """
    now = now or timezone.now()
    with transaction.atomic():
        locked = _locked(Truck, truck.pk)
        if locked.status != Truck.Status.LOADING:
            raise InvalidTransition(f"Машину {locked.number} нельзя отправить")
        locked.status = Truck.Status.IN_TRANSIT
        locked.departed_at = now
        locked.save(update_fields=["status", "departed_at"])
        locked.cargos.filter(status=Cargo.Status.LOADED).update(status=Cargo.Status.IN_TRANSIT)
        notify_services.enqueue_departed(locked, now=now)
    truck.refresh_from_db()
    return truck


def truck_arrive(truck: Truck, user=None, now=None) -> Truck:
    """``in_transit`` → ``arrived``; cargo → ``ready`` with storage dates; queue ``arrived``."""
    now = now or timezone.now()
    free_until = timezone.localdate(now) + timedelta(days=settings.FREE_STORAGE_DAYS)
    with transaction.atomic():
        locked = _locked(Truck, truck.pk)
        if locked.status != Truck.Status.IN_TRANSIT:
            raise InvalidTransition(f"Машина {locked.number} не в пути")
        locked.status = Truck.Status.ARRIVED
        locked.arrived_at = now
        locked.save(update_fields=["status", "arrived_at"])
        locked.cargos.filter(status=Cargo.Status.IN_TRANSIT).update(
            status=Cargo.Status.READY, ready_at=now, free_storage_until=free_until
        )
        notify_services.enqueue_arrived(locked, now=now)
    truck.refresh_from_db()
    return truck


def truck_close(truck: Truck, user=None) -> Truck:
    """``arrived`` → ``closed`` by hand. Cargo not yet picked up stays ``ready``."""
    with transaction.atomic():
        locked = _locked(Truck, truck.pk)
        if locked.status != Truck.Status.ARRIVED:
            raise InvalidTransition(f"Рейс {locked.number} нельзя закрыть: машина не прибыла")
        locked.status = Truck.Status.CLOSED
        locked.save(update_fields=["status"])
    truck.refresh_from_db()
    return truck


def cargo_pickup(cargo: Cargo, user, paid: bool, now=None) -> Cargo:
    """``ready`` → ``picked_up``. Closes an ``arrived`` truck once all its cargo is out."""
    now = now or timezone.now()
    with transaction.atomic():
        truck_id = Cargo.objects.values_list("truck_id", flat=True).get(pk=cargo.pk)
        # Lock the truck first so concurrent pickups from one truck are serialized
        # and exactly one of them sees "everything picked up".
        locked_truck = _locked(Truck, truck_id) if truck_id else None
        locked = _locked(Cargo, cargo.pk)
        if locked.status != Cargo.Status.READY:
            raise InvalidTransition(
                f"Груз {locked.article} нельзя выдать: {locked.get_status_display()}"
            )
        locked.status = Cargo.Status.PICKED_UP
        locked.picked_up_at = now
        locked.picked_up_by = user
        locked.paid = bool(paid)
        locked.save(update_fields=["status", "picked_up_at", "picked_up_by", "paid"])
        if (
            locked_truck is not None
            and locked_truck.status == Truck.Status.ARRIVED
            and not locked_truck.cargos.exclude(status=Cargo.Status.PICKED_UP).exists()
        ):
            locked_truck.status = Truck.Status.CLOSED
            locked_truck.save(update_fields=["status"])
    cargo.refresh_from_db()
    return cargo

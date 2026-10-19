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
    capacity_places: int = 60,
) -> Truck:
    truck = Truck(
        capacity_places=capacity_places,
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
    track_number: str = "",
    seller: str = "",
    packaging: str = Cargo.Packaging.BOX,
    fragile: bool = False,
    note: str = "",
    photo=None,
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
            track_number=(track_number or "").strip(),
            seller=(seller or "").strip(),
            packaging=packaging or Cargo.Packaging.BOX,
            fragile=bool(fragile),
            note=note or "",
            status=Cargo.Status.RECEIVED,
        )
        if photo:
            cargo.photo = photo
        cargo.full_clean()
        cargo.save()
        if truck is not None:
            assign_to_truck(cargo, truck)
    return cargo


# Fields a person may edit on a cargo. Status, truck and the pickup / storage dates
# change only through the transitions below.
EDITABLE_CARGO_FIELDS = frozenset(
    {
        "client",
        "article",
        "description",
        "places",
        "weight_kg",
        "volume_m3",
        "amount_due",
        "track_number",
        "seller",
        "packaging",
        "fragile",
        "note",
        "photo",
    }
)
_STRIPPED = ("article", "track_number", "seller")


def update_cargo(cargo: Cargo, **fields) -> Cargo:
    """Edit plain (non-status) fields of a cargo after ``full_clean``.

    Unknown or protected fields raise ``ValueError``; a ``ValidationError`` leaves the
    database and the caller's instance unchanged.
    """
    refused = sorted(set(fields) - EDITABLE_CARGO_FIELDS)
    if refused:
        raise ValueError(f"Эти поля груза нельзя менять здесь: {', '.join(refused)}")
    for name in _STRIPPED:
        if name in fields:
            fields[name] = (fields[name] or "").strip()
    with transaction.atomic():
        locked = _locked(Cargo, cargo.pk)
        for name, value in fields.items():
            setattr(locked, name, value)
        locked.full_clean()
        if fields:
            locked.save(update_fields=list(fields))
    cargo.refresh_from_db()
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


def unassign_from_truck(cargo: Cargo) -> Cargo:
    """Take a ``loaded`` cargo out of a ``loading`` truck: it is ``received`` again."""
    with transaction.atomic():
        truck_id = Cargo.objects.values_list("truck_id", flat=True).get(pk=cargo.pk)
        # Truck first, then cargo: the same lock order as ``assign_to_truck``.
        locked_truck = _locked(Truck, truck_id) if truck_id else None
        locked = _locked(Cargo, cargo.pk)
        if locked_truck is None or locked.truck_id != locked_truck.pk:
            raise InvalidTransition(f"Груз {locked.article} не в машине")
        if locked_truck.status != Truck.Status.LOADING:
            raise InvalidTransition(f"Машина {locked_truck.number} уже не грузится")
        if locked.status != Cargo.Status.LOADED:
            raise InvalidTransition(
                f"Груз {locked.article} нельзя убрать: {locked.get_status_display()}"
            )
        locked.truck = None
        locked.status = Cargo.Status.RECEIVED
        locked.save(update_fields=["truck", "status"])
    cargo.refresh_from_db()
    return cargo


def _lock_many(truck: Truck, cargo_ids) -> tuple[Truck, list[Cargo]]:
    """Lock the truck, then the cargo rows in id order. Every id must exist."""
    ids = sorted({int(pk) for pk in cargo_ids})
    locked_truck = _locked(Truck, truck.pk)
    cargos = list(Cargo.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if len(cargos) != len(ids):
        raise InvalidTransition("Часть грузов не найдена. Обновите страницу")
    if locked_truck.status != Truck.Status.LOADING:
        raise InvalidTransition(f"Машина {locked_truck.number} уже не грузится")
    return locked_truck, cargos


def assign_many(cargo_ids, truck: Truck) -> int:
    """Load several ``received`` cargo into a ``loading`` truck, all or nothing."""
    with transaction.atomic():
        locked_truck, cargos = _lock_many(truck, cargo_ids)
        for cargo in cargos:
            if cargo.status != Cargo.Status.RECEIVED or cargo.truck_id is not None:
                raise InvalidTransition(
                    f"Груз {cargo.article} нельзя погрузить: {cargo.get_status_display()}"
                )
        Cargo.objects.filter(pk__in=[c.pk for c in cargos]).update(
            truck=locked_truck, status=Cargo.Status.LOADED
        )
    return len(cargos)


def unassign_many(cargo_ids, truck: Truck) -> int:
    """Take several ``loaded`` cargo out of a ``loading`` truck, all or nothing."""
    with transaction.atomic():
        locked_truck, cargos = _lock_many(truck, cargo_ids)
        for cargo in cargos:
            if cargo.truck_id != locked_truck.pk:
                raise InvalidTransition(f"Груз {cargo.article} не в машине {locked_truck.number}")
            if cargo.status != Cargo.Status.LOADED:
                raise InvalidTransition(
                    f"Груз {cargo.article} нельзя убрать: {cargo.get_status_display()}"
                )
        Cargo.objects.filter(pk__in=[c.pk for c in cargos]).update(
            truck=None, status=Cargo.Status.RECEIVED
        )
    return len(cargos)


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


def truck_close(truck: Truck, user=None, now=None) -> Truck:
    """``arrived`` → ``closed`` by hand, sets ``closed_at``. Unpicked cargo stays ``ready``."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = _locked(Truck, truck.pk)
        if locked.status != Truck.Status.ARRIVED:
            raise InvalidTransition(f"Рейс {locked.number} нельзя закрыть: машина не прибыла")
        locked.status = Truck.Status.CLOSED
        locked.closed_at = now
        locked.save(update_fields=["status", "closed_at"])
    truck.refresh_from_db()
    return truck


def cargo_pickup(cargo: Cargo, user, paid: bool, now=None) -> Cargo:
    """``ready`` → ``picked_up``. Closes an ``arrived`` truck (``closed_at = now``) once all
    its cargo is out."""
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
            locked_truck.closed_at = now
            locked_truck.save(update_fields=["status", "closed_at"])
    cargo.refresh_from_db()
    return cargo

"""«Автопилот» engine (SPEC §14.8): one ``tick`` advances the simulated clock and applies
the rules below through the domain services, with ``now=`` the simulated time.

Rules, in order:

1. in-transit trucks whose ETA has passed arrive (``truck_arrive``);
2. a loading truck at ≥ 85 % of its places, or loading for over 3 sim hours (and not
   empty), departs (``truck_depart``, ETA = sim now + 2–4 h);
3. there is always a loading truck; a second one starts once the newest is half full;
4. 1–3 received cargo for random clients (existing or new) are loaded into a truck with
   room, or wait for one;
5. 0–2 ready cargo are picked up by the system user «autopilot», older cargo first;
6. once per sim day after 08:30 the daily call order is built, only if the sim date is
   today's real date (the order functions work on the real date).

Each action runs in its own transaction; a failure is written to the journal as an
``error`` event and the tick goes on. Randomness comes only from the ``rng`` argument.
"""

import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from autopilot.models import AutopilotEvent, AutopilotState
from logistics import orders, services
from logistics.management.commands.seed_demo import (
    FEMALE_FIRST_NAMES,
    GOODS,
    MALE_FIRST_NAMES,
    PREFIXES,
    SURNAMES,
)
from logistics.models import Cargo, Client, DailyOrder, Truck
from panel.permissions import MANAGER_GROUP
from panel.views.common import plural

logger = logging.getLogger(__name__)

Kind = AutopilotEvent.Kind

DEFAULT_STEP = timedelta(seconds=5)  # real time of one step when there is no previous tick
MAX_ADVANCE = timedelta(minutes=10)  # sim time one tick may move, so a long gap is no jump
MIN_TICK_GAP = timedelta(seconds=1)  # a tick this soon after the last one is a duplicate
SKIPPED = "skipped"  # what ``tick`` returns when another tick already claimed this moment
MAX_LOADING = timedelta(hours=3)
DEFAULT_TRANSIT = timedelta(hours=3)  # for an in-transit truck without ETA
DEPART_FILL_PERCENT = 85
SECOND_TRUCK_FILL_PERCENT = 50
MAX_LOADING_TRUCKS = 2
ORDER_TIME = time(8, 30)
KEEP_EVENTS = 500
EXISTING_CLIENT_SHARE = 0.65
TELEGRAM_SHARE = 0.6
AUTOPILOT_USERNAME = "autopilot"

SELLERS = [
    "Yiwu Market", "Guangzhou Baiyun", "Shenzhen Huaqiang", "Taobao", "1688.com",
    "Pinduoduo", "Suifenhe Trade", "Harbin Opt",
]  # fmt: skip
TRACK_PREFIXES = ["YT", "SF", "JT", "ZTO"]
NOTES = ["Не кантовать", "Сверить количество при выдаче", "Клиент просил позвонить заранее"]
PACKAGING = {
    "одежда": [Cargo.Packaging.BAG, Cargo.Packaging.BOX],
    "текстиль": [Cargo.Packaging.BAG, Cargo.Packaging.BOX],
    "игрушки": [Cargo.Packaging.BOX, Cargo.Packaging.BAG],
    "запчасти": [Cargo.Packaging.BOX, Cargo.Packaging.PALLET],
    "инструмент": [Cargo.Packaging.BOX, Cargo.Packaging.PALLET],
}
FRAGILE_KINDS = {"посуда", "электроника"}
PLATE_LETTERS = "АВЕКМНОРСТУХ"


@dataclass
class Tick:
    """What one tick works with: sim «now», real «now», the rng and what it did."""

    state: AutopilotState
    now: datetime
    real_now: datetime
    rng: random.Random
    counts: Counter = field(default_factory=Counter)
    events: list = field(default_factory=list)

    def log(self, kind, message, *, truck=None, cargo=None) -> AutopilotEvent:
        event = AutopilotEvent.objects.create(
            created_at=self.real_now,
            sim_at=self.now,
            kind=kind,
            message=message[:255],
            truck=truck,
            cargo=cargo,
        )
        self.events.append(event)
        return event


def _places(n: int) -> str:
    return f"{n} {plural(n, 'место', 'места', 'мест')}"


def _attempt(ctx: Tick, what: str, action):
    """Run one action in its own transaction; on failure log an error event, never raise."""
    mark, counts = len(ctx.events), ctx.counts.copy()
    try:
        with transaction.atomic():
            return action()
    except (services.InvalidTransition, orders.OrderError) as exc:
        error = exc
    except Exception as exc:  # the simulator must keep going whatever one action does
        logger.exception("autopilot: %s", what)
        error = exc
    del ctx.events[mark:]  # their rows were rolled back with the action
    ctx.counts.clear()
    ctx.counts.update(counts)
    ctx.log(Kind.ERROR, f"{what}: {error}")
    return None


# ---------- clock ----------


def advance_clock(state: AutopilotState, real_now: datetime, elapsed=None) -> timedelta:
    """Move ``sim_clock`` by real elapsed × speed (at most ``MAX_ADVANCE``)."""
    if elapsed is None:
        elapsed = real_now - state.last_tick_at if state.last_tick_at else DEFAULT_STEP
    elapsed = max(elapsed, timedelta(0))
    advance = min(elapsed * state.speed, MAX_ADVANCE)
    state.sim_clock = (state.sim_clock or real_now) + advance
    state.last_tick_at = real_now
    return advance


# ---------- helpers ----------


def _phone(rng: random.Random) -> str:
    return f"+7{rng.choice(PREFIXES)}{rng.randint(0, 9999999):07d}"


def _plate(rng: random.Random) -> str:
    pick = lambda: rng.choice(PLATE_LETTERS)  # noqa: E731
    return f"{pick()}{rng.randint(100, 999)}{pick()}{pick()}125"


def _loaded_places(truck: Truck) -> int:
    total = truck.cargos.filter(status=Cargo.Status.LOADED).aggregate(n=Sum("places"))["n"]
    return total or 0


def _loading_trucks() -> list[Truck]:
    return list(Truck.objects.filter(status=Truck.Status.LOADING).order_by("created_at", "id"))


def autopilot_user():
    """System user «autopilot» (group «Менеджер», no usable password) who issues cargo."""
    user, _ = get_user_model().objects.get_or_create(
        username=AUTOPILOT_USERNAME,
        defaults={"first_name": "Автопилот", "password": make_password(None)},
    )
    group, _ = Group.objects.get_or_create(name=MANAGER_GROUP)
    user.groups.add(group)
    return user


# ---------- rules ----------


def arrive_trucks(ctx: Tick) -> None:
    due = Q(eta__lte=ctx.now) | Q(eta__isnull=True, departed_at__lte=ctx.now - DEFAULT_TRANSIT)
    for truck in Truck.objects.filter(due, status=Truck.Status.IN_TRANSIT).order_by("eta", "id"):
        _attempt(ctx, f"Прибытие {truck.number}", lambda t=truck: _arrive(ctx, t))


def _arrive(ctx: Tick, truck: Truck) -> None:
    services.truck_arrive(truck, now=ctx.now)
    n = truck.cargos.filter(status=Cargo.Status.READY).count()
    ctx.log(
        Kind.ARRIVED,
        f"Рейс {truck.number} прибыл на склад, к выдаче {n} {plural(n, 'груз', 'груза', 'грузов')}",
        truck=truck,
    )
    ctx.counts["arrived"] += 1


def depart_trucks(ctx: Tick) -> None:
    for truck in _loading_trucks():
        loaded = _loaded_places(truck)
        if not loaded:
            continue
        started = truck.loading_starts or truck.created_at
        full = loaded * 100 >= DEPART_FILL_PERCENT * truck.capacity_places
        if full or ctx.now - started > MAX_LOADING:
            _attempt(ctx, f"Отправка {truck.number}", lambda t=truck, n=loaded: _depart(ctx, t, n))


def _depart(ctx: Tick, truck: Truck, loaded: int) -> None:
    truck.eta = ctx.now + timedelta(minutes=ctx.rng.randint(120, 240))
    truck.save(update_fields=["eta"])  # a plain field; the status changes in the service
    services.truck_depart(truck, now=ctx.now)
    ctx.log(Kind.DEPARTED, f"Рейс {truck.number} отправлен ({_places(loaded)})", truck=truck)
    ctx.counts["departed"] += 1


def ensure_loading_trucks(ctx: Tick) -> None:
    loading = _loading_trucks()
    if len(loading) >= MAX_LOADING_TRUCKS:
        return
    if loading:
        newest = loading[-1]
        if _loaded_places(newest) * 100 < SECOND_TRUCK_FILL_PERCENT * newest.capacity_places:
            return
    _attempt(ctx, "Новый рейс", lambda: _create_truck(ctx))


def _create_truck(ctx: Tick) -> None:
    rng = ctx.rng
    capacity = rng.randint(40, 60)
    departure = ctx.now + MAX_LOADING
    truck = services.create_truck(
        plate=_plate(rng),
        driver_phone=_phone(rng),
        capacity_places=capacity,
        loading_starts=ctx.now,
        departure_date=timezone.localdate(ctx.now),
        eta=departure + timedelta(minutes=rng.randint(120, 240)),
    )
    ctx.log(
        Kind.TRUCK_CREATED,
        f"Рейс {truck.number} встал на погрузку (вместимость {_places(capacity)})",
        truck=truck,
    )
    ctx.counts["trucks_created"] += 1


def new_client(ctx: Tick) -> Client:
    """A new client with a free +7 9xx phone; about 60 % get a (fake) Telegram chat."""
    rng = ctx.rng
    male, female = rng.choice(SURNAMES)
    if rng.random() < 0.5:
        name = f"{rng.choice(MALE_FIRST_NAMES)} {male}"
    else:
        name = f"{rng.choice(FEMALE_FIRST_NAMES)} {female}"
    while True:
        client, created = services.get_or_create_client(_phone(rng), name)
        if created:
            break
    if rng.random() < TELEGRAM_SHARE:
        chat_id = 700_000_000 + rng.randrange(100_000_000)
        if not Client.objects.filter(telegram_chat_id=chat_id).exists():
            client.telegram_chat_id = chat_id
            client.save(update_fields=["telegram_chat_id"])
    return client


def _pick_client(ctx: Tick) -> tuple[Client, bool]:
    ids = list(Client.objects.order_by("pk").values_list("pk", flat=True))
    if ids and ctx.rng.random() < EXISTING_CLIENT_SHARE:
        return Client.objects.get(pk=ctx.rng.choice(ids)), False
    return new_client(ctx), True


def _truck_with_room(places: int) -> Truck | None:
    for truck in _loading_trucks():
        if _loaded_places(truck) + places <= truck.capacity_places:
            return truck
    return None


def receive_cargo(ctx: Tick) -> None:
    for _ in range(ctx.rng.randint(1, 3)):
        _attempt(ctx, "Приём груза", lambda: _receive_one(ctx))


def _receive_one(ctx: Tick) -> None:
    rng = ctx.rng
    client, is_new = _pick_client(ctx)
    kind, description = rng.choice(GOODS)
    places = rng.randint(1, 8)
    weight = (Decimal(places * rng.randint(6, 30)) + Decimal(rng.randint(0, 99)) / 100).quantize(
        Decimal("0.01")
    )
    fragile_chance = 0.7 if kind in FRAGILE_KINDS else 0.05
    truck = _truck_with_room(places)
    cargo = services.add_cargo(
        client,
        f"CN{ctx.now:%y}{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
        description=description,
        places=places,
        weight_kg=weight,
        volume_m3=Decimal(places) * Decimal(rng.choice(["0.040", "0.060", "0.080", "0.120"])),
        amount_due=Decimal(places * rng.choice([350, 420, 500, 650, 800])),
        track_number=f"{rng.choice(TRACK_PREFIXES)}{rng.randint(10**11, 10**12 - 1)}",
        seller=rng.choice(SELLERS),
        packaging=rng.choice(PACKAGING.get(kind, [Cargo.Packaging.BOX])),
        fragile=rng.random() < fragile_chance,
        note=rng.choice(NOTES) if rng.random() < 0.15 else "",
        truck=truck,
    )
    who = f"{client.name} ({client.code}{', новый клиент' if is_new else ''})"
    where = f"→ {truck.number}" if truck else "— ждёт машину"
    ctx.log(
        Kind.CARGO_ADDED,
        f"Принят груз {cargo.article} · {who}, {_places(places)} {where}",
        truck=truck,
        cargo=cargo,
    )
    ctx.counts["cargo_added"] += 1


def pick_weighted(cargos, now: datetime, rng: random.Random, k: int) -> list[Cargo]:
    """Up to ``k`` distinct cargo; each one's weight is 1 + 2 × days it has been ready."""
    pool, chosen = list(cargos), []
    while pool and len(chosen) < k:
        weights = [1 + 2 * max(0.0, (now - c.ready_at).total_seconds()) / 86400 for c in pool]
        chosen.append(pool.pop(rng.choices(range(len(pool)), weights=weights)[0]))
    return chosen


def pickup_cargo(ctx: Tick) -> None:
    k = ctx.rng.randint(0, 2)
    if not k:
        return
    ready = Cargo.objects.filter(status=Cargo.Status.READY, ready_at__lte=ctx.now).order_by(
        "ready_at", "id"
    )
    for cargo in pick_weighted(ready.select_related("client"), ctx.now, ctx.rng, k):
        _attempt(ctx, f"Выдача {cargo.article}", lambda c=cargo: _pickup(ctx, c))


def _pickup(ctx: Tick, cargo: Cargo) -> None:
    services.cargo_pickup(cargo, autopilot_user(), paid=True, now=ctx.now)
    message = (
        f"Выдан груз {cargo.article} · {cargo.client.name} ({cargo.client.code}), "
        f"{_places(cargo.places)}"
    )
    truck = Truck.objects.filter(pk=cargo.truck_id).first()
    if truck and truck.status == Truck.Status.CLOSED:
        message += f" · рейс {truck.number} закрыт"
    ctx.log(Kind.PICKED_UP, message, truck=truck, cargo=cargo)
    ctx.counts["picked_up"] += 1


def build_order(ctx: Tick) -> None:
    local_now = timezone.localtime(ctx.now)
    day = local_now.date()
    if local_now.time() < ORDER_TIME or ctx.state.order_checked_on == day:
        return
    ctx.state.order_checked_on = day
    AutopilotState.objects.filter(pk=ctx.state.pk).update(order_checked_on=day)
    if day != timezone.localdate():
        ctx.log(
            Kind.SKIPPED,
            f"Ордер на {day:%d.%m.%Y} пропущен: дата симуляции не совпадает с сегодняшней",
        )
        return
    if DailyOrder.objects.filter(date=day).exists():
        ctx.log(Kind.SKIPPED, f"Ордер на {day:%d.%m.%Y} уже сформирован")
        return
    _attempt(ctx, f"Ордер на {day:%d.%m.%Y}", lambda: _build(ctx, day))


def _build(ctx: Tick, day) -> None:
    _order, added = orders.build_daily_order(day)
    lines = f"{added} {plural(added, 'строка', 'строки', 'строк')}"
    ctx.log(Kind.ORDER_BUILT, f"Сформирован ордер на {day:%d.%m.%Y} ({lines})")
    ctx.counts["orders_built"] += 1


# ---------- tick ----------


def prune_events(keep: int = KEEP_EVENTS) -> None:
    ids = AutopilotEvent.objects.order_by("-id").values_list("id", flat=True)
    cutoff = ids[keep - 1 : keep].first()
    if cutoff is not None:
        AutopilotEvent.objects.filter(id__lt=cutoff).delete()


def resync_clock(state: AutopilotState, real_now: datetime) -> None:
    """Put the simulated clock back to real time (counters stay)."""
    state.sim_clock = real_now
    state.last_tick_at = real_now
    AutopilotState.objects.filter(pk=state.pk).update(sim_clock=real_now, last_tick_at=real_now)


def _claim(state: AutopilotState, real_now: datetime, elapsed) -> AutopilotState | None:
    """Make this tick the only one for its moment: advance the clock, or return None.

    The row lock serializes ticks on PostgreSQL; SQLite ignores ``select_for_update``,
    so the write is also conditional on ``last_tick_at`` being what this tick read.
    """
    with transaction.atomic():
        locked = AutopilotState.objects.select_for_update().get(pk=state.pk)
        seen = locked.last_tick_at
        # Real-time ticks (polling, --loop) this close together are duplicates; explicit
        # steps («Один шаг», --steps) may follow each other at once.
        if elapsed is None and seen is not None and seen > real_now - MIN_TICK_GAP:
            return None
        advance_clock(locked, real_now, elapsed)
        won = AutopilotState.objects.filter(pk=locked.pk, last_tick_at=seen).update(
            sim_clock=locked.sim_clock, last_tick_at=locked.last_tick_at
        )
        return locked if won else None


def tick(state: AutopilotState, real_now: datetime, rng: random.Random, *, elapsed=None):
    """Claim the tick, apply every rule, save the counters.

    Returns the new events, or ``SKIPPED`` when another tick got this moment first.
    The rules run outside the claim, each action in its own transaction.
    """
    locked = _claim(state, real_now, elapsed)
    if locked is None:
        state.refresh_from_db()
        return SKIPPED
    ctx = Tick(state=locked, now=locked.sim_clock, real_now=real_now, rng=rng)
    rules = (
        ("Прибытие", arrive_trucks),
        ("Отправка", depart_trucks),
        ("Новый рейс", ensure_loading_trucks),
        ("Приём груза", receive_cargo),
        ("Выдача", pickup_cargo),
        ("Ордер", build_order),
    )
    for name, rule in rules:
        try:
            rule(ctx)
        except Exception as exc:  # a broken rule must not stop the others
            logger.exception("autopilot rule %s", name)
            ctx.log(Kind.ERROR, f"{name}: {exc}")
    increments = {name: F(name) + n for name, n in ctx.counts.items() if n}
    if increments:
        AutopilotState.objects.filter(pk=locked.pk).update(**increments)
    prune_events()
    state.refresh_from_db()
    return ctx.events

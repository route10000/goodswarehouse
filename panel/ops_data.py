"""Ops-центр aggregations: KPI, pipeline, fleet, day timeline, warehouse blocks, scene JSON.

Pure read-only queries. Every function takes ``now`` (aware datetime, defaults to
``timezone.now()``); «today» is the local date in ``TIME_ZONE`` (Asia/Vladivostok).
The scene payload is documented in ``docs/SCENE_API.md`` and carries no client data.
"""

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from logistics.models import Cargo, DailyOrder, DailyOrderLine, Truck
from panel.views.common import count_phrase, plural
from public.templatetags.gw_format import ru_date

ROUTE = {"from": "Уссурийск", "to": "Владивосток", "km": 110}
CLOSED_VISIBLE_FOR = timedelta(hours=24)
SCALE_START_HOUR, SCALE_END_HOUR = 6, 22

BLOCKS = (
    # key, caption, max days waiting (inclusive); D has no upper bound.
    ("A", "≤ 2 дн", 2),
    ("B", "3–4 дн", 4),
    ("C", "5–6 дн", 6),
    ("D", "≥ 7 дн", None),
)

CallStatus = DailyOrderLine.CallStatus


# ---------- time helpers ----------


def _now(now: datetime | None) -> datetime:
    return now or timezone.now()


def _day_start(day: Date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def _today_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = _day_start(timezone.localdate(now))
    return start, _day_start(timezone.localdate(now) + timedelta(days=1))


def _iso(value: datetime | None) -> str | None:
    return timezone.localtime(value).isoformat() if value else None


def _hhmm(value: datetime) -> str:
    return f"{timezone.localtime(value):%H:%M}"


def short_time(value: datetime | None, now: datetime) -> str:
    """``15:40`` for today, ``17.10 15:40`` otherwise (local time)."""
    if not value:
        return ""
    local = timezone.localtime(value)
    if local.date() == timezone.localdate(now):
        return f"{local:%H:%M}"
    return f"{local:%d.%m %H:%M}"


def duration_text(minutes: int) -> str:
    """``45 мин``, ``2 ч 05 мин``, ``3 дн``."""
    minutes = max(0, minutes)
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 24 * 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} ч {rest:02d} мин" if rest else f"{hours} ч"
    return f"{minutes // (24 * 60)} дн"


# ---------- KPI ----------


def _today_order(now: datetime) -> DailyOrder | None:
    return DailyOrder.objects.filter(date=timezone.localdate(now)).first()


def _not_picked_up(lines):
    return lines.exclude(cargo__status=Cargo.Status.PICKED_UP)


def kpis(now: datetime | None = None) -> dict:
    """The four KPI tiles. Order «pending» skips lines whose cargo was already picked up."""
    now = _now(now)
    start, end = _today_bounds(now)
    ready = Cargo.objects.filter(status=Cargo.Status.READY).aggregate(
        places=Coalesce(Sum("places"), 0), cargo=Count("id")
    )
    became_ready = Cargo.objects.filter(
        status__in=[Cargo.Status.READY, Cargo.Status.PICKED_UP],
        ready_at__gte=start,
        ready_at__lt=end,
    ).aggregate(places=Coalesce(Sum("places"), 0))
    trucks = Truck.objects.exclude(status=Truck.Status.CLOSED).aggregate(
        active=Count("id"), in_transit=Count("id", filter=Q(status=Truck.Status.IN_TRANSIT))
    )
    picked = Cargo.objects.filter(
        status=Cargo.Status.PICKED_UP, picked_up_at__gte=start, picked_up_at__lt=end
    ).aggregate(cargo=Count("id"), places=Coalesce(Sum("places"), 0))
    order = _today_order(now)
    pending = total = 0
    if order is not None:
        total = order.lines.count()
        pending = _not_picked_up(order.lines.filter(call_status=CallStatus.PENDING)).count()
    return {
        "awaiting_places": ready["places"],
        "awaiting_cargo": ready["cargo"],
        "awaiting_delta": became_ready["places"],
        "trucks_in_transit": trucks["in_transit"],
        "trucks_active": trucks["active"],
        "order_built": order is not None,
        "order_pending": pending,
        "order_total": total,
        "picked_up_today": picked["cargo"],
        "picked_up_places": picked["places"],
    }


# ---------- pipeline ----------


def places_unit(n: int) -> str:
    return plural(n, "место", "места", "мест")


def cargo_unit(n: int) -> str:
    return plural(n, "груз", "груза", "грузов")


def _trucks_with_places(status: str) -> dict:
    return Truck.objects.filter(status=status).aggregate(
        trucks=Count("id", distinct=True), places=Coalesce(Sum("cargos__places"), 0)
    )


def pipeline(now: datetime | None = None) -> list[dict]:
    """Five stages from Уссурийск to pickup; ``value`` is the number shown on the right."""
    now = _now(now)
    numbers = kpis(now)
    loading = _trucks_with_places(Truck.Status.LOADING)
    transit = _trucks_with_places(Truck.Status.IN_TRANSIT)
    order = _today_order(now)
    if order is None:
        to_call, order_caption = 0, "Ордер ещё не сформирован"
    else:
        open_lines = _not_picked_up(
            order.lines.filter(call_status__in=[CallStatus.PENDING, CallStatus.NO_ANSWER])
        )
        to_call = open_lines.count()
        no_answer = open_lines.filter(call_status=CallStatus.NO_ANSWER).count()
        order_caption = f"не дозвонились: {no_answer}"

    def trucks_caption(n):
        return count_phrase(n, "машина", "машины", "машин")

    return [
        {
            "key": "loading",
            "label": "Грузятся в Уссурийске",
            "caption": trucks_caption(loading["trucks"]),
            "value": loading["places"],
            "unit": places_unit(loading["places"]),
        },
        {
            "key": "in_transit",
            "label": "В пути",
            "caption": trucks_caption(transit["trucks"]),
            "value": transit["places"],
            "unit": places_unit(transit["places"]),
        },
        {
            "key": "ready",
            "label": "На складе, ждут выдачи",
            "caption": count_phrase(numbers["awaiting_cargo"], "груз", "груза", "грузов"),
            "value": numbers["awaiting_places"],
            "unit": places_unit(numbers["awaiting_places"]),
        },
        {
            "key": "order",
            "label": "В ордере на звонок",
            "caption": order_caption,
            "value": to_call,
            "unit": cargo_unit(to_call),
        },
        {
            "key": "picked_up",
            "label": "Выдано сегодня",
            "caption": count_phrase(numbers["picked_up_places"], "место", "места", "мест"),
            "value": numbers["picked_up_today"],
            "unit": cargo_unit(numbers["picked_up_today"]),
        },
    ]


# ---------- progress ----------


def truck_progress(
    status: str,
    *,
    now: datetime,
    departed_at: datetime | None,
    eta: datetime | None,
    places_loaded: int,
    places_total: int,
) -> float:
    """0..1. Loading: share of places loaded; in transit: share of departed_at → eta elapsed."""
    if status == Truck.Status.LOADING:
        return round(places_loaded / places_total, 4) if places_total else 0.0
    if status == Truck.Status.IN_TRANSIT:
        if departed_at is None or eta is None:
            return 0.0
        trip = (eta - departed_at).total_seconds()
        if trip <= 0:
            return 1.0
        share = (now - departed_at).total_seconds() / trip
        return round(min(1.0, max(0.0, share)), 4)
    return 1.0


# ---------- fleet ----------

STATUS_RANK = {
    Truck.Status.ARRIVED: 0,
    Truck.Status.IN_TRANSIT: 1,
    Truck.Status.LOADING: 2,
    Truck.Status.CLOSED: 3,
}


@dataclass
class FleetItem:
    truck: Truck
    now: datetime
    places_total: int
    places_loaded: int
    places_ready: int
    places_picked: int
    clients: int
    weight_kg: Decimal
    last_activity: datetime | None
    progress: float

    @property
    def id(self) -> int:
        return self.truck.pk

    @property
    def number(self) -> str:
        return self.truck.number

    @property
    def short_number(self) -> str:
        """``R-2026-041`` → ``R-041``."""
        head, _, tail = self.truck.number.rpartition("-")
        return f"{head.split('-')[0]}-{tail}" if head else self.truck.number

    @property
    def status(self) -> str:
        return self.truck.status

    @property
    def status_label(self) -> str:
        return self.truck.get_status_display()

    @property
    def progress_pct(self) -> int:
        return round(self.progress * 100)

    @property
    def minutes_left(self) -> int | None:
        if self.status != Truck.Status.IN_TRANSIT or self.truck.eta is None:
            return None
        return int((self.truck.eta - self.now).total_seconds() // 60)

    @property
    def is_late(self) -> bool:
        return self.minutes_left is not None and self.minutes_left < 0

    @property
    def places_text(self) -> str:
        if self.status == Truck.Status.LOADING:
            return f"{self.places_loaded}/{self.places_total} мест"
        return f"{self.places_total} мест"

    @property
    def eta_text(self) -> str:
        return f"ETA {short_time(self.truck.eta, self.now)}" if self.truck.eta else "ETA —"

    @property
    def when_text(self) -> str:
        truck = self.truck
        if self.status == Truck.Status.IN_TRANSIT:
            left = self.minutes_left
            if left is None:
                return "ETA не указано"
            if left < 0:
                return f"опаздывает {duration_text(-left)}"
            return f"через {duration_text(left)}"
        if self.status == Truck.Status.LOADING:
            if truck.departure_date:
                return f"отправка {ru_date(truck.departure_date)}"
            return "дата отправки не указана"
        if self.status == Truck.Status.ARRIVED:
            return f"прибыла {short_time(truck.arrived_at, self.now)}".strip()
        return "закрыта"

    @property
    def left_text(self) -> str:
        """Tracker trip card: «осталось 45 мин» on the way, otherwise ``when_text``."""
        left = self.minutes_left
        if left is not None and left >= 0:
            return f"осталось {duration_text(left)}"
        text = self.when_text
        return text[:1].upper() + text[1:]


def _annotated_trucks():
    loaded = ~Q(cargos__status=Cargo.Status.RECEIVED)
    return Truck.objects.annotate(
        places_total=Coalesce(Sum("cargos__places"), 0),
        places_loaded=Coalesce(Sum("cargos__places", filter=loaded), 0),
        places_ready=Coalesce(Sum("cargos__places", filter=Q(cargos__status="ready")), 0),
        places_picked=Coalesce(Sum("cargos__places", filter=Q(cargos__status="picked_up")), 0),
        clients_total=Count("cargos__client", distinct=True),
        weight_total=Sum("cargos__weight_kg"),
        last_pickup=Max("cargos__picked_up_at"),
    )


def _item(truck: Truck, now: datetime) -> FleetItem:
    return FleetItem(
        truck=truck,
        now=now,
        places_total=truck.places_total,
        places_loaded=truck.places_loaded,
        places_ready=truck.places_ready,
        places_picked=truck.places_picked,
        clients=truck.clients_total,
        weight_kg=truck.weight_total or Decimal("0"),
        last_activity=truck.last_pickup or truck.arrived_at,
        progress=truck_progress(
            truck.status,
            now=now,
            departed_at=truck.departed_at,
            eta=truck.eta,
            places_loaded=truck.places_loaded,
            places_total=truck.places_total,
        ),
    )


def _sort_key(item: FleetItem):
    truck = item.truck
    if item.status == Truck.Status.ARRIVED:
        # Latest arrival first.
        key = -(truck.arrived_at.timestamp() if truck.arrived_at else 0)
    elif item.status == Truck.Status.IN_TRANSIT:
        key = truck.eta.timestamp() if truck.eta else float("inf")
    elif item.status == Truck.Status.LOADING:
        key = (truck.departure_date or Date.max).toordinal()
    else:
        key = -(item.last_activity.timestamp() if item.last_activity else 0)
    return (STATUS_RANK[item.status], key, truck.pk)


def fleet(now: datetime | None = None) -> list[FleetItem]:
    """Non-closed trucks plus those closed within 24 h, most relevant first.

    Trucks have no ``closed_at``: a closed truck's last activity is its latest pickup,
    or its arrival when nothing was picked up (closed by hand).
    """
    now = _now(now)
    items = []
    for truck in _annotated_trucks():
        item = _item(truck, now)
        if item.status == Truck.Status.CLOSED and (
            item.last_activity is None or item.last_activity < now - CLOSED_VISIBLE_FOR
        ):
            continue
        items.append(item)
    return sorted(items, key=_sort_key)


def fleet_item(pk: int, now: datetime | None = None) -> FleetItem | None:
    truck = _annotated_trucks().filter(pk=pk).first()
    return _item(truck, _now(now)) if truck else None


def default_truck(items: list[FleetItem]) -> FleetItem | None:
    """Nearest to arrival: the in-transit truck with the earliest ETA, else the first one."""
    transit = [i for i in items if i.status == Truck.Status.IN_TRANSIT]
    if transit:
        return transit[0]
    return items[0] if items else None


# ---------- journey (drawer + tracker) ----------


def journey(item: FleetItem, now: datetime | None = None) -> list[dict]:
    """Five steps Погрузка → Отправлена → В пути → Разгрузка → На складе with states."""
    now = _now(now)
    truck = item.truck
    current = {
        Truck.Status.LOADING: 0,
        Truck.Status.IN_TRANSIT: 2,
        Truck.Status.ARRIVED: 4,
    }.get(truck.status, 5)

    if truck.departed_at:
        depart_caption = short_time(truck.departed_at, now)
        depart_text = "Выехала из Уссурийска"
    elif truck.departure_date:
        depart_caption = f"план {truck.departure_date:%d.%m}"
        depart_text = "Плановая отправка"
    else:
        depart_caption, depart_text = "—", "Дата отправки не указана"

    if truck.status == Truck.Status.IN_TRANSIT and item.minutes_left is not None:
        transit_text = item.when_text.capitalize()
    else:
        transit_text = f"≈ {ROUTE['km']} км до склада"

    waiting = item.places_ready
    if truck.status in (Truck.Status.ARRIVED, Truck.Status.CLOSED):
        stock_caption = f"{waiting} мест ждут" if waiting else "всё выдано"
    else:
        stock_caption = "—"

    steps = [
        {
            "label": "Погрузка",
            "caption": f"{item.places_loaded}/{item.places_total} мест",
            "time": short_time(truck.loading_starts, now),
            "text": "Уссурийск · погрузка",
        },
        {
            "label": "Отправлена",
            "caption": depart_caption,
            "time": short_time(truck.departed_at, now),
            "text": depart_text,
        },
        {
            "label": "В пути",
            "caption": item.eta_text,
            "time": short_time(truck.eta, now),
            "text": transit_text,
        },
        {
            "label": "Разгрузка",
            "caption": short_time(truck.arrived_at, now) or "—",
            "time": short_time(truck.arrived_at, now),
            "text": "Склад · Владивосток",
        },
        {
            "label": "На складе",
            "caption": stock_caption,
            "time": "",
            "text": f"Выдано {item.places_picked} из {item.places_total} мест",
        },
    ]
    for index, step in enumerate(steps):
        step["number"] = index + 1
        if index < current:
            step["state"] = "done"
        elif index == current:
            step["state"] = "current"
        else:
            step["state"] = "future"
    return steps


# ---------- day timeline ----------


def _pct(value: datetime, window: tuple[datetime, datetime]) -> float:
    start, end = window
    share = (value - start).total_seconds() / (end - start).total_seconds()
    return round(min(100.0, max(0.0, share * 100)), 2)


def _bar(kind, start, end, window, day, title, **flags) -> dict | None:
    """A bar clamped to the hour scale; ``None`` when it misses the local day entirely."""
    day_start, day_end = day
    if end <= day_start or start >= day_end:
        return None
    left = _pct(start, window)
    return {
        "kind": kind,
        "left": left,
        "width": round(_pct(end, window) - left, 2),
        "title": title,
        "late": flags.get("late", False),
        "planned": flags.get("planned", False),
    }


def _marker(kind, at, window, day, title, planned=False) -> dict | None:
    day_start, day_end = day
    if not day_start <= at < day_end:
        return None
    return {
        "kind": kind,
        "left": _pct(at, window),
        "width": 0.0,
        "title": title,
        "late": False,
        "planned": planned,
    }


def _truck_events(item: FleetItem, today: Date, day) -> list[dict]:
    truck = item.truck
    day_start, day_end = day
    events = []

    def add(kind, at, label):
        events.append(
            {
                "kind": kind,
                "truck_id": truck.pk,
                "number": truck.number,
                "time": _hhmm(at) if at else None,
                "at": at,
                "label": label,
            }
        )

    if truck.status == Truck.Status.LOADING and truck.departure_date == today:
        add("departure_planned", None, "Плановая отправка сегодня")
    if truck.departed_at and day_start <= truck.departed_at < day_end:
        add("departed", truck.departed_at, "Отправлена")
    if truck.status == Truck.Status.IN_TRANSIT and truck.eta and day_start <= truck.eta < day_end:
        add("eta", truck.eta, "Ожидаемое прибытие")
    if truck.arrived_at and day_start <= truck.arrived_at < day_end:
        add("arrived", truck.arrived_at, "Прибыла")
    return events


def _truck_bars(item: FleetItem, now: datetime, window, day) -> list[dict]:
    truck = item.truck
    bars = []
    if truck.loading_starts:
        end = truck.departed_at or (now if truck.status == Truck.Status.LOADING else None)
        if end and end > truck.loading_starts:
            title = f"{item.short_number} · погрузка с {_hhmm(truck.loading_starts)}"
            bars.append(_bar("loading", truck.loading_starts, end, window, day, title))
    if truck.departed_at:
        late = bool(
            truck.status == Truck.Status.IN_TRANSIT and truck.eta is not None and truck.eta < now
        )
        if truck.arrived_at:
            end = truck.arrived_at
        elif truck.eta and not late:
            end = truck.eta
        else:
            end = now
        if end > truck.departed_at:
            title = f"{item.short_number} · в пути с {_hhmm(truck.departed_at)}"
            bars.append(_bar("transit", truck.departed_at, end, window, day, title, late=late))
    if truck.arrived_at:
        title = f"{item.short_number} · прибыла {_hhmm(truck.arrived_at)}"
        bars.append(_marker("arrival", truck.arrived_at, window, day, title))
    elif truck.status == Truck.Status.IN_TRANSIT and truck.eta:
        title = f"{item.short_number} · ETA {_hhmm(truck.eta)}"
        bars.append(_marker("arrival", truck.eta, window, day, title, planned=True))
    return [bar for bar in bars if bar]


def day_timeline(now: datetime | None = None, items: list[FleetItem] | None = None) -> dict:
    """Today's departures, ETAs and arrivals on a 06:00–22:00 local scale (percent offsets)."""
    now = _now(now)
    today = timezone.localdate(now)
    day = _today_bounds(now)
    window = (
        day[0] + timedelta(hours=SCALE_START_HOUR),
        day[0] + timedelta(hours=SCALE_END_HOUR),
    )
    items = fleet(now) if items is None else items
    hours = [
        {"label": f"{hour:02d}:00", "pct": _pct(day[0] + timedelta(hours=hour), window)}
        for hour in range(SCALE_START_HOUR, SCALE_END_HOUR + 1, 2)
    ]
    rows, events = [], []
    for item in items:
        truck_events = _truck_events(item, today, day)
        bars = _truck_bars(item, now, window, day)
        if not bars and not truck_events:
            continue
        planned_today = any(e["kind"] == "departure_planned" for e in truck_events)
        rows.append({"item": item, "bars": bars, "planned_departure": planned_today})
        events.extend(truck_events)
    events.sort(key=lambda e: (e["at"] is None, e["at"] or now, e["truck_id"]))
    for event in events:
        del event["at"]
    now_pct = _pct(now, window) if window[0] <= now <= window[1] else None
    return {"hours": hours, "now_pct": now_pct, "rows": rows, "events": events}


# ---------- warehouse blocks ----------


def block_key(days_waiting: int) -> str:
    for key, _caption, max_days in BLOCKS:
        if max_days is None or days_waiting <= max_days:
            return key
    raise AssertionError("unreachable")


def blocks(now: datetime | None = None) -> list[dict]:
    """Ready cargo by local days on the warehouse: A ≤ 2, B 3–4, C 5–6, D ≥ 7."""
    now = _now(now)
    today = timezone.localdate(now)
    capacity = settings.WAREHOUSE_BLOCK_CAPACITY
    totals = {key: {"places": 0, "cargo": 0} for key, _caption, _max in BLOCKS}
    ready = Cargo.objects.filter(status=Cargo.Status.READY).values_list("places", "ready_at")
    for places, ready_at in ready:
        days = (today - timezone.localdate(ready_at)).days if ready_at else 0
        bucket = totals[block_key(max(0, days))]
        bucket["places"] += places
        bucket["cargo"] += 1
    result = []
    for key, caption, _max in BLOCKS:
        places = totals[key]["places"]
        fill = min(1.0, round(places / capacity, 4)) if capacity > 0 else 0.0
        result.append(
            {
                "key": key,
                "label": f"Блок {key}",
                "caption": caption,
                "places": places,
                "cargo": totals[key]["cargo"],
                "fill": fill,
            }
        )
    return result


# ---------- scene JSON ----------


def scene_truck(item: FleetItem) -> dict:
    truck = item.truck
    return {
        "id": truck.pk,
        "number": truck.number,
        "status": truck.status,
        "status_label": truck.get_status_display(),
        "progress": item.progress,
        "places": item.places_total,
        "places_loaded": item.places_loaded,
        "clients": item.clients,
        "weight_kg": float(item.weight_kg),
        "departed_at": _iso(truck.departed_at),
        "eta": _iso(truck.eta),
        "arrived_at": _iso(truck.arrived_at),
        "url": reverse("panel:truck", args=[truck.pk]),
    }


def scene(now: datetime | None = None, items: list[FleetItem] | None = None) -> dict:
    """``GET /panel/api/scene/`` payload (docs/SCENE_API.md)."""
    now = _now(now)
    items = fleet(now) if items is None else items
    return {
        "generated_at": _iso(now),
        "route": dict(ROUTE),
        "trucks": [scene_truck(item) for item in items],
        "blocks": blocks(now),
        "summary": kpis(now),
    }

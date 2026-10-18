"""Public site (SPEC §9): home, schedule, cargo tracking, contacts. Read-only."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from logistics.models import Cargo, Client, Truck

from .forms import TrackForm

ARRIVED_VISIBLE_DAYS = 3
PICKED_UP_VISIBLE_DAYS = 14
LOADING_PROGRESS = 0.08
TRACK_RATE_LIMIT = 10
TRACK_RATE_WINDOW = 60  # seconds
HOME_TRUCKS = 5

NOT_FOUND = "Не нашли груз. Проверьте код или артикул и цифры телефона."
TOO_MANY = "Слишком много попыток. Подождите минуту."

STATUS_ORDER = {Truck.Status.LOADING: 0, Truck.Status.IN_TRANSIT: 1, Truck.Status.ARRIVED: 2}
TRUCK_PILL = {
    Truck.Status.LOADING: "st-loading",
    Truck.Status.IN_TRANSIT: "st-transit",
    Truck.Status.ARRIVED: "st-ready",
}
CARGO_PILL = {
    Cargo.Status.RECEIVED: "st-loading",
    Cargo.Status.LOADED: "st-loading",
    Cargo.Status.IN_TRANSIT: "st-transit",
    Cargo.Status.READY: "st-ready",
    Cargo.Status.PICKED_UP: "st-done",
}
CARGO_ORDER = {
    Cargo.Status.READY: 0,
    Cargo.Status.IN_TRANSIT: 1,
    Cargo.Status.LOADED: 2,
    Cargo.Status.RECEIVED: 3,
    Cargo.Status.PICKED_UP: 4,
}
CODE_RE = re.compile(r"^GW-?(\d{1,8})$", re.IGNORECASE)


# ---------- shared context ----------


def _bot_url() -> str:
    username = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{username}" if username else ""


def _site_context() -> dict:
    phone = settings.WAREHOUSE_PHONE
    address = settings.WAREHOUSE_ADDRESS
    city = settings.WAREHOUSE_CITY
    return {
        "bot_url": _bot_url(),
        "free_storage_days": settings.FREE_STORAGE_DAYS,
        "warehouse": {
            "city": city,
            "address": address,
            "hours": settings.WAREHOUSE_HOURS,
            "phone": phone,
            "phone_href": re.sub(r"[^\d+]", "", phone),
            "map_query": ", ".join(part for part in (city, address) if part),
        },
    }


def _render(request, template, context=None, status=200):
    return render(request, template, {**_site_context(), **(context or {})}, status=status)


# ---------- schedule ----------


@dataclass
class TruckRow:
    truck: Truck
    progress: float
    pill: str

    @property
    def percent(self) -> int:
        return round(self.progress * 100)


def truck_progress(truck: Truck, now: datetime) -> float:
    if truck.status == Truck.Status.ARRIVED:
        return 1.0
    if truck.status == Truck.Status.IN_TRANSIT:
        if not truck.departed_at or not truck.eta:
            return 0.5
        total = (truck.eta - truck.departed_at).total_seconds()
        if total <= 0:
            return 1.0
        elapsed = (now - truck.departed_at).total_seconds()
        return min(max(elapsed / total, 0.0), 1.0)
    return LOADING_PROGRESS


def _sort_key(truck: Truck):
    if truck.status == Truck.Status.LOADING:
        when = truck.departure_date.toordinal() if truck.departure_date else float("inf")
    elif truck.status == Truck.Status.IN_TRANSIT:
        when = truck.eta.timestamp() if truck.eta else float("inf")
    else:  # arrived: most recent first
        when = -truck.arrived_at.timestamp() if truck.arrived_at else float("inf")
    return (STATUS_ORDER[truck.status], when, truck.created_at.timestamp())


def schedule_rows(now: datetime, limit: int | None = None) -> list[TruckRow]:
    """Public trucks: loading, in transit, and arrived within the last 3 days."""
    trucks = Truck.objects.filter(is_public=True).filter(
        Q(status__in=[Truck.Status.LOADING, Truck.Status.IN_TRANSIT])
        | Q(
            status=Truck.Status.ARRIVED,
            arrived_at__gte=now - timedelta(days=ARRIVED_VISIBLE_DAYS),
        )
    )
    ordered = sorted(trucks, key=_sort_key)
    if limit is not None:
        ordered = ordered[:limit]
    return [TruckRow(t, truck_progress(t, now), TRUCK_PILL[t.status]) for t in ordered]


@dataclass
class RouteSummary:
    """Hero widget «Сейчас на маршруте»: public trucks only, no client data."""

    loading: int
    in_transit: int
    nearest_eta: datetime | None
    dots: list[TruckRow]


def route_summary(now: datetime) -> RouteSummary:
    trucks = list(
        Truck.objects.filter(
            is_public=True, status__in=[Truck.Status.LOADING, Truck.Status.IN_TRANSIT]
        )
    )
    moving = sorted((t for t in trucks if t.status == Truck.Status.IN_TRANSIT), key=_sort_key)
    upcoming = [t.eta for t in moving if t.eta and t.eta >= now]
    return RouteSummary(
        loading=sum(t.status == Truck.Status.LOADING for t in trucks),
        in_transit=len(moving),
        nearest_eta=min(upcoming, default=None),
        dots=[TruckRow(t, truck_progress(t, now), TRUCK_PILL[t.status]) for t in moving],
    )


# ---------- track ----------


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _rate_limited(request) -> bool:
    """Fixed one-minute window per IP; True once the limit is exceeded."""
    key = f"public:track:{_client_ip(request)}"
    cache.add(key, 0, TRACK_RATE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, TRACK_RATE_WINDOW)
        count = 1
    return count > TRACK_RATE_LIMIT


def find_client(query: str, last4: str) -> Client | None:
    """Client code (``GW-0142``, ``gw0142``) or cargo article, plus the phone's last 4 digits."""
    match = CODE_RE.match(query)
    if match:
        code = f"GW-{int(match.group(1)):04d}"
        client = Client.objects.filter(code=code).first()
        if client and client.phone.endswith(last4):
            return client
    cargo = (
        Cargo.objects.filter(article__iexact=query, client__phone__endswith=last4)
        .select_related("client")
        .order_by("client_id")
        .first()
    )
    return cargo.client if cargo else None


@dataclass
class CargoRow:
    cargo: Cargo
    pill: str


def visible_cargo(client: Client, now: datetime) -> list[CargoRow]:
    since = now - timedelta(days=PICKED_UP_VISIBLE_DAYS)
    cargos = (
        client.cargos.filter(~Q(status=Cargo.Status.PICKED_UP) | Q(picked_up_at__gte=since))
        .select_related("truck")
        .order_by("-created_at", "-pk")
    )
    # Most actionable first: ready to pick up, then on the way, then picked up.
    ordered = sorted(cargos, key=lambda c: CARGO_ORDER[c.status])
    return [CargoRow(c, CARGO_PILL[c.status]) for c in ordered]


def _first_name(client: Client) -> str:
    parts = client.name.split()
    return parts[0] if parts else ""


# ---------- views ----------


def home(request):
    now = timezone.now()
    context = {
        "trucks": schedule_rows(now, limit=HOME_TRUCKS),
        "route": route_summary(now),
        "form": TrackForm(),
    }
    return _render(request, "public/home.html", context)


def schedule(request):
    return _render(request, "public/schedule.html", {"trucks": schedule_rows(timezone.now())})


def track(request):
    if request.method != "POST":
        return _render(request, "public/track.html", {"form": TrackForm()})

    form = TrackForm(request.POST)
    if _rate_limited(request):
        return _render(request, "public/track.html", {"form": form, "error": TOO_MANY}, status=429)

    client = None
    if form.is_valid():
        client = find_client(form.cleaned_data["query"], form.cleaned_data["last4"])
    if client is None:
        return _render(request, "public/track.html", {"form": form, "error": NOT_FOUND})

    return _render(
        request,
        "public/track.html",
        {
            "form": form,
            "found": True,
            "first_name": _first_name(client),
            "cargos": visible_cargo(client, timezone.now()),
        },
    )


def contacts(request):
    return _render(request, "public/contacts.html")

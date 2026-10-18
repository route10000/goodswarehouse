"""Home chapter «04 Цифры»: aggregate platform numbers. Counts only, no personal data."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, DurationField, ExpressionWrapper, F, Sum

from logistics.models import Cargo, Client, Truck

STATS_WINDOW_DAYS = 30
SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class PlatformStats:
    trips_30d: int
    places_picked_30d: int
    avg_storage_days: float
    telegram_clients: int


def platform_stats(now: datetime) -> PlatformStats:
    """Trips departed, places picked up and average storage (ready → picked up, days,
    1 decimal) over the last 30 days; clients with Telegram linked. Empty DB → zeros."""
    since = now - timedelta(days=STATS_WINDOW_DAYS)
    picked = Cargo.objects.filter(picked_up_at__gte=since, picked_up_at__lte=now)
    storage = picked.filter(ready_at__isnull=False).aggregate(
        avg=Avg(ExpressionWrapper(F("picked_up_at") - F("ready_at"), output_field=DurationField()))
    )["avg"]
    avg_days = 0.0
    if storage is not None:
        days = Decimal(storage.total_seconds()) / SECONDS_PER_DAY
        avg_days = float(days.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return PlatformStats(
        trips_30d=Truck.objects.filter(departed_at__gte=since, departed_at__lte=now).count(),
        places_picked_30d=picked.aggregate(total=Sum("places"))["total"] or 0,
        avg_storage_days=avg_days,
        telegram_clients=Client.objects.filter(telegram_chat_id__isnull=False).count(),
    )

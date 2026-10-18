"""«Аналитика» aggregations (DESIGN v1.0-final «Графики (Аналитика)»). Read-only.

A period is the last 7, 30 or 90 local (Asia/Vladivostok) days ending today, compared with
the equal period right before it. Every chart is a dict with the same shape::

    {"id", "title", "type", "labels", "titles", "series": [{"key", "label", "color" | "colors",
     "data"}], "empty", "table": {"columns": [{"label", "num"}], "rows": [[str, ...]]}}

Colours are token names (``chart-1``, ``age-a``, ``black``); the page script reads the CSS
variables. Tables hold display strings; ``chart_json`` drops them for the embedded JSON.
No phones leave this module.
"""

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from logistics.models import Cargo, ContactAttempt, Truck
from panel import ops_data
from panel.views.common import plural
from public.templatetags.gw_format import ru_date, ru_datetime, ru_number

PERIODS = (7, 30, 90)
DEFAULT_PERIOD = 30
PERIOD_LABELS = {7: "7 дней", 30: "30 дней", 90: "Квартал"}
COMPARE_CAPTIONS = {7: "к прошлым 7 дням", 30: "к прошлым 30 дням", 90: "к прошлому кварталу"}
LOW_FILL_PCT = 60
TOP_CLIENTS = 10
SECONDS_PER_DAY = 86400
DASH = "—"

Outcome = ContactAttempt.Outcome


# ---------- period ----------


def parse_period(value) -> int:
    """``?period=`` → 7, 30 or 90; anything else is the default 30."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PERIOD
    return days if days in PERIODS else DEFAULT_PERIOD


def _midnight(day: Date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


@dataclass(frozen=True)
class Period:
    days: int
    dates: tuple[Date, ...]

    @property
    def start(self) -> datetime:
        return _midnight(self.dates[0])

    @property
    def end(self) -> datetime:
        return _midnight(self.dates[-1] + timedelta(days=1))

    @property
    def prev_start(self) -> datetime:
        return _midnight(self.dates[0] - timedelta(days=self.days))

    def window(self, field: str) -> Q:
        return Q(**{f"{field}__gte": self.start, f"{field}__lt": self.end})

    def prev_window(self, field: str) -> Q:
        return Q(**{f"{field}__gte": self.prev_start, f"{field}__lt": self.start})


def period(days: int, now: datetime | None = None) -> Period:
    today = timezone.localdate(now or timezone.now())
    first = today - timedelta(days=days - 1)
    return Period(days=days, dates=tuple(first + timedelta(days=i) for i in range(days)))


# ---------- formatting ----------


def _round(value, places: str = "1") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _pct(part: int, whole: int) -> int | None:
    return int(_round(Decimal(part) * 100 / whole)) if whole else None


def _days(duration: timedelta | None) -> float | None:
    if duration is None:
        return None
    return float(_round(Decimal(duration.total_seconds()) / SECONDS_PER_DAY, "0.1"))


def _num(value) -> str:
    if value is None:
        return DASH
    # Floats go through str() so 2.4 stays «2,4», not the binary 2.3999….
    return ru_number(Decimal(str(value)) if isinstance(value, float) else value)


def _day_label(day: Date) -> str:
    return f"{day:%d.%m}"


def _columns(*spec) -> list[dict]:
    """``("Дата", False), ("Мест", True)`` → table columns; ``num`` aligns right."""
    return [{"label": label, "num": num} for label, num in spec]


def _chart(chart_id, title, chart_type, labels, series, empty, table, **extra) -> dict:
    # Indexes of right-aligned (numeric) columns, for the template.
    table["num"] = [index for index, column in enumerate(table["columns"]) if column["num"]]
    return {
        "id": chart_id,
        "title": title,
        "type": chart_type,
        "labels": labels,
        "titles": extra.pop("titles", labels),
        "series": series,
        "empty": empty,
        "table": table,
        **extra,
    }


def _per_day(queryset, field: str, p: Period, **sums) -> dict[str, list[int]]:
    """Values per local day of ``field`` inside the period; missing days are 0."""
    rows = (
        queryset.filter(p.window(field))
        .annotate(day=TruncDate(field))
        .values("day")
        .annotate(**sums)
    )
    by_day = {row["day"]: row for row in rows}
    return {key: [by_day.get(day, {}).get(key) or 0 for day in p.dates] for key in sums}


# ---------- KPI ----------


def _delta_display(delta, suffix: str = "") -> str:
    if delta == 0:
        return "без изменений"
    arrow = "↑" if delta > 0 else "↓"
    text = f"{arrow} {_num(abs(delta))}"
    return f"{text} {suffix}" if suffix else text


def _kpi(key, label, value, prev, unit, p: Period, suffix: str = "", none_text: str = "") -> dict:
    delta = None
    if value is not None and prev is not None:
        delta = round(value - prev, 1) if isinstance(value, float) else value - prev
    if delta is not None:
        delta_display = _delta_display(delta, suffix)
    elif value is not None:
        delta_display = "нет данных за прошлый период"
    else:
        delta_display = none_text
    return {
        "key": key,
        "label": label,
        "value": value,
        "display": _num(value),
        "unit": unit,
        "prev": prev,
        "delta": delta,
        "delta_display": delta_display,
        "compare": COMPARE_CAPTIONS[p.days],
    }


def kpis(p: Period) -> list[dict]:
    """Принято / Выдано мест, среднее хранение (ready → picked up, days) and Дозвон %,
    each with the delta to the previous equal period. Three queries."""
    received = Cargo.objects.aggregate(
        cur=Coalesce(Sum("places", filter=p.window("ready_at")), 0),
        prev=Coalesce(Sum("places", filter=p.prev_window("ready_at")), 0),
    )
    storage = ExpressionWrapper(F("picked_up_at") - F("ready_at"), output_field=DurationField())
    has_ready = Q(ready_at__isnull=False)
    picked = Cargo.objects.aggregate(
        cur=Coalesce(Sum("places", filter=p.window("picked_up_at")), 0),
        prev=Coalesce(Sum("places", filter=p.prev_window("picked_up_at")), 0),
        cur_days=Avg(storage, filter=p.window("picked_up_at") & has_ready),
        prev_days=Avg(storage, filter=p.prev_window("picked_up_at") & has_ready),
    )
    calls = ContactAttempt.objects.aggregate(
        cur_ok=Count("id", filter=p.window("created_at") & Q(outcome=Outcome.REACHED)),
        cur_all=Count("id", filter=p.window("created_at")),
        prev_ok=Count("id", filter=p.prev_window("created_at") & Q(outcome=Outcome.REACHED)),
        prev_all=Count("id", filter=p.prev_window("created_at")),
    )
    places_unit = plural(received["cur"], "место", "места", "мест")
    picked_unit = plural(picked["cur"], "место", "места", "мест")
    return [
        _kpi("received", "Принято мест", received["cur"], received["prev"], places_unit, p),
        _kpi("picked", "Выдано мест", picked["cur"], picked["prev"], picked_unit, p),
        _kpi(
            "storage",
            "Среднее хранение",
            _days(picked["cur_days"]),
            _days(picked["prev_days"]),
            "дн",
            p,
            suffix="дн",
            none_text="нет выдач за период",
        ),
        _kpi(
            "calls",
            "Дозвон",
            _pct(calls["cur_ok"], calls["cur_all"]),
            _pct(calls["prev_ok"], calls["prev_all"]),
            "%",
            p,
            suffix="п.п.",
            none_text="нет звонков за период",
        ),
    ]


# ---------- charts ----------


def flow(p: Period) -> dict:
    """Places that arrived (became ready) vs places picked up, per local day."""
    received = _per_day(Cargo.objects, "ready_at", p, places=Sum("places"))["places"]
    picked = _per_day(Cargo.objects, "picked_up_at", p, places=Sum("places"))["places"]
    titles = [ru_date(day) for day in p.dates]
    return _chart(
        "flow",
        "Поток мест по дням",
        "line",
        [_day_label(day) for day in p.dates],
        [
            {"key": "received", "label": "Принято", "color": "chart-1", "data": received},
            {"key": "picked", "label": "Выдано", "color": "chart-2", "data": picked},
        ],
        not any(received) and not any(picked),
        {
            "columns": _columns(("Дата", False), ("Принято, мест", True), ("Выдано, мест", True)),
            "rows": [
                [t, _num(r), _num(k)] for t, r, k in zip(titles, received, picked, strict=True)
            ],
        },
        titles=titles,
        unit="мест",
    )


def stock_by_age(now: datetime | None = None) -> dict:
    """Current ready cargo by warehouse block (days since arrival: A ≤ 2 … D ≥ 7)."""
    blocks = ops_data.blocks(now)
    places = [block["places"] for block in blocks]
    return _chart(
        "stock",
        "На складе по сроку",
        "hbar",
        [f"{block['key']} · {block['caption']}" for block in blocks],
        [
            {
                "key": "places",
                "label": "Мест",
                "colors": [f"age-{block['key'].lower()}" for block in blocks],
                "data": places,
            }
        ],
        not any(places),
        {
            "columns": _columns(("Блок", False), ("Срок", False), ("Мест", True), ("Грузов", True)),
            "rows": [[b["key"], b["caption"], _num(b["places"]), _num(b["cargo"])] for b in blocks],
        },
        unit="мест",
    )


def _trips(p: Period):
    return Truck.objects.filter(p.window("departed_at")).order_by("departed_at", "id")


def truck_fill(p: Period) -> dict:
    """Places / capacity per trip departed in the period; trips under 60 % are flagged."""
    trips = list(
        _trips(p)
        .annotate(places=Coalesce(Sum("cargos__places"), 0))
        .values("number", "departed_at", "capacity_places", "places")
    )
    pcts = [_pct(t["places"], t["capacity_places"]) or 0 for t in trips]
    low = [pct < LOW_FILL_PCT for pct in pcts]
    rows = [
        [
            t["number"],
            ru_date(t["departed_at"]),
            _num(t["places"]),
            _num(t["capacity_places"]),
            _num(pct),
            f"ниже {LOW_FILL_PCT} %" if is_low else "",
        ]
        for t, pct, is_low in zip(trips, pcts, low, strict=True)
    ]
    return _chart(
        "fill",
        "Загрузка рейсов, % вместимости",
        "bar",
        [t["number"] for t in trips],
        [
            {
                "key": "fill",
                "label": "Загрузка",
                "colors": ["age-a" if is_low else "black" for is_low in low],
                "data": pcts,
            }
        ],
        not trips,
        {
            "columns": _columns(
                ("Рейс", False),
                ("Отправлен", False),
                ("Мест", True),
                ("Вместимость", True),
                ("Загрузка, %", True),
                ("Отметка", False),
            ),
            "rows": rows,
        },
        titles=[f"{t['number']} · {ru_date(t['departed_at'])}" for t in trips],
        low=low,
        threshold=LOW_FILL_PCT,
        unit="%",
    )


def calls(p: Period) -> dict:
    """Call results from the ContactAttempt journal per local day (stacked)."""
    counts = _per_day(
        ContactAttempt.objects,
        "created_at",
        p,
        reached=Count("id", filter=Q(outcome=Outcome.REACHED)),
        no_answer=Count("id", filter=Q(outcome=Outcome.NO_ANSWER)),
    )
    reached, no_answer = counts["reached"], counts["no_answer"]
    titles = [ru_date(day) for day in p.dates]
    rows = [
        [title, _num(ok), _num(missed), _num(_pct(ok, ok + missed))]
        for title, ok, missed in zip(titles, reached, no_answer, strict=True)
    ]
    return _chart(
        "calls",
        "Обзвон по дням",
        "stacked",
        [_day_label(day) for day in p.dates],
        [
            {"key": "reached", "label": "Позвонили", "color": "chart-1", "data": reached},
            {"key": "no_answer", "label": "Не дозвонились", "color": "chart-2", "data": no_answer},
        ],
        not any(reached) and not any(no_answer),
        {
            "columns": _columns(
                ("Дата", False), ("Позвонили", True), ("Не дозвонились", True), ("Дозвон, %", True)
            ),
            "rows": rows,
        },
        titles=titles,
        unit="звонков",
    )


def transit(p: Period) -> dict:
    """Hours on the road (departed → arrived) per trip departed in the period."""
    trips = list(
        _trips(p).filter(arrived_at__isnull=False).values("number", "departed_at", "arrived_at")
    )
    hours = [
        float(_round(Decimal((t["arrived_at"] - t["departed_at"]).total_seconds()) / 3600, "0.1"))
        for t in trips
    ]
    rows = [
        [t["number"], ru_datetime(t["departed_at"]), ru_datetime(t["arrived_at"]), _num(h)]
        for t, h in zip(trips, hours, strict=True)
    ]
    return _chart(
        "transit",
        "Время в пути рейса, ч",
        "bar",
        [t["number"] for t in trips],
        [{"key": "hours", "label": "Часов в пути", "color": "black", "data": hours}],
        not trips,
        {
            "columns": _columns(
                ("Рейс", False), ("Отправлен", False), ("Прибыл", False), ("Часов", True)
            ),
            "rows": rows,
        },
        titles=[f"{t['number']} · {ru_date(t['departed_at'])}" for t in trips],
        unit="ч",
    )


def top_clients(p: Period) -> dict:
    """Ten clients with the most places received (became ready) in the period."""
    clients = list(
        Cargo.objects.filter(p.window("ready_at"))
        .values("client_id", "client__name", "client__code")
        .annotate(places=Sum("places"), cargo=Count("id"))
        .order_by("-places", "client__code")[:TOP_CLIENTS]
    )

    def label(row):
        name = row["client__name"].strip()
        return f"{name} · {row['client__code']}" if name else row["client__code"]

    return _chart(
        "top",
        "Топ-10 клиентов по местам",
        "hbar",
        [label(row) for row in clients],
        [
            {
                "key": "places",
                "label": "Мест",
                "color": "black",
                "data": [row["places"] for row in clients],
            }
        ],
        not clients,
        {
            "columns": _columns(
                ("№", True), ("Клиент", False), ("Код", False), ("Мест", True), ("Грузов", True)
            ),
            "rows": [
                [
                    str(index),
                    row["client__name"] or DASH,
                    row["client__code"],
                    _num(row["places"]),
                    _num(row["cargo"]),
                ]
                for index, row in enumerate(clients, start=1)
            ],
        },
        unit="мест",
    )


# ---------- page ----------


def analytics(days: int, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    p = period(days, now)
    return {
        "period": {
            "days": days,
            "label": PERIOD_LABELS[days],
            "range": f"{ru_date(p.dates[0])} — {ru_date(p.dates[-1])}",
            "options": [{"days": d, "label": PERIOD_LABELS[d]} for d in PERIODS],
        },
        "kpis": kpis(p),
        "charts": [
            flow(p),
            stock_by_age(now),
            truck_fill(p),
            calls(p),
            transit(p),
            top_clients(p),
        ],
    }


def chart_json(data: dict) -> list[dict]:
    """Charts for ``json_script``: everything but the server-rendered tables."""
    return [{k: v for k, v in item.items() if k != "table"} for item in data["charts"]]

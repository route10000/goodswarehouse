"""Telegram message texts (HTML parse mode) per DESIGN.md «Сообщения бота».

Every piece of user or settings data is HTML-escaped. Money uses a narrow no-break
space between thousands («8 400 ₽»), weight a decimal comma («12,4 кг»), dates are
Russian («21 октября»), times are local (``TIME_ZONE``, Asia/Vladivostok).
"""

from datetime import date
from decimal import Decimal
from html import escape

from django.conf import settings
from django.utils import timezone

NNBSP = " "

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_money(amount: Decimal) -> str:
    """``8400`` → ``8 400``; ``1600.5`` → ``1 600,50`` (narrow no-break spaces)."""
    amount = Decimal(amount)
    if amount == amount.to_integral_value():
        text = f"{int(amount):,}"
    else:
        text = f"{amount:,.2f}"
    return text.replace(",", NNBSP).replace(".", ",")


def format_weight(weight: Decimal) -> str:
    """``12.40`` → ``12,4``; ``100.00`` → ``100``."""
    text = f"{Decimal(weight).normalize():f}"
    return text.replace(".", ",")


def format_date(value: date) -> str:
    """``2026-10-21`` → ``21 октября``."""
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]}"


def _places(cargo) -> str:
    return f"{cargo.places} {plural_ru(cargo.places, 'место', 'места', 'мест')}"


def _cargo_line(cargo, *, description: bool = False, weight: bool = True) -> str:
    parts = []
    if description and cargo.description:
        parts.append(escape(cargo.description))
    parts.append(_places(cargo))
    if weight and cargo.weight_kg is not None:
        parts.append(f"{format_weight(cargo.weight_kg)} кг")
    return f"• Арт. {escape(cargo.article)} — {', '.join(parts)}"


def _pickup_block(cargos, today: date) -> list[str]:
    """Cargo list, total due, warehouse address/hours and the free storage date."""
    lines = [_cargo_line(c) for c in cargos]
    total = sum((Decimal(c.amount_due) for c in cargos), Decimal("0"))
    lines.append(f"К оплате: {format_money(total)} ₽")
    lines.append("")
    if settings.WAREHOUSE_ADDRESS:
        lines.append(f"📍 {escape(settings.WAREHOUSE_ADDRESS)}")
    if settings.WAREHOUSE_HOURS:
        lines.append(f"🕘 {escape(settings.WAREHOUSE_HOURS)}")
    dates = [c.free_storage_until for c in cargos if c.free_storage_until]
    if dates and min(dates) >= today:
        lines.append(f"Бесплатное хранение до {format_date(min(dates))}.")
    return lines


def _departed(client, cargos, truck, today: date) -> str:
    lines = [
        "🚚 Ваш груз выехал из Уссурийска",
        f"Рейс {escape(truck.number)} → {escape(settings.WAREHOUSE_CITY)}",
    ]
    if truck.eta:
        eta = timezone.localtime(truck.eta)
        delta = (eta.date() - today).days
        day = {0: "сегодня", 1: "завтра"}.get(delta, format_date(eta.date()))
        lines.append(f"Ожидаем на складе: {day} около {eta:%H:%M}")
    lines.append("")
    lines += [_cargo_line(c, description=True, weight=False) for c in cargos]
    lines += ["", "Напишем, когда можно будет забирать."]
    return "\n".join(lines)


def _arrived(client, cargos, truck, today: date) -> str:
    lines = ["📦 Груз на складе во Владивостоке, можно забирать", ""]
    lines += _pickup_block(cargos, today)
    return "\n".join(lines).rstrip()


def _reminder(client, cargos, truck, today: date) -> str:
    days = max(
        ((today - timezone.localdate(c.ready_at)).days for c in cargos if c.ready_at),
        default=0,
    )
    lines = [f"Напоминаем: ваш груз ждёт на складе уже {days} дн.", ""]
    lines += _pickup_block(cargos, today)
    return "\n".join(lines).rstrip()


def _storage_ending(client, cargos, truck, today: date) -> str:
    lines = ["Завтра заканчивается бесплатное хранение груза", ""]
    lines += _pickup_block(cargos, today)
    return "\n".join(lines).rstrip()


RENDERERS = {
    "departed": _departed,
    "arrived": _arrived,
    "reminder": _reminder,
    "storage_ending": _storage_ending,
}


def render(kind: str, client, cargos, truck, *, today: date | None = None) -> str:
    """Build the message text for ``kind`` about ``cargos`` of one client.

    ``today`` (local date, defaults to now) anchors relative words («сегодня»)
    and day counts. ``truck`` may be ``None`` for ``reminder``.
    """
    try:
        renderer = RENDERERS[kind]
    except KeyError:
        raise ValueError(f"No message template for kind {kind!r}") from None
    return renderer(client, list(cargos), truck, today or timezone.localdate())

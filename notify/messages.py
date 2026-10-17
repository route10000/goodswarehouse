"""Telegram message texts (HTML parse mode), roughly per DESIGN.md. T3 polishes them."""

from decimal import Decimal
from html import escape

from django.conf import settings
from django.utils import timezone


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_money(amount: Decimal) -> str:
    """``8400`` → ``8 400``; ``1600.5`` → ``1 600,50``."""
    amount = Decimal(amount)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def format_weight(weight: Decimal) -> str:
    text = f"{Decimal(weight).normalize():f}"
    return text.replace(".", ",")


def _places(cargo) -> str:
    return f"{cargo.places} {plural_ru(cargo.places, 'место', 'места', 'мест')}"


def _departed(client, cargos, truck) -> str:
    lines = [
        "🚚 Ваш груз выехал из Уссурийска",
        f"Рейс {escape(truck.number)} → {escape(settings.WAREHOUSE_CITY)}",
    ]
    if truck.eta:
        eta = timezone.localtime(truck.eta)
        lines.append(f"Ожидаем на складе: {eta:%d.%m} около {eta:%H:%M}")
    lines.append("")
    for cargo in cargos:
        parts = [escape(cargo.description)] if cargo.description else []
        parts.append(_places(cargo))
        lines.append(f"• Арт. {escape(cargo.article)} — {', '.join(parts)}")
    lines += ["", "Напишем, когда можно будет забирать."]
    return "\n".join(lines)


def _arrived(client, cargos, truck) -> str:
    lines = ["📦 Груз на складе во Владивостоке, можно забирать", ""]
    for cargo in cargos:
        parts = [_places(cargo)]
        if cargo.weight_kg is not None:
            parts.append(f"{format_weight(cargo.weight_kg)} кг")
        lines.append(f"• Арт. {escape(cargo.article)} — {', '.join(parts)}")
    total = sum((Decimal(c.amount_due) for c in cargos), Decimal("0"))
    lines.append(f"К оплате: {format_money(total)} ₽")
    lines.append("")
    if settings.WAREHOUSE_ADDRESS:
        lines.append(f"📍 {escape(settings.WAREHOUSE_ADDRESS)}")
    if settings.WAREHOUSE_HOURS:
        lines.append(f"🕘 {escape(settings.WAREHOUSE_HOURS)}")
    dates = [c.free_storage_until for c in cargos if c.free_storage_until]
    if dates:
        lines.append(f"Бесплатное хранение до {min(dates):%d.%m.%Y}.")
    return "\n".join(lines).rstrip()


RENDERERS = {"departed": _departed, "arrived": _arrived}


def render(kind: str, client, cargos, truck) -> str:
    """Build the message text for ``kind`` about ``cargos`` of one client."""
    try:
        renderer = RENDERERS[kind]
    except KeyError:
        raise ValueError(f"No message template for kind {kind!r}") from None
    return renderer(client, list(cargos), truck)

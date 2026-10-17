"""Russian formatting filters: dates «21 октября», money «8 400 ₽», numbers «12,5».

Thousands are separated with a non-breaking space so values never wrap mid-number.
"""

from datetime import date, datetime
from decimal import Decimal

from django import template
from django.utils import timezone

register = template.Library()

NBSP = " "
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


def _local(value: datetime) -> datetime:
    return timezone.localtime(value) if timezone.is_aware(value) else value


@register.filter
def ru_date(value: date | datetime | None) -> str:
    """``21 октября``; aware datetimes are converted to local time first."""
    if not value:
        return ""
    if isinstance(value, datetime):
        value = _local(value)
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]}"


@register.filter
def ru_datetime(value: datetime | None) -> str:
    """``21 октября 15:40`` in local time."""
    if not value:
        return ""
    value = _local(value)
    return f"{ru_date(value)} {value:%H:%M}"


def _group(integer: int) -> str:
    return f"{integer:,}".replace(",", NBSP)


@register.filter
def ru_number(value) -> str:
    """Decimal with trailing zeros trimmed and a comma: ``12,5``, ``1 200``."""
    if value is None or value == "":
        return ""
    number = Decimal(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    integer = int(number)
    fraction = number - integer
    text = _group(integer)
    if fraction:
        digits = f"{fraction.normalize():f}".split(".")[1]
        text = f"{text},{digits}"
    return sign + text


@register.filter
def rub(value) -> str:
    """Money: ``8 400 ₽``; kopecks are shown only when present: ``99,50 ₽``."""
    if value is None or value == "":
        return ""
    number = Decimal(value).quantize(Decimal("0.01"))
    sign = "-" if number < 0 else ""
    number = abs(number)
    integer = int(number)
    kopecks = int((number - integer) * 100)
    text = _group(integer)
    if kopecks:
        text = f"{text},{kopecks:02d}"
    return f"{sign}{text}{NBSP}₽"

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from public.templatetags.gw_format import ru_date, ru_datetime, ru_number, rub

VLAT = ZoneInfo("Asia/Vladivostok")
NBSP = " "


def test_ru_date_from_date():
    assert ru_date(date(2026, 10, 21)) == "21 октября"
    assert ru_date(date(2026, 3, 1)) == "1 марта"


def test_ru_date_uses_local_time_for_datetimes():
    # 20:00 UTC is already the next day in Vladivostok (UTC+10).
    assert ru_date(datetime(2026, 10, 20, 20, 0, tzinfo=UTC)) == "21 октября"


def test_ru_datetime():
    assert ru_datetime(datetime(2026, 10, 21, 15, 40, tzinfo=VLAT)) == "21 октября 15:40"


def test_empty_values():
    assert ru_date(None) == ""
    assert ru_datetime(None) == ""
    assert rub(None) == ""
    assert ru_number(None) == ""


def test_rub_groups_thousands():
    assert rub(Decimal("8400")) == f"8{NBSP}400{NBSP}₽"
    assert rub(Decimal("1250000.00")) == f"1{NBSP}250{NBSP}000{NBSP}₽"
    assert rub(Decimal("0")) == f"0{NBSP}₽"
    assert rub(Decimal("99.50")) == f"99,50{NBSP}₽"


def test_ru_number_trims_zeros():
    assert ru_number(Decimal("12.50")) == "12,5"
    assert ru_number(Decimal("1200.00")) == f"1{NBSP}200"

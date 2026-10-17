from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from notify.messages import format_date, format_money, format_weight, render

VLAT = ZoneInfo("Asia/Vladivostok")
NNBSP = " "  # narrow no-break space, the thousands separator
TODAY = date(2026, 10, 17)


def cargo(
    article,
    places=1,
    weight=None,
    amount="0",
    description="",
    until=None,
    ready_at=None,
):
    return SimpleNamespace(
        article=article,
        places=places,
        weight_kg=Decimal(weight) if weight else None,
        amount_due=Decimal(amount),
        description=description,
        free_storage_until=until,
        ready_at=ready_at,
    )


def truck(eta=None, number="R-2026-041"):
    return SimpleNamespace(number=number, eta=eta)


@pytest.fixture(autouse=True)
def _warehouse(settings):
    settings.WAREHOUSE_ADDRESS = "ул. Складская, 1"
    settings.WAREHOUSE_HOURS = "Пн–Сб 9:00–18:00"
    settings.WAREHOUSE_CITY = "Владивосток"


# --- formatting helpers ---


def test_format_money_uses_narrow_nbsp_thousands():
    assert format_money(Decimal("8400")) == f"8{NNBSP}400"
    assert format_money(Decimal("1234567.5")) == f"1{NNBSP}234{NNBSP}567,50"
    assert format_money(Decimal("999")) == "999"
    assert format_money(Decimal("0")) == "0"
    assert format_money(Decimal("8400.00")) == f"8{NNBSP}400"


def test_format_weight_uses_comma_and_drops_trailing_zeros():
    assert format_weight(Decimal("12.40")) == "12,4"
    assert format_weight(Decimal("100.00")) == "100"
    assert format_weight(Decimal("0.25")) == "0,25"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 21), "21 октября"),
        (date(2026, 1, 1), "1 января"),
        (date(2026, 3, 8), "8 марта"),
        (date(2026, 5, 9), "9 мая"),
        (date(2026, 12, 31), "31 декабря"),
    ],
)
def test_format_date_russian_genitive(value, expected):
    assert format_date(value) == expected


# --- departed ---


def test_departed_text_matches_design():
    eta = datetime(2026, 10, 17, 15, 40, tzinfo=VLAT)
    text = render(
        "departed",
        None,
        [cargo("88123", places=3, description="одежда")],
        truck(eta),
        today=TODAY,
    )
    assert text == (
        "🚚 Ваш груз выехал из Уссурийска\n"
        "Рейс R-2026-041 → Владивосток\n"
        "Ожидаем на складе: сегодня около 15:40\n"
        "\n"
        "• Арт. 88123 — одежда, 3 места\n"
        "\n"
        "Напишем, когда можно будет забирать."
    )


def test_departed_eta_tomorrow_and_later_and_in_local_time():
    # 22:30 UTC on the 17th is 08:30 on the 18th in Vladivostok.
    tomorrow = datetime(2026, 10, 17, 22, 30, tzinfo=UTC)
    text = render("departed", None, [cargo("1")], truck(tomorrow), today=TODAY)
    assert "Ожидаем на складе: завтра около 08:30" in text

    later = datetime(2026, 10, 21, 11, 5, tzinfo=VLAT)
    text = render("departed", None, [cargo("1")], truck(later), today=TODAY)
    assert "Ожидаем на складе: 21 октября около 11:05" in text


def test_departed_without_eta_has_no_eta_line():
    text = render("departed", None, [cargo("1", places=1)], truck(None), today=TODAY)
    assert "Ожидаем" not in text
    assert "• Арт. 1 — 1 место" in text


# --- arrived ---


def test_arrived_text_matches_design():
    text = render(
        "arrived",
        None,
        [cargo("88123", places=3, weight="12.4", amount="8400", until=date(2026, 10, 24))],
        truck(),
        today=TODAY,
    )
    assert text == (
        "📦 Груз на складе во Владивостоке, можно забирать\n"
        "\n"
        "• Арт. 88123 — 3 места, 12,4 кг\n"
        f"К оплате: 8{NNBSP}400 ₽\n"
        "\n"
        "📍 ул. Складская, 1\n"
        "🕘 Пн–Сб 9:00–18:00\n"
        "Бесплатное хранение до 24 октября."
    )


def test_arrived_sums_several_cargo_and_uses_earliest_storage_date():
    text = render(
        "arrived",
        None,
        [
            cargo("A1", places=5, amount="8400", until=date(2026, 10, 25)),
            cargo("A2", places=2, amount="1600.50", until=date(2026, 10, 24)),
        ],
        truck(),
        today=TODAY,
    )
    assert "• Арт. A1 — 5 мест" in text
    assert "• Арт. A2 — 2 места" in text
    assert f"К оплате: 10{NNBSP}000,50 ₽" in text
    assert "Бесплатное хранение до 24 октября." in text


def test_arrived_without_address_settings_skips_those_lines(settings):
    settings.WAREHOUSE_ADDRESS = ""
    settings.WAREHOUSE_HOURS = ""
    text = render("arrived", None, [cargo("A1")], truck(), today=TODAY)
    assert "📍" not in text
    assert "🕘" not in text


# --- reminder ---


def test_reminder_text():
    ready = datetime(2026, 10, 13, 16, 0, tzinfo=VLAT)
    text = render(
        "reminder",
        None,
        [
            cargo(
                "88123",
                places=3,
                weight="12.4",
                amount="8400",
                until=date(2026, 10, 20),
                ready_at=ready,
            )
        ],
        None,
        today=TODAY,
    )
    assert text == (
        "Напоминаем: ваш груз ждёт на складе уже 4 дн.\n"
        "\n"
        "• Арт. 88123 — 3 места, 12,4 кг\n"
        f"К оплате: 8{NNBSP}400 ₽\n"
        "\n"
        "📍 ул. Складская, 1\n"
        "🕘 Пн–Сб 9:00–18:00\n"
        "Бесплатное хранение до 20 октября."
    )


def test_reminder_days_counted_in_local_dates_and_uses_oldest_cargo():
    # 15:30 UTC on the 14th is already the 15th in Vladivostok: 2 days, not 3.
    newer = datetime(2026, 10, 14, 15, 30, tzinfo=UTC)
    older = datetime(2026, 10, 12, 10, 0, tzinfo=VLAT)
    text = render("reminder", None, [cargo("A", ready_at=newer)], None, today=TODAY)
    assert "уже 2 дн." in text
    text = render(
        "reminder",
        None,
        [cargo("A", ready_at=newer), cargo("B", ready_at=older)],
        None,
        today=TODAY,
    )
    assert "уже 5 дн." in text


def test_reminder_omits_storage_line_once_free_storage_is_over():
    ready = datetime(2026, 10, 1, 10, 0, tzinfo=VLAT)
    text = render(
        "reminder",
        None,
        [cargo("A", ready_at=ready, until=date(2026, 10, 8))],
        None,
        today=TODAY,
    )
    assert "Бесплатное хранение" not in text


# --- storage_ending ---


def test_storage_ending_text():
    text = render(
        "storage_ending",
        None,
        [cargo("88123", places=3, weight="12.4", amount="8400", until=date(2026, 10, 18))],
        truck(),
        today=TODAY,
    )
    assert text == (
        "Завтра заканчивается бесплатное хранение груза\n"
        "\n"
        "• Арт. 88123 — 3 места, 12,4 кг\n"
        f"К оплате: 8{NNBSP}400 ₽\n"
        "\n"
        "📍 ул. Складская, 1\n"
        "🕘 Пн–Сб 9:00–18:00\n"
        "Бесплатное хранение до 18 октября."
    )


# --- escaping and errors ---


def test_user_data_is_html_escaped(settings):
    settings.WAREHOUSE_ADDRESS = "ул. <Склад> & Ко"
    text = render("departed", None, [cargo("<b>1</b>", description="a&b")], truck(), today=TODAY)
    assert "&lt;b&gt;1&lt;/b&gt;" in text
    assert "a&amp;b" in text
    text = render("arrived", None, [cargo("<i>2</i>")], truck(number="R<1>"), today=TODAY)
    assert "&lt;i&gt;2&lt;/i&gt;" in text
    assert "ул. &lt;Склад&gt; &amp; Ко" in text
    text = render("departed", None, [cargo("1")], truck(number="R<1>"), today=TODAY)
    assert "Рейс R&lt;1&gt;" in text


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        render("nope", None, [], None)

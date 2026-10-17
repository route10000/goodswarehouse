from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from notify.messages import format_money, render

VLAT = ZoneInfo("Asia/Vladivostok")


def cargo(article, places=1, weight=None, amount="0", description="", until=None):
    return SimpleNamespace(
        article=article,
        places=places,
        weight_kg=Decimal(weight) if weight else None,
        amount_due=Decimal(amount),
        description=description,
        free_storage_until=until,
    )


def test_departed_text():
    truck = SimpleNamespace(number="R-2026-041", eta=datetime(2026, 10, 17, 15, 40, tzinfo=VLAT))
    client = SimpleNamespace(name="Иван")
    text = render("departed", client, [cargo("88123", places=3, description="одежда")], truck)
    assert text.startswith("🚚 Ваш груз выехал из Уссурийска")
    assert "Рейс R-2026-041 → Владивосток" in text
    assert "15:40" in text
    assert "• Арт. 88123 — одежда, 3 места" in text
    assert text.endswith("Напишем, когда можно будет забирать.")


def test_arrived_text():
    truck = SimpleNamespace(number="R-2026-041", eta=None)
    client = SimpleNamespace(name="Иван")
    text = render(
        "arrived",
        client,
        [cargo("88123", places=3, weight="12.4", amount="8400", until=date(2026, 10, 24))],
        truck,
    )
    assert text.startswith("📦 Груз на складе во Владивостоке, можно забирать")
    assert "• Арт. 88123 — 3 места, 12,4 кг" in text
    assert "К оплате: 8 400 ₽" in text
    assert "Бесплатное хранение до 24.10.2026." in text


def test_user_data_is_html_escaped():
    truck = SimpleNamespace(number="R-2026-001", eta=None)
    text = render("departed", None, [cargo("<b>1</b>", description="a&b")], truck)
    assert "&lt;b&gt;1&lt;/b&gt;" in text
    assert "a&amp;b" in text


def test_format_money():
    assert format_money(Decimal("8400")) == "8 400"
    assert format_money(Decimal("1234567.5")) == "1 234 567,50"
    assert format_money(Decimal("0")) == "0"

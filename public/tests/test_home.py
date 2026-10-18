"""Home page (DESIGN «Главная — презентация»): first screen, chapters, stats, old blocks."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from logistics import services
from public.views import route_summary

pytestmark = pytest.mark.django_db

PANEL_LOGIN = 'href="/panel/login/?next=/panel/"'
PICKUP_LOGIN = 'href="/panel/login/?next=/panel/pickup/"'


@pytest.fixture(autouse=True)
def _site_settings(settings):
    settings.WAREHOUSE_ADDRESS = "ул. Складская, 7"
    settings.WAREHOUSE_HOURS = "Пн–Сб 9:00–18:00"
    settings.WAREHOUSE_PHONE = "+7 (423) 200-00-00"
    settings.WAREHOUSE_CITY = "Владивосток"
    settings.TELEGRAM_BOT_USERNAME = "gw_test_bot"
    settings.FREE_STORAGE_DAYS = 9


def in_transit(departed_ago, eta_in, **kwargs):
    now = timezone.now()
    truck = services.create_truck(eta=now + eta_in, **kwargs)
    services.truck_depart(truck, now=now - departed_ago)
    return truck


def header(html: str) -> str:
    return html.split("</header>", 1)[0]


# ---------- header ----------


def test_header_anonymous_shows_login_menu_with_both_entries(client):
    html = header(client.get("/").content.decode())
    assert "Войти" in html
    assert 'aria-expanded="false"' in html
    assert PANEL_LOGIN in html
    assert PICKUP_LOGIN in html
    assert "Панель управления" in html
    assert "Склад" in html
    assert "Открыть панель" not in html


def test_header_authenticated_shows_open_panel(client):
    user = get_user_model().objects.create_user("staff", password="x")
    client.force_login(user)
    html = header(client.get("/").content.decode())
    assert "Открыть панель" in html
    assert 'href="/panel/"' in html
    assert PANEL_LOGIN not in html
    assert "Войти" not in html


@pytest.mark.parametrize("url", ["/schedule/", "/track/", "/contacts/"])
def test_inner_pages_share_header_and_footer(client, url):
    html = client.get(url).content.decode()
    assert PANEL_LOGIN in header(html)
    footer = html.split("<footer", 1)[1]
    assert "Вход для сотрудников" in footer
    assert "ул. Складская, 7" in footer
    assert "Пн–Сб 9:00–18:00" in footer


def test_header_bot_button_only_when_configured(client, settings):
    assert "Telegram-бот" in header(client.get("/").content.decode())
    settings.TELEGRAM_BOT_USERNAME = ""
    assert "Telegram-бот" not in header(client.get("/").content.decode())


# ---------- sections ----------


def test_home_sections_in_design_order(client):
    html = client.get("/").content.decode()
    markers = [
        'class="hero intro"',
        'id="client"',
        'id="warehouse"',
        'id="manager"',
        'id="numbers"',
        'id="track"',
        'id="schedule"',
        'id="terms"',
        'id="faq"',
        'id="staff"',
        'id="contacts"',
        "<footer",
    ]
    positions = [html.find(m) for m in markers]
    assert all(p >= 0 for p in positions), dict(zip(markers, positions, strict=True))
    assert positions == sorted(positions)


def test_home_first_screen_texts_and_anchors(client):
    html = client.get("/").content.decode()
    intro = html.split('class="hero intro"', 1)[1].split('id="client"', 1)[0]
    assert "ПЛАТФОРМА GOODSWAREHOUSE" in intro
    assert "Склад, машины и клиенты — в одном окне." in intro
    assert 'href="#track"' in intro
    assert 'href="#staff"' in intro
    assert "Где мой груз" in intro
    assert "Вход для сотрудников" in intro
    assert "<svg" in intro
    assert "intro__scroll" in intro


def chapter(html: str, chapter_id: str, next_id: str) -> str:
    return html.split(f'id="{chapter_id}"', 1)[1].split(f'id="{next_id}"', 1)[0]


@pytest.mark.parametrize(
    ("chapter_id", "next_id", "label", "tone"),
    [
        ("client", "warehouse", "01 / 04 · КЛИЕНТУ", "light"),
        ("warehouse", "manager", "02 / 04 · СКЛАДУ", "dark"),
        ("manager", "numbers", "03 / 04 · МЕНЕДЖЕРУ", "light"),
        ("numbers", "track", "04 / 04 · ЦИФРЫ", "dark"),
    ],
)
def test_chapters_have_label_and_alternate_tone(client, chapter_id, next_id, label, tone):
    html = client.get("/").content.decode()
    before, after = html.split(f'id="{chapter_id}"', 1)
    tag = before.rsplit("<section", 1)[1] + after.split(">", 1)[0]
    assert f"chapter--{tone}" in tag
    assert "data-reveal" in tag
    assert label in chapter(html, chapter_id, next_id)


def test_chapter_mocks_show_product(client):
    html = client.get("/").content.decode()
    client_ch = chapter(html, "client", "warehouse")
    assert "Ваш груз выехал из Уссурийска" in client_ch
    assert "К оплате" in client_ch
    warehouse_ch = chapter(html, "warehouse", "manager")
    assert "Позвонили" in warehouse_ch
    assert "Не дозвонились" in warehouse_ch
    manager_ch = chapter(html, "manager", "numbers")
    assert "Сейчас на маршруте" in manager_ch


def test_numbers_chapter_renders_stats(client):
    now = timezone.now()
    for ago in (timedelta(days=1), timedelta(days=2), timedelta(days=3)):
        services.truck_depart(services.create_truck(), now=now - ago)
    html = client.get("/").content.decode()
    numbers = chapter(html, "numbers", "track")
    values = re.findall(r'class="stat__value[^"]*">([^<]+)<', numbers)
    assert values == ["3", "0", "0,0", "0"]
    for caption in ("рейсов за 30 дней", "мест выдано", "хранения", "Telegram"):
        assert caption in numbers


def test_numbers_chapter_empty_database_shows_zeros(client):
    html = client.get("/").content.decode()
    numbers = chapter(html, "numbers", "track")
    values = re.findall(r'class="stat__value[^"]*">([^<]+)<', numbers)
    assert values == ["0", "0", "0,0", "0"]


def test_conditions_use_free_storage_days(client):
    html = client.get("/").content.decode()
    conditions = html.split('id="terms"', 1)[1].split('id="faq"', 1)[0]
    assert ">9<" in conditions
    assert "дней бесплатного хранения" in conditions


def test_faq_has_five_questions_and_uses_settings(client):
    html = client.get("/").content.decode()
    faq = html.split("Частые вопросы", 1)[1].split('id="staff"', 1)[0]
    assert faq.count("<details") == 5
    for question in (
        "Где взять код клиента?",
        "Сколько хранится груз бесплатно?",
        "Как подключить уведомления?",
        "Что нужно, чтобы забрать груз?",
        "Можно ли забрать чужой груз по доверенности?",
    ):
        assert question in faq
    assert "9 дней" in faq
    assert "ул. Складская, 7" in faq
    assert "Пн–Сб 9:00–18:00" in faq
    assert "+7 (423) 200-00-00" in faq
    assert "₽" not in faq


def test_staff_section_has_two_entry_cards(client):
    html = client.get("/").content.decode()
    staff = html.split('id="staff"', 1)[1].split('id="contacts"', 1)[0]
    assert "Вход для сотрудников" in staff
    assert "Войти как менеджер" in staff
    assert "Войти на склад" in staff
    assert PANEL_LOGIN in staff
    assert PICKUP_LOGIN in staff


# ---------- route widget ----------


def test_route_summary_counts_and_nearest_arrival():
    now = timezone.now()
    services.create_truck()
    services.create_truck()
    services.create_truck(is_public=False)
    later = in_transit(timedelta(hours=1), timedelta(hours=3))
    soon = in_transit(timedelta(hours=3), timedelta(hours=1))
    in_transit(timedelta(hours=5), -timedelta(hours=1))  # overdue: not "nearest"
    summary = route_summary(now)
    assert summary.loading == 2
    assert summary.in_transit == 3
    assert summary.nearest_eta == soon.eta
    assert later.eta > summary.nearest_eta


def test_route_summary_dot_positions_follow_progress():
    now = timezone.now()
    truck = in_transit(timedelta(hours=1), timedelta(hours=3))
    summary = route_summary(now)
    assert [dot.truck.pk for dot in summary.dots] == [truck.pk]
    assert summary.dots[0].percent == 25


def test_route_summary_empty():
    summary = route_summary(timezone.now())
    assert (summary.loading, summary.in_transit, summary.nearest_eta, summary.dots) == (
        0,
        0,
        None,
        [],
    )


def test_route_widget_renders_dots_rings_and_local_time(client):
    services.create_truck()
    truck = in_transit(timedelta(hours=1), timedelta(hours=3))
    html = client.get("/").content.decode()
    widget = html.split("Сейчас на маршруте", 1)[1].split('id="numbers"', 1)[0]
    assert len(re.findall(r'class="route-widget__dot"', widget)) == 1
    assert len(re.findall(r'class="route-widget__ring"', widget)) == 1
    assert "left: 25%" in widget
    assert timezone.localtime(truck.eta).strftime("%H:%M") in widget
    assert "в пути" in widget
    assert "грузятся" in widget
    assert "ближайшее прибытие" in widget


def test_route_widget_dash_without_arrivals(client):
    html = client.get("/").content.decode()
    widget = html.split("Сейчас на маршруте", 1)[1].split('id="numbers"', 1)[0]
    assert "—" in widget

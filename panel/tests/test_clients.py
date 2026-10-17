import sys
from unittest import mock

import pytest
from django.contrib.auth import get_user_model

from logistics import services
from logistics.models import ContactAttempt
from notify.models import Notification

pytestmark = pytest.mark.django_db


def test_list_search_by_phone_code_name(mgr, ivan, petr):
    services.add_cargo(ivan, "A1")
    services.add_cargo(ivan, "A2")

    def codes(q):
        response = mgr.get("/panel/clients/", {"q": q})
        return [c.code for c in response.context["page"].object_list]

    assert codes("") == [ivan.code, petr.code]
    assert codes("8 914 123 45 68") == [petr.code]
    assert codes(ivan.code.lower()) == [ivan.code]
    assert codes("Пёт") == [petr.code]  # SQLite LIKE folds ASCII case only
    assert codes("4567") == [ivan.code]


def test_list_shows_cargo_counts_and_telegram(mgr, ivan, petr):
    services.add_cargo(ivan, "A1")
    services.add_cargo(ivan, "A2")

    response = mgr.get("/panel/clients/")

    rows = {c.code: c for c in response.context["page"].object_list}
    assert rows[ivan.code].cargo_total == 2
    assert rows[petr.code].cargo_total == 0
    page = response.content.decode()
    assert "Telegram" in page and "Нет" in page


def test_card_renders_sections(mgr, manager, ivan, loaded_truck):
    services.truck_depart(loaded_truck)
    Notification.objects.filter(client=ivan).update(status="failed", last_error="chat not found")
    cargo = ivan.cargos.first()
    ContactAttempt.objects.create(client=ivan, cargo=cargo, staff=manager, outcome="no_answer")

    with mock.patch("bot.links.deep_link", return_value=None):
        response = mgr.get(f"/panel/clients/{ivan.pk}/")

    page = response.content.decode()
    assert response.status_code == 200
    assert f'href="tel:{ivan.phone}"' in page
    assert "A1" in page and "A2" in page
    assert "chat not found" in page
    assert "Не дозвонились" in page
    assert [c.article for c in response.context["cargos"]] == ["A2", "A1"]


def test_card_shows_deep_link_with_copy_button(mgr, ivan):
    link = "https://t.me/gw_bot?start=tok123"
    with mock.patch("bot.links.deep_link", return_value=link):
        page = mgr.get(f"/panel/clients/{ivan.pk}/").content.decode()

    assert link in page
    assert "Копировать" in page
    assert "Бот ещё не настроен" not in page


def test_card_falls_back_when_bot_not_configured(mgr, ivan):
    with mock.patch("bot.links.deep_link", return_value=None):
        page = mgr.get(f"/panel/clients/{ivan.pk}/").content.decode()

    assert "Бот ещё не настроен" in page
    assert "Копировать" not in page


def test_card_falls_back_when_bot_module_missing(mgr, ivan):
    with mock.patch.dict(sys.modules, {"bot.links": None}):
        page = mgr.get(f"/panel/clients/{ivan.pk}/").content.decode()

    assert "Бот ещё не настроен" in page


def test_card_limits_notifications_and_calls_to_20(mgr, manager, ivan):
    for n in range(25):
        Notification.objects.create(client=ivan, kind="custom", text=f"n{n}")
        ContactAttempt.objects.create(client=ivan, staff=manager, outcome="reached")

    with mock.patch("bot.links.deep_link", return_value=None):
        response = mgr.get(f"/panel/clients/{ivan.pk}/")

    assert len(response.context["notifications"]) == 20
    assert len(response.context["attempts"]) == 20


def test_edit_client(mgr, ivan):
    response = mgr.post(
        f"/panel/clients/{ivan.pk}/edit/",
        {"name": "Иван Петров", "phone": "8 (914) 000-00-09", "notes": "VIP"},
    )

    assert response.status_code == 302
    ivan.refresh_from_db()
    assert ivan.name == "Иван Петров"
    assert ivan.phone == "+79140000009"
    assert ivan.notes == "VIP"
    assert ivan.telegram_chat_id == 111


def test_edit_client_phone_must_be_unique(mgr, ivan, petr):
    response = mgr.post(
        f"/panel/clients/{ivan.pk}/edit/", {"name": "Иван", "phone": "8 914 123 45 68"}
    )

    assert response.status_code == 200
    assert "Этот телефон уже у другого клиента" in response.content.decode()
    ivan.refresh_from_db()
    assert ivan.phone == "+79141234567"


def test_edit_client_invalid_phone(mgr, ivan):
    response = mgr.post(f"/panel/clients/{ivan.pk}/edit/", {"name": "Иван", "phone": "12"})

    assert response.status_code == 200
    assert "Неверный номер телефона" in response.content.decode()


def test_superuser_is_manager(ivan):
    from django.test import Client as HttpClient

    admin = get_user_model().objects.create_superuser("root", password="x")
    http = HttpClient()
    http.force_login(admin)

    assert http.get(f"/panel/clients/{ivan.pk}/edit/").status_code == 200

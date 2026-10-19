"""Clients: «Новый клиент», QR code for the bot link and the card's summary tiles."""

from decimal import Decimal
from unittest import mock

import pytest

from logistics import services
from logistics.models import Client, ContactAttempt

pytestmark = pytest.mark.django_db

LINK = "https://t.me/gw_bot?start=tok123"


@pytest.fixture
def bot():
    with mock.patch("bot.links.deep_link", return_value=LINK):
        yield


@pytest.fixture
def no_bot():
    with mock.patch("bot.links.deep_link", return_value=None):
        yield


# ---------- new client ----------


def test_list_has_new_client_button_for_manager_only(mgr, wh):
    assert "/panel/clients/new/" in mgr.get("/panel/clients/").content.decode()
    assert "/panel/clients/new/" not in wh.get("/panel/clients/").content.decode()


def test_new_client_form(mgr):
    response = mgr.get("/panel/clients/new/")

    assert response.status_code == 200
    page = response.content.decode()
    for name in ("name", "phone", "notes"):
        assert f'name="{name}"' in page


def test_new_client_created(mgr):
    response = mgr.post(
        "/panel/clients/new/",
        {"name": " Ольга ", "phone": "8 (914) 555-11-22", "notes": "VIP"},
    )

    client = Client.objects.get(phone="+79145551122")
    assert response.status_code == 302
    assert response["Location"] == f"/panel/clients/{client.pk}/"
    assert client.name == "Ольга"
    assert client.notes == "VIP"
    assert client.code.startswith("GW-")


def test_new_client_duplicate_phone_links_to_existing(mgr, ivan):
    response = mgr.post(
        "/panel/clients/new/", {"name": "Другой", "phone": "+7 914 123-45-67", "notes": "x"}
    )

    assert response.status_code == 200
    page = response.content.decode()
    assert f"/panel/clients/{ivan.pk}/" in page
    assert ivan.code in page
    assert Client.objects.count() == 1
    ivan.refresh_from_db()
    assert ivan.name == "Иван"
    assert ivan.notes == ""


def test_new_client_invalid_phone(mgr):
    response = mgr.post("/panel/clients/new/", {"name": "А", "phone": "12"})

    assert response.status_code == 200
    assert response.context["form"].errors["phone"]
    assert not Client.objects.exists()


def test_new_client_forbidden_for_warehouse(wh):
    assert wh.get("/panel/clients/new/").status_code == 403
    assert wh.post("/panel/clients/new/", {"phone": "+79145551122"}).status_code == 403
    assert not Client.objects.exists()


# ---------- QR ----------


def test_card_shows_inline_qr(mgr, ivan, bot):
    page = mgr.get(f"/panel/clients/{ivan.pk}/").content.decode()

    assert "<svg" in page
    assert 'class="qr' in page
    assert f"/panel/clients/{ivan.pk}/qr.svg" in page
    assert f"/panel/clients/{ivan.pk}/qr.png" in page


def test_card_without_bot_has_no_qr(mgr, ivan, no_bot):
    page = mgr.get(f"/panel/clients/{ivan.pk}/").content.decode()

    assert 'class="qr' not in page
    assert "qr.svg" not in page
    assert "Бот ещё не настроен" in page


def test_qr_svg_download(wh, ivan, bot):
    response = wh.get(f"/panel/clients/{ivan.pk}/qr.svg")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("image/svg+xml")
    assert f'filename="{ivan.code}-telegram.svg"' in response["Content-Disposition"]
    assert response.content.lstrip().startswith(b"<?xml") or b"<svg" in response.content[:200]


def test_qr_png_download(mgr, ivan, bot):
    response = mgr.get(f"/panel/clients/{ivan.pk}/qr.png")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert f'filename="{ivan.code}-telegram.png"' in response["Content-Disposition"]


def test_qr_404_without_bot_or_client(mgr, ivan, no_bot):
    assert mgr.get(f"/panel/clients/{ivan.pk}/qr.svg").status_code == 404
    with mock.patch("bot.links.deep_link", return_value=LINK):
        assert mgr.get("/panel/clients/999999/qr.png").status_code == 404


# ---------- summary tiles ----------


def test_card_summary_tiles(mgr, manager, ivan, loaded_truck, no_bot):
    services.truck_depart(loaded_truck)
    services.truck_arrive(loaded_truck)
    services.add_cargo(ivan, "WAIT", amount_due=Decimal("40"))
    ContactAttempt.objects.create(client=ivan, staff=manager, outcome="reached")

    response = mgr.get(f"/panel/clients/{ivan.pk}/")
    summary = response.context["summary"]

    assert summary["total"] == 3
    assert summary["ready"] == 2
    assert summary["amount"] == Decimal("340")
    assert summary["last_call"].outcome == "reached"


def test_card_summary_empty(mgr, petr, no_bot):
    summary = mgr.get(f"/panel/clients/{petr.pk}/").context["summary"]

    assert summary == {"total": 0, "ready": 0, "amount": Decimal("0"), "last_call": None}

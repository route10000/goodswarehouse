from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bot.handlers import (
    BTN_ADDRESS,
    BTN_CARGO,
    BTN_SCHEDULE,
    BTN_SHARE,
    MAIN_MENU,
    Reply,
    handle_update,
)
from bot.tests.conftest import message
from logistics.models import Cargo, Client, Truck

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")


def only(replies) -> Reply:
    assert len(replies) == 1
    return replies[0]


def is_share_keyboard(markup) -> bool:
    return markup["keyboard"][0][0] == {"text": BTN_SHARE, "request_contact": True}


def menu_texts(markup) -> list[str]:
    return [b["text"] for row in markup["keyboard"] for b in row]


@pytest.fixture
def linked(ivan):
    Client.objects.filter(pk=ivan.pk).update(telegram_chat_id=500)
    ivan.refresh_from_db()
    return ivan


# ---------- /start with token ----------


def test_start_with_token_links_chat_and_shows_menu(ivan):
    reply = only(handle_update(message(500, text=f"/start {ivan.telegram_link_token}")))

    ivan.refresh_from_db()
    assert ivan.telegram_chat_id == 500
    assert reply.chat_id == 500
    assert "Иван" in reply.text
    assert "Петров" not in reply.text
    assert ivan.code in reply.text
    assert reply.reply_markup == MAIN_MENU
    assert menu_texts(MAIN_MENU) == [BTN_CARGO, BTN_SCHEDULE, BTN_ADDRESS]


def test_chat_moves_to_new_client_and_old_link_is_cleared(ivan):
    petr = Client.objects.create(phone="+79141234568", name="Пётр")
    handle_update(message(500, text=f"/start {ivan.telegram_link_token}"))
    handle_update(message(500, text=f"/start {petr.telegram_link_token}"))

    ivan.refresh_from_db()
    petr.refresh_from_db()
    assert ivan.telegram_chat_id is None
    assert petr.telegram_chat_id == 500


def test_token_reused_by_another_chat_relinks_client(ivan):
    handle_update(message(500, text=f"/start {ivan.telegram_link_token}"))
    handle_update(message(600, text=f"/start {ivan.telegram_link_token}"))

    ivan.refresh_from_db()
    assert ivan.telegram_chat_id == 600
    assert not Client.objects.filter(telegram_chat_id=500).exists()


def test_unknown_token_asks_to_share_phone(ivan):
    reply = only(handle_update(message(500, text="/start nope")))

    ivan.refresh_from_db()
    assert ivan.telegram_chat_id is None
    assert "Ссылка" in reply.text
    assert is_share_keyboard(reply.reply_markup)


def test_chat_id_is_unique_when_set(ivan):
    from django.db import IntegrityError, transaction

    petr = Client.objects.create(phone="+79141234568")
    Client.objects.filter(pk=ivan.pk).update(telegram_chat_id=500)
    with pytest.raises(IntegrityError), transaction.atomic():
        Client.objects.filter(pk=petr.pk).update(telegram_chat_id=500)
    # Many clients without Telegram are fine.
    Client.objects.create(phone="+79141234569")


# ---------- unlinked chat ----------


@pytest.mark.parametrize("text", ["/start", "привет", BTN_CARGO])
def test_unlinked_chat_is_asked_to_share_phone(db, text):
    reply = only(handle_update(message(500, text=text)))
    assert "номер" in reply.text
    assert is_share_keyboard(reply.reply_markup)


def test_start_without_token_in_linked_chat_shows_menu(linked):
    reply = only(handle_update(message(500, text="/start")))
    assert reply.reply_markup == MAIN_MENU
    assert linked.code in reply.text


def test_group_chats_are_ignored(ivan):
    update = message(-100, text=f"/start {ivan.telegram_link_token}", chat_type="group")
    assert handle_update(update) == []
    ivan.refresh_from_db()
    assert ivan.telegram_chat_id is None


def test_updates_without_message_are_ignored(db):
    assert handle_update({"update_id": 1, "edited_message": {}}) == []
    assert handle_update({"update_id": 2}) == []


# ---------- contact ----------


@pytest.mark.parametrize("phone", ["79141234567", "+79141234567", "+7 914 123-45-67"])
def test_own_contact_links_known_client(ivan, phone):
    update = message(500, contact={"phone_number": phone, "user_id": 500, "first_name": "И"})
    reply = only(handle_update(update))

    ivan.refresh_from_db()
    assert ivan.telegram_chat_id == 500
    assert ivan.code in reply.text
    assert reply.reply_markup == MAIN_MENU


def test_own_contact_unknown_phone(db, warehouse):
    update = message(500, contact={"phone_number": "+79990001122", "user_id": 500})
    reply = only(handle_update(update))

    assert reply.text == "Номер не найден. Обратитесь к менеджеру: +7 423 200-00-00"
    assert not Client.objects.filter(telegram_chat_id=500).exists()


def test_someone_elses_contact_is_rejected(ivan):
    update = message(500, contact={"phone_number": "+79141234567", "user_id": 777})
    reply = only(handle_update(update))

    ivan.refresh_from_db()
    assert ivan.telegram_chat_id is None
    assert "свой" in reply.text
    assert is_share_keyboard(reply.reply_markup)


def test_contact_without_user_id_is_rejected(ivan):
    handle_update(message(500, contact={"phone_number": "+79141234567"}))
    ivan.refresh_from_db()
    assert ivan.telegram_chat_id is None


def test_contact_with_garbage_phone_is_not_found(db, warehouse):
    reply = only(handle_update(message(500, contact={"phone_number": "abc", "user_id": 500})))
    assert reply.text.startswith("Номер не найден")


# ---------- menu ----------


def test_my_cargo_empty(linked):
    reply = only(handle_update(message(500, text=BTN_CARGO)))
    assert reply.text == "Сейчас грузов нет"
    assert reply.reply_markup == MAIN_MENU


def test_my_cargo_lists_active_cargo(linked):
    truck = Truck.objects.create(
        status=Truck.Status.IN_TRANSIT, eta=datetime(2026, 10, 21, 15, 40, tzinfo=VLAT)
    )
    Cargo.objects.create(
        client=linked,
        truck=truck,
        article="88123",
        description="одежда",
        places=3,
        weight_kg=Decimal("12.40"),
        amount_due=Decimal("8400"),
        status=Cargo.Status.IN_TRANSIT,
    )
    Cargo.objects.create(
        client=linked,
        article="55001",
        places=1,
        amount_due=Decimal("1600"),
        status=Cargo.Status.READY,
        free_storage_until=date(2026, 10, 24),
    )
    Cargo.objects.create(client=linked, article="OLD1", status=Cargo.Status.PICKED_UP)
    other = Client.objects.create(phone="+79141234568")
    Cargo.objects.create(client=other, article="ALIEN", status=Cargo.Status.READY)

    text = only(handle_update(message(500, text=BTN_CARGO))).text

    assert "Арт. 88123" in text
    assert "одежда" in text
    assert "3 места" in text
    assert "12,4 кг" in text
    assert "В пути" in text
    assert truck.number in text
    assert "21 октября" in text and "15:40" in text
    assert "8 400 ₽" in text
    assert "Арт. 55001" in text
    assert "на складе, бесплатное хранение до 24 октября" in text
    assert "1 600 ₽" in text
    assert "OLD1" not in text
    assert "ALIEN" not in text


def test_my_cargo_escapes_user_data(linked):
    Cargo.objects.create(
        client=linked, article="<b>1</b>", description="a & <i>b</i>", status=Cargo.Status.RECEIVED
    )
    text = only(handle_update(message(500, text=BTN_CARGO))).text
    assert "&lt;b&gt;1&lt;/b&gt;" in text
    assert "a &amp; &lt;i&gt;b&lt;/i&gt;" in text
    assert "<i>" not in text


def test_greeting_escapes_client_name(db):
    client = Client.objects.create(phone="+79141234569", name="<script>")
    text = only(handle_update(message(500, text=f"/start {client.telegram_link_token}"))).text
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


def test_schedule_lists_public_active_trucks(linked):
    loading = Truck.objects.create(status=Truck.Status.LOADING, departure_date=date(2026, 10, 20))
    moving = Truck.objects.create(
        status=Truck.Status.IN_TRANSIT, eta=datetime(2026, 10, 18, 12, 0, tzinfo=VLAT)
    )
    hidden = Truck.objects.create(status=Truck.Status.LOADING, is_public=False)
    done = Truck.objects.create(status=Truck.Status.ARRIVED)
    Cargo.objects.create(
        client=linked, truck=moving, article="SECRET", status=Cargo.Status.IN_TRANSIT
    )

    text = only(handle_update(message(500, text=BTN_SCHEDULE))).text

    assert loading.number in text and "Грузится" in text and "20 октября" in text
    assert moving.number in text and "В пути" in text and "18 октября" in text
    assert hidden.number not in text
    assert done.number not in text
    assert "SECRET" not in text and "Иван" not in text


def test_schedule_empty(linked):
    text = only(handle_update(message(500, text=BTN_SCHEDULE))).text
    assert "нет" in text


def test_address_and_hours(linked, warehouse):
    reply = only(handle_update(message(500, text=BTN_ADDRESS)))
    assert "ул. Складская, 1" in reply.text
    assert "Пн-Сб 9:00-18:00" in reply.text
    assert "+7 423 200-00-00" in reply.text
    assert reply.reply_markup == MAIN_MENU


def test_address_escapes_settings(linked, settings):
    settings.WAREHOUSE_ADDRESS = "A <b> & B"
    text = only(handle_update(message(500, text=BTN_ADDRESS))).text
    assert "A &lt;b&gt; &amp; B" in text


def test_public_menu_items_work_for_unlinked_chat(db, warehouse):
    reply = only(handle_update(message(500, text=BTN_ADDRESS)))
    assert "ул. Складская, 1" in reply.text
    assert is_share_keyboard(reply.reply_markup)


def test_unknown_text_in_linked_chat_shows_menu(linked):
    reply = only(handle_update(message(500, text="что-то")))
    assert reply.reply_markup == MAIN_MENU


# ---------- /stop ----------


def test_stop_unlinks_chat(linked):
    reply = only(handle_update(message(500, text="/stop")))
    linked.refresh_from_db()
    assert linked.telegram_chat_id is None
    assert "/start" in reply.text
    assert reply.reply_markup == {"remove_keyboard": True}


def test_stop_in_unlinked_chat_is_harmless(db):
    reply = only(handle_update(message(500, text="/stop")))
    assert reply.reply_markup == {"remove_keyboard": True}

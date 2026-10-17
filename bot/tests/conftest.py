import itertools

import pytest

from logistics.models import Client

_ids = itertools.count(1000)


def message(chat_id=500, *, text=None, contact=None, from_id=None, chat_type="private"):
    """A Telegram ``Update`` dict with one message."""
    msg = {
        "message_id": next(_ids),
        "from": {"id": from_id or chat_id, "is_bot": False, "first_name": "Тест"},
        "chat": {"id": chat_id, "type": chat_type},
        "date": 1760000000,
    }
    if text is not None:
        msg["text"] = text
    if contact is not None:
        msg["contact"] = contact
    return {"update_id": next(_ids), "message": msg}


@pytest.fixture
def ivan(db):
    return Client.objects.create(phone="+79141234567", name="Иван Петров")


@pytest.fixture
def warehouse(settings):
    settings.WAREHOUSE_ADDRESS = "ул. Складская, 1"
    settings.WAREHOUSE_HOURS = "Пн-Сб 9:00-18:00"
    settings.WAREHOUSE_PHONE = "+7 423 200-00-00"
    return settings

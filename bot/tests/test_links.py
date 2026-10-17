import pytest

from bot.links import deep_link
from logistics.models import Client

pytestmark = pytest.mark.django_db


def test_deep_link_uses_bot_username_and_token(ivan, settings):
    settings.TELEGRAM_BOT_USERNAME = "gw_bot"
    assert deep_link(ivan) == f"https://t.me/gw_bot?start={ivan.telegram_link_token}"


def test_deep_link_strips_at_sign(ivan, settings):
    settings.TELEGRAM_BOT_USERNAME = "@gw_bot"
    assert deep_link(ivan).startswith("https://t.me/gw_bot?start=")


def test_deep_link_none_without_username(ivan, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    assert deep_link(ivan) is None


def test_deep_link_creates_token_once(ivan, settings):
    settings.TELEGRAM_BOT_USERNAME = "gw_bot"
    Client.objects.filter(pk=ivan.pk).update(telegram_link_token="")
    ivan.refresh_from_db()

    first = deep_link(ivan)
    ivan.refresh_from_db()
    token = ivan.telegram_link_token
    assert token and len(token) >= 20
    assert first == f"https://t.me/gw_bot?start={token}"

    assert deep_link(ivan) == first
    ivan.refresh_from_db()
    assert ivan.telegram_link_token == token

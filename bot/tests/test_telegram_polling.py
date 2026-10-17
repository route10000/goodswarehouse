"""``get_updates`` and ``reply_markup`` additions to ``notify.telegram``."""

import json

import httpx
import pytest

from notify.telegram import BotTokenMissing, PermanentError, TelegramClient, TemporaryError


def make_client(handler, token="123:ABC"):
    return TelegramClient(token=token, transport=httpx.MockTransport(handler))


def test_get_updates_posts_offset_and_timeout():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]["read"]
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    updates = make_client(handler).get_updates(offset=5, timeout=25)

    assert seen["url"] == "https://api.telegram.org/bot123:ABC/getUpdates"
    assert seen["body"] == {"offset": 5, "timeout": 25, "allowed_updates": ["message"]}
    assert seen["timeout"] > 25  # HTTP timeout must outlast the long poll
    assert updates == [{"update_id": 7}]


def test_get_updates_without_offset_omits_it():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": []})

    assert make_client(handler).get_updates(offset=None, timeout=0) == []
    assert "offset" not in seen["body"]


def test_get_updates_network_error_is_temporary():
    def handler(request):
        raise httpx.ConnectError("boom 123:ABC")

    with pytest.raises(TemporaryError) as info:
        make_client(handler).get_updates(offset=None, timeout=1)
    assert "123:ABC" not in str(info.value)


def test_get_updates_conflict_is_permanent():
    def handler(request):
        return httpx.Response(409, json={"ok": False, "description": "Conflict: webhook"})

    with pytest.raises(PermanentError, match="409"):
        make_client(handler).get_updates(offset=None, timeout=1)


def test_get_updates_dry_run_raises_clear_error():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("network used in dry-run")

    client = TelegramClient(token="", transport=httpx.MockTransport(handler))
    with pytest.raises(BotTokenMissing, match="TELEGRAM_BOT_TOKEN"):
        client.get_updates(offset=None, timeout=1)


def test_send_message_with_reply_markup():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    markup = {"remove_keyboard": True}
    make_client(handler).send_message(1, "x", reply_markup=markup)
    assert seen["body"]["reply_markup"] == markup

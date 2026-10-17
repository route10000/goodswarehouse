import json

import httpx
import pytest
from django.core.management import CommandError, call_command

from bot.management.commands import run_bot
from bot.models import BotState
from bot.tests.conftest import message
from notify.telegram import TelegramClient

pytestmark = pytest.mark.django_db


class FakeTelegram:
    """Mock transport: serves queued getUpdates batches and records sendMessage calls."""

    def __init__(self, batches, fail_chats=()):
        self.batches = list(batches)
        self.fail_chats = set(fail_chats)
        self.offsets = []
        self.sent = []

    def __call__(self, request):
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            self.offsets.append(body.get("offset"))
            result = self.batches.pop(0) if self.batches else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if body["chat_id"] in self.fail_chats:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})
        self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def factory(**kwargs):
        return TelegramClient(token="1:T", transport=httpx.MockTransport(holder["fake"]))

    monkeypatch.setattr(run_bot, "TelegramClient", factory)
    return holder


def test_once_handles_batch_sends_replies_and_persists_offset(fake, ivan):
    u1 = message(501, text="/start")
    u2 = message(502, text=f"/start {ivan.telegram_link_token}")
    fake["fake"] = FakeTelegram([[u1, u2]])

    call_command("run_bot", "--once")

    assert [m["chat_id"] for m in fake["fake"].sent] == [501, 502]
    assert all(m["parse_mode"] == "HTML" for m in fake["fake"].sent)
    assert "reply_markup" in fake["fake"].sent[0]
    assert BotState.objects.get(key="telegram_offset").value == str(u2["update_id"] + 1)
    ivan.refresh_from_db()
    assert ivan.telegram_chat_id == 502


def test_restart_continues_from_saved_offset(fake, db):
    BotState.objects.create(key="telegram_offset", value="42")
    fake["fake"] = FakeTelegram([[]])

    call_command("run_bot", "--once")

    assert fake["fake"].offsets == [42]
    assert BotState.objects.get(key="telegram_offset").value == "42"


def test_send_failure_does_not_stop_batch(fake, db, caplog):
    u1 = message(501, text="/start")
    u2 = message(502, text="/start")
    fake["fake"] = FakeTelegram([[u1, u2]], fail_chats={501})

    call_command("run_bot", "--once")

    assert [m["chat_id"] for m in fake["fake"].sent] == [502]
    assert BotState.objects.get(key="telegram_offset").value == str(u2["update_id"] + 1)
    assert "501" in caplog.text


def test_handler_crash_is_logged_and_offset_advances(fake, db, monkeypatch, caplog):
    u1 = message(501, text="/start")
    u2 = message(502, text="/start")
    fake["fake"] = FakeTelegram([[u1, u2]])
    real = run_bot.handle_update

    def flaky(update):
        if update is u1 or update["update_id"] == u1["update_id"]:
            raise RuntimeError("bug")
        return real(update)

    monkeypatch.setattr(run_bot, "handle_update", flaky)
    call_command("run_bot", "--once")

    assert [m["chat_id"] for m in fake["fake"].sent] == [502]
    assert BotState.objects.get(key="telegram_offset").value == str(u2["update_id"] + 1)
    assert "bug" in caplog.text


def test_network_error_in_loop_sleeps_and_continues(monkeypatch, db):
    calls = {"n": 0}

    class Stop(Exception):
        pass

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        raise Stop

    monkeypatch.setattr(
        run_bot,
        "TelegramClient",
        lambda **kw: TelegramClient(token="1:T", transport=httpx.MockTransport(handler)),
    )
    sleeps = []
    monkeypatch.setattr(run_bot.time, "sleep", sleeps.append)

    with pytest.raises(Stop):
        call_command("run_bot")

    assert sleeps == [5]
    assert calls["n"] == 2


def test_missing_token_is_a_command_error(settings, db):
    settings.TELEGRAM_BOT_TOKEN = ""
    with pytest.raises(CommandError, match="TELEGRAM_BOT_TOKEN"):
        call_command("run_bot", "--once")

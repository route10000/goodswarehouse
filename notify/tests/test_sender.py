import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from logistics.models import Client
from notify.models import Notification
from notify.services import MAX_ATTEMPTS, send_pending
from notify.telegram import TelegramClient

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=VLAT)

OK = {"ok": True, "result": {"message_id": 1}}


def telegram(responses):
    """Client whose replies are picked by chat_id: ``{chat_id: (status, body) | Exception}``."""
    calls = []

    def handler(request):
        chat_id = json.loads(request.content)["chat_id"]
        calls.append(chat_id)
        reply = responses.get(chat_id, (200, OK))
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body)

    client = TelegramClient(token="1:T", transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


_phone = iter(range(10, 99))


def make_client(chat_id=None):
    return Client.objects.create(
        name="Клиент", phone=f"+791412345{next(_phone):02d}", telegram_chat_id=chat_id
    )


def notification(client, minutes=0, status=Notification.Status.PENDING, attempts=0):
    return Notification.objects.create(
        client=client,
        kind=Notification.Kind.CUSTOM,
        status=status,
        text=f"text for {client.telegram_chat_id}",
        attempts=attempts,
        created_at=NOW + timedelta(minutes=minutes),
    )


def fresh(obj):
    return type(obj).objects.get(pk=obj.pk)


def test_success_marks_sent_with_time():
    n = notification(make_client(101))
    tg = telegram({})

    stats = send_pending(now=NOW, telegram=tg)

    n = fresh(n)
    assert n.status == Notification.Status.SENT
    assert n.sent_at == NOW
    assert n.attempts == 1
    assert n.last_error == ""
    assert stats["sent"] == 1
    assert tg.calls == [101]


def test_dry_run_marks_sent_with_dry_run_note():
    n = notification(make_client(101))

    stats = send_pending(now=NOW, telegram=TelegramClient(token=""))

    n = fresh(n)
    assert n.status == Notification.Status.SENT
    assert n.last_error == "dry-run"
    assert n.sent_at == NOW
    assert stats["sent"] == 1


def test_dry_run_is_default_without_token(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    n = notification(make_client(101))
    send_pending(now=NOW)
    assert fresh(n).last_error == "dry-run"


def test_bot_blocked_fails_and_clears_chat_id():
    client = make_client(101)
    n = notification(client)
    tg = telegram({101: (403, {"ok": False, "description": "Forbidden: bot was blocked"})})

    stats = send_pending(now=NOW, telegram=tg)

    n = fresh(n)
    assert n.status == Notification.Status.FAILED
    assert "403" in n.last_error
    assert n.sent_at is None
    assert fresh(client).telegram_chat_id is None
    assert stats["failed"] == 1


def test_after_block_other_pending_of_that_client_are_skipped_without_sending():
    client = make_client(101)
    first = notification(client, minutes=0)
    second = notification(client, minutes=1)
    tg = telegram({101: (403, {"ok": False, "description": "Forbidden"})})

    send_pending(now=NOW, telegram=tg)

    assert fresh(first).status == Notification.Status.FAILED
    assert fresh(second).status == Notification.Status.SKIPPED
    assert tg.calls == [101]


@pytest.mark.parametrize(
    "reply",
    [
        (429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 5}}),
        (502, {"ok": False, "description": "Bad Gateway"}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
    ids=["429", "5xx", "network", "timeout"],
)
def test_retryable_errors_keep_pending_until_max_attempts(reply):
    client = make_client(101)
    n = notification(client)
    tg = telegram({101: reply})

    for expected_attempts in range(1, MAX_ATTEMPTS):
        send_pending(now=NOW, telegram=tg)
        n = fresh(n)
        assert n.status == Notification.Status.PENDING
        assert n.attempts == expected_attempts
        assert n.last_error

    send_pending(now=NOW, telegram=tg)
    n = fresh(n)
    assert n.status == Notification.Status.FAILED
    assert n.attempts == MAX_ATTEMPTS
    assert fresh(client).telegram_chat_id == 101  # only 403 unlinks the chat
    assert MAX_ATTEMPTS == 3


def test_retry_later_note_mentions_retry_after():
    n = notification(make_client(101))
    tg = telegram({101: (429, {"ok": False, "parameters": {"retry_after": 30}})})
    send_pending(now=NOW, telegram=tg)
    assert "30" in fresh(n).last_error


def test_permanent_error_fails_immediately():
    client = make_client(101)
    n = notification(client)
    tg = telegram({101: (400, {"ok": False, "description": "Bad Request: chat not found"})})

    send_pending(now=NOW, telegram=tg)

    n = fresh(n)
    assert n.status == Notification.Status.FAILED
    assert n.attempts == 1
    assert "chat not found" in n.last_error
    assert fresh(client).telegram_chat_id == 101


def test_batch_continues_after_failures():
    blocked = notification(make_client(101), minutes=0)
    broken = notification(make_client(102), minutes=1)
    flaky = notification(make_client(103), minutes=2)
    good = notification(make_client(104), minutes=3)
    tg = telegram(
        {
            101: (403, {"ok": False}),
            102: (400, {"ok": False}),
            103: (500, {"ok": False}),
        }
    )

    stats = send_pending(now=NOW, telegram=tg)

    assert fresh(blocked).status == Notification.Status.FAILED
    assert fresh(broken).status == Notification.Status.FAILED
    assert fresh(flaky).status == Notification.Status.PENDING
    assert fresh(good).status == Notification.Status.SENT
    assert tg.calls == [101, 102, 103, 104]
    assert stats == {"sent": 1, "failed": 2, "retry": 1, "skipped": 0}


def test_unexpected_exception_does_not_stop_the_batch(monkeypatch):
    first = notification(make_client(101), minutes=0)
    second = notification(make_client(102), minutes=1)
    tg = telegram({})
    real_send = tg.send_message

    def send(chat_id, text):
        if chat_id == 101:
            raise RuntimeError("boom")
        return real_send(chat_id, text)

    monkeypatch.setattr(tg, "send_message", send)

    send_pending(now=NOW, telegram=tg)

    first = fresh(first)
    assert first.status == Notification.Status.PENDING
    assert first.attempts == 1
    assert "boom" in first.last_error
    assert fresh(second).status == Notification.Status.SENT


def test_oldest_first_and_limit():
    newest = notification(make_client(103), minutes=10)
    oldest = notification(make_client(101), minutes=0)
    middle = notification(make_client(102), minutes=5)
    tg = telegram({})

    send_pending(limit=2, now=NOW, telegram=tg)

    assert tg.calls == [101, 102]
    assert fresh(oldest).status == Notification.Status.SENT
    assert fresh(middle).status == Notification.Status.SENT
    assert fresh(newest).status == Notification.Status.PENDING


def test_only_pending_are_processed():
    client = make_client(101)
    for status in (
        Notification.Status.SENT,
        Notification.Status.FAILED,
        Notification.Status.SKIPPED,
    ):
        notification(client, status=status)
    tg = telegram({})

    stats = send_pending(now=NOW, telegram=tg)

    assert tg.calls == []
    assert stats == {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}


def test_pending_for_client_without_telegram_is_skipped():
    n = notification(make_client(None))
    tg = telegram({})

    send_pending(now=NOW, telegram=tg)

    assert fresh(n).status == Notification.Status.SKIPPED
    assert tg.calls == []


def test_sends_the_stored_text_to_the_client_chat():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=OK)

    notification(make_client(777))
    send_pending(
        now=NOW, telegram=TelegramClient(token="1:T", transport=httpx.MockTransport(handler))
    )

    assert seen[0]["chat_id"] == 777
    assert seen[0]["text"] == "text for 777"

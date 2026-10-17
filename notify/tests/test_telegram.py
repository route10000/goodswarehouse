import json
import logging

import httpx
import pytest

from notify.telegram import (
    BotBlocked,
    PermanentError,
    RetryLater,
    TelegramClient,
    TemporaryError,
)


def make_client(handler, token="123:ABC"):
    return TelegramClient(token=token, transport=httpx.MockTransport(handler))


def respond(status, body):
    return lambda request: httpx.Response(status, json=body)


def test_send_message_posts_html_without_preview():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    result = make_client(handler).send_message(111, "<b>Привет</b>")

    assert seen["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert seen["body"] == {
        "chat_id": 111,
        "text": "<b>Привет</b>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    assert result.ok is True
    assert result.dry_run is False
    assert result.message_id == 42


def test_403_raises_bot_blocked():
    handler = respond(403, {"ok": False, "error_code": 403, "description": "Forbidden: blocked"})
    with pytest.raises(BotBlocked, match="blocked"):
        make_client(handler).send_message(1, "x")


def test_429_raises_retry_later_with_retry_after():
    handler = respond(
        429,
        {
            "ok": False,
            "error_code": 429,
            "description": "Too Many",
            "parameters": {"retry_after": 7},
        },
    )
    with pytest.raises(RetryLater) as info:
        make_client(handler).send_message(1, "x")
    assert info.value.retry_after == 7


def test_429_without_parameters_has_none_retry_after():
    with pytest.raises(RetryLater) as info:
        make_client(respond(429, {"ok": False})).send_message(1, "x")
    assert info.value.retry_after is None


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_raises_temporary(status):
    with pytest.raises(TemporaryError):
        make_client(respond(status, {"ok": False, "description": "Bad Gateway"})).send_message(
            1, "x"
        )


def test_5xx_with_non_json_body_is_temporary():
    def handler(request):
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(TemporaryError):
        make_client(handler).send_message(1, "x")


@pytest.mark.parametrize("status", [400, 401, 404])
def test_other_4xx_raises_permanent(status):
    handler = respond(status, {"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(PermanentError, match="chat not found"):
        make_client(handler).send_message(1, "x")


def test_network_error_is_temporary():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(TemporaryError):
        make_client(handler).send_message(1, "x")


def test_timeout_is_temporary():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(TemporaryError):
        make_client(handler).send_message(1, "x")


def test_error_messages_never_contain_the_token():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(TemporaryError) as info:
        make_client(handler, token="999:SECRET").send_message(1, "x")
    assert "SECRET" not in str(info.value)


def test_200_with_ok_false_is_permanent():
    with pytest.raises(PermanentError):
        make_client(respond(200, {"ok": False, "description": "weird"})).send_message(1, "x")


def test_dry_run_without_token_logs_and_skips_network(caplog):
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("network used in dry-run")

    client = TelegramClient(token="", transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO, logger="notify.telegram"):
        result = client.send_message(555, "Текст сообщения")

    assert client.dry_run is True
    assert result.ok is True
    assert result.dry_run is True
    assert result.message_id is None
    assert "555" in caplog.text
    assert "Текст сообщения" in caplog.text


def test_token_defaults_to_settings(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    assert TelegramClient().dry_run is True
    settings.TELEGRAM_BOT_TOKEN = "1:X"
    assert TelegramClient().dry_run is False

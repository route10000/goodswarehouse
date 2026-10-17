"""Minimal Telegram Bot API client for ``sendMessage`` (HTML parse mode).

Only ``notify.services.send_pending`` uses it; web requests never talk to Telegram.
Without ``TELEGRAM_BOT_TOKEN`` the client runs in dry-run: it logs the message and
returns a dry-run result without touching the network (SPEC §11).

Errors are typed so the sender can decide what happens to a notification:
``BotBlocked`` (403), ``RetryLater`` (429), ``TemporaryError`` (5xx, network, timeout),
``PermanentError`` (any other 4xx or a malformed reply). Messages never include the token.
"""

import logging
from dataclasses import dataclass

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 10.0


class TelegramError(Exception):
    """Base class for send failures."""


class BotBlocked(TelegramError):
    """403: the user blocked the bot or the chat is gone."""


class RetryLater(TelegramError):
    """429: flood control; ``retry_after`` seconds if Telegram said so."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TemporaryError(TelegramError):
    """5xx, network failure or timeout: worth retrying."""


class PermanentError(TelegramError):
    """Any other 4xx (e.g. chat not found): retrying will not help."""


@dataclass(frozen=True)
class SendResult:
    ok: bool
    dry_run: bool = False
    message_id: int | None = None


def _payload(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.token = settings.TELEGRAM_BOT_TOKEN if token is None else token
        self._http = httpx.Client(transport=transport, timeout=timeout)

    @property
    def dry_run(self) -> bool:
        return not self.token

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send_message(self, chat_id: int, text: str) -> SendResult:
        if self.dry_run:
            logger.info("dry-run sendMessage chat_id=%s\n%s", chat_id, text)
            return SendResult(ok=True, dry_run=True)

        url = f"{API_BASE}/bot{self.token}/sendMessage"
        body = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = self._http.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise TemporaryError(f"timeout: {type(exc).__name__}") from None
        except httpx.TransportError as exc:
            # The exception text may contain the URL (and so the token): use the type only.
            raise TemporaryError(f"network error: {type(exc).__name__}") from None

        data = _payload(response)
        description = str(data.get("description") or response.reason_phrase or "")
        status = response.status_code
        if status == 200 and data.get("ok") is True:
            result = data.get("result") or {}
            return SendResult(ok=True, message_id=result.get("message_id"))
        if status == 403:
            raise BotBlocked(f"403 {description}")
        if status == 429:
            retry_after = (data.get("parameters") or {}).get("retry_after")
            raise RetryLater(f"429 {description}", retry_after=retry_after)
        if status >= 500:
            raise TemporaryError(f"{status} {description}")
        raise PermanentError(f"{status} {description}")

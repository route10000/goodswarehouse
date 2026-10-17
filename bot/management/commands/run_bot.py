"""Long-polling Telegram bot (SPEC §8): ``manage.py run_bot [--once]``.

Each update is handled by ``bot.handlers.handle_update``; its replies are sent right
away. A failed send or a handler crash is logged and does not stop the batch. The
next ``getUpdates`` offset is stored in ``BotState`` after every update, so a restart
does not process the same messages again. Network errors: log, wait 5 s, continue.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from bot.handlers import handle_update
from bot.models import BotState
from notify.telegram import BotTokenMissing, TelegramClient, TelegramError

logger = logging.getLogger(__name__)

OFFSET_KEY = "telegram_offset"
POLL_TIMEOUT = 25
ERROR_PAUSE = 5


def _load_offset() -> int | None:
    value = BotState.get_value(OFFSET_KEY)
    try:
        return int(value) if value else None
    except ValueError:
        return None


def process_update(telegram: TelegramClient, update: dict) -> int:
    """Handle one update, send its replies, return how many replies were sent."""
    update_id = update.get("update_id")
    try:
        replies = handle_update(update)
    except Exception:
        logger.exception("update %s: handler failed", update_id)
        replies = []
    sent = 0
    for reply in replies:
        try:
            telegram.send_message(reply.chat_id, reply.text, reply_markup=reply.reply_markup)
            sent += 1
        except TelegramError as exc:
            logger.warning("update %s: send to chat %s failed: %s", update_id, reply.chat_id, exc)
    if isinstance(update_id, int):
        BotState.set_value(OFFSET_KEY, str(update_id + 1))
    return sent


def poll_once(telegram: TelegramClient, timeout: int) -> int:
    """One getUpdates batch; returns the number of updates processed."""
    if telegram.dry_run:  # fail before touching the DB: nothing works without a token
        telegram.get_updates(offset=None, timeout=timeout)
    updates = telegram.get_updates(offset=_load_offset(), timeout=timeout)
    for update in updates:
        process_update(telegram, update)
    return len(updates)


class Command(BaseCommand):
    help = "Запустить Telegram-бота (long polling)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Обработать одну пачку обновлений и выйти."
        )

    def handle(self, *args, once=False, **options):
        with TelegramClient() as telegram:
            try:
                if once:
                    count = poll_once(telegram, timeout=0)
                    self.stdout.write(f"Обработано обновлений: {count}")
                    return
                self.stdout.write("Бот запущен. Остановить: Ctrl+C.")
                while True:
                    try:
                        poll_once(telegram, timeout=POLL_TIMEOUT)
                    except BotTokenMissing:
                        raise
                    except TelegramError as exc:
                        pause = max(ERROR_PAUSE, getattr(exc, "retry_after", None) or 0)
                        logger.warning("getUpdates failed: %s; retry in %ss", exc, pause)
                        time.sleep(pause)
            except BotTokenMissing as exc:
                raise CommandError(str(exc)) from None
            except TelegramError as exc:  # --once only: the loop handles its own errors
                raise CommandError(f"getUpdates failed: {exc}") from None
            except KeyboardInterrupt:
                self.stdout.write("Бот остановлен.")

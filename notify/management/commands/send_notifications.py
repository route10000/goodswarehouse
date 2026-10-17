"""Send queued Telegram notifications: one pass, or ``--loop`` for a systemd service."""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from notify.services import send_pending

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Отправляет уведомления из очереди (pending) в Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Сколько за один проход.")
        parser.add_argument("--loop", action="store_true", help="Работать постоянно.")
        parser.add_argument(
            "--interval", type=float, default=10, help="Пауза между проходами, сек (с --loop)."
        )

    def handle(self, *args, limit, loop, interval, **options):
        if not loop:
            self._report(send_pending(limit=limit))
            return
        try:
            while True:
                close_old_connections()
                try:
                    stats = send_pending(limit=limit)
                except Exception:  # keep the service alive (e.g. the database restarts)
                    logger.exception("send_notifications pass failed")
                else:
                    if any(stats.values()):
                        self._report(stats)
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Остановлено.")

    def _report(self, stats):
        get = lambda key: stats.get(key, 0)  # noqa: E731
        self.stdout.write(
            f"Уведомления: отправлено {get('sent')}, ошибок {get('failed')}, "
            f"повтор {get('retry')}, пропущено {get('skipped')}"
        )

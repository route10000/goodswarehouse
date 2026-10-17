"""Queue the morning ``reminder`` and ``storage_ending`` notifications (SPEC §7)."""

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from notify.models import Notification
from notify.services import enqueue_reminders


class Command(BaseCommand):
    help = "Ставит в очередь напоминания о грузах на складе и о конце бесплатного хранения."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", dest="day", help="Дата YYYY-MM-DD (по умолчанию сегодня, местное время)."
        )

    def handle(self, *args, day=None, **options):
        today = None
        if day:
            try:
                today = _parse_date(day)
            except ValueError:
                raise CommandError(f"Неверная дата {day!r}, нужен формат YYYY-MM-DD") from None
        created = enqueue_reminders(today)
        reminders = sum(n.kind == Notification.Kind.REMINDER for n in created)
        endings = sum(n.kind == Notification.Kind.STORAGE_ENDING for n in created)
        self.stdout.write(f"В очереди: напоминаний {reminders}, о конце хранения {endings}")


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()

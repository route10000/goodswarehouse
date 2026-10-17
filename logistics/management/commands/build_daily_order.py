"""Build (or top up) the daily call order (SPEC §7). Run by the morning timer."""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from logistics.orders import OrderError, build_daily_order


class Command(BaseCommand):
    help = "Формирует ордер на день: добавляет недостающие строки, отмеченные не трогает."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", dest="day", help="Дата YYYY-MM-DD (по умолчанию сегодня, местное время)."
        )

    def handle(self, *args, day=None, **options):
        if day:
            try:
                day = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError(f"Неверная дата {day!r}, нужен формат YYYY-MM-DD") from None
        else:
            day = timezone.localdate()
        try:
            order, added = build_daily_order(day)
        except OrderError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"{order}: добавлено {added}, всего {order.lines.count()}")

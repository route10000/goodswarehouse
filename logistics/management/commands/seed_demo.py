"""Fill the database with demo data (SPEC §13): ``manage.py seed_demo [--reset]``.

Everything goes through ``logistics.services`` / ``logistics.orders`` / ``notify.services``
with ``now=`` in the past, so the data is what the real flows would produce.
Notifications are "sent" by an offline fake client: no network.
"""

import os
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from logistics import orders, services
from logistics.models import (
    Cargo,
    Client,
    ContactAttempt,
    Counter,
    DailyOrder,
    DailyOrderLine,
    Truck,
)
from notify.models import Notification
from notify.services import send_pending
from notify.telegram import PermanentError, SendResult

DEFAULT_PASSWORD = "demo12345"

FIRST_NAMES = [
    "Алексей", "Марина", "Сергей", "Ольга", "Дмитрий", "Наталья", "Игорь", "Елена",
    "Андрей", "Татьяна", "Виктор", "Светлана", "Павел", "Ирина", "Максим", "Анна",
    "Роман", "Юлия", "Евгений", "Оксана", "Константин", "Людмила", "Артём", "Вера", "Глеб",
]  # fmt: skip
LAST_NAMES = [
    "Ковалёв", "Ли", "Пак", "Смирнов", "Ким", "Захаров", "Гончаров", "Тен", "Орлов",
    "Белов", "Шевченко", "Цой", "Морозов", "Волков", "Хан", "Лебедев", "Ни", "Попов",
    "Соколов", "Кузнецов", "Тимофеев", "Югай", "Никитин", "Фёдоров", "Дорофеев",
]  # fmt: skip
GOODS = [
    ("одежда", "Куртки зимние"), ("одежда", "Футболки хлопок"), ("одежда", "Детская одежда"),
    ("запчасти", "Запчасти Toyota"), ("запчасти", "Фильтры и колодки"),
    ("электроника", "Наушники и зарядки"), ("электроника", "Видеорегистраторы"),
    ("посуда", "Посуда керамика"), ("посуда", "Кастрюли нержавейка"),
    ("игрушки", "Игрушки конструкторы"), ("игрушки", "Мягкие игрушки"),
    ("обувь", "Кроссовки"), ("текстиль", "Постельное бельё"), ("инструмент", "Электроинструмент"),
]  # fmt: skip
PREFIXES = ["914", "924", "902", "908", "984", "951"]


class FakeTelegram:
    """Offline stand-in for ``TelegramClient``: the first chat it sees fails, others pass."""

    def __init__(self):
        self.failing = None

    def send_message(self, chat_id, text, reply_markup=None):
        if self.failing is None:
            self.failing = chat_id
        if chat_id == self.failing:
            raise PermanentError("Bad Request: chat not found (демо)")
        return SendResult(ok=True, dry_run=True)

    def close(self):
        pass


class Command(BaseCommand):
    help = "Заполняет базу демо-данными. --reset сначала удаляет все грузы, машины и клиентов."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Удалить данные логистики и уведомлений."
        )

    def handle(self, *args, reset=False, **options):
        if reset:
            self._reset()
        elif Truck.objects.exists():
            raise CommandError("В базе уже есть машины. Запустите с --reset, чтобы пересоздать.")
        self.rng = random.Random(2024)
        self.article_no = 0
        manager, sklad, password_note = self._users()
        with transaction.atomic():
            clients = self._clients()
            telegram = FakeTelegram()
            trucks = self._trucks(clients, sklad, telegram)
            order, added = orders.build_daily_order(timezone.localdate())
            self._mark_lines(order, manager)
        self._summary(clients, trucks, order, password_note)

    # --- steps ---

    def _reset(self):
        with transaction.atomic():
            ContactAttempt.objects.all().delete()
            DailyOrderLine.objects.all().delete()
            DailyOrder.objects.all().delete()
            Notification.objects.all().delete()
            Cargo.objects.all().delete()
            Truck.objects.all().delete()
            Client.objects.all().delete()
            Counter.objects.all().delete()
        self.stdout.write("Старые данные удалены.")

    def _users(self):
        env_password = os.environ.get("DEMO_PASSWORD")
        password = env_password or DEFAULT_PASSWORD
        result = []
        for username, group_name, first in (
            ("manager", "Менеджер", "Менеджер"),
            ("sklad", "Склад", "Кладовщик"),
        ):
            user, created = User.objects.get_or_create(
                username=username, defaults={"first_name": first, "is_staff": False}
            )
            if created:
                user.set_password(password)
                user.save()
            group, _ = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
            result.append(user)
        note = (
            "демо-пароль из DEMO_PASSWORD"
            if env_password
            else f"пароль {DEFAULT_PASSWORD} (демо-пароль, не для боевого сервера)"
        )
        return result[0], result[1], note

    def _clients(self) -> list[Client]:
        clients = []
        used = set()
        for i in range(25):
            while True:
                phone = f"+7{self.rng.choice(PREFIXES)}{self.rng.randint(0, 9999999):07d}"
                if phone not in used:
                    used.add(phone)
                    break
            name = f"{FIRST_NAMES[i]} {LAST_NAMES[i]}"
            client, _ = services.get_or_create_client(phone, name)
            if i % 5 in (0, 1, 3):  # 60% with Telegram
                client.telegram_chat_id = 500_000_000 + i * 7919
                client.save(update_fields=["telegram_chat_id"])
            clients.append(client)
        return clients

    def _cargo(self, client, truck, places=None):
        self.article_no += 1
        _kind, description = self.rng.choice(GOODS)
        places = places or self.rng.randint(1, 12)
        weight = Decimal(places * self.rng.randint(8, 35)).quantize(Decimal("0.01"))
        amount = Decimal(places * self.rng.choice([350, 420, 500, 650, 800]))
        article = f"CN24{self.rng.randint(10, 99)}-{self.article_no:04d}"
        return services.add_cargo(
            client,
            article,
            description=description,
            places=places,
            weight_kg=weight,
            amount_due=amount,
            truck=truck,
        )

    def _fill(self, truck, clients, total_places):
        cargos, left = [], total_places
        while left > 0:
            places = min(left, self.rng.randint(1, 12))
            cargos.append(self._cargo(self.rng.choice(clients), truck, places))
            left -= places
        return cargos

    def _trip(self, clients, places, departed, arrived=None, eta=None, telegram=None):
        truck = services.create_truck(
            plate=self._plate(), driver_phone=self._driver(), eta=eta,
            departure_date=timezone.localdate(departed),
        )  # fmt: skip
        cargos = self._fill(truck, clients, places)
        services.truck_depart(truck, now=departed)
        send_pending(limit=1000, now=departed + timedelta(minutes=1), telegram=telegram)
        if arrived:
            services.truck_arrive(truck, now=arrived)
            send_pending(limit=1000, now=arrived + timedelta(minutes=1), telegram=telegram)
        return truck, cargos

    def _trucks(self, clients, sklad, telegram):
        now = timezone.now()
        trucks = []

        # Closed: arrived 9 days ago, all picked up.
        truck, cargos = self._trip(
            clients,
            40,
            now - timedelta(days=9, hours=4),
            now - timedelta(days=9),
            telegram=telegram,
        )
        for i, cargo in enumerate(cargos):
            services.cargo_pickup(cargo, sklad, paid=True, now=now - timedelta(days=8 - i % 3))
        trucks.append(truck)

        # Arrived 6 days ago and yesterday, partly picked up.
        for days, picked in ((6, 0.6), (1, 0.3)):
            arrived = now - timedelta(days=days)
            truck, cargos = self._trip(
                clients, 45, arrived - timedelta(hours=4), arrived, telegram=telegram
            )
            for cargo in cargos[: max(1, int(len(cargos) * picked))]:
                services.cargo_pickup(cargo, sklad, paid=True, now=arrived + timedelta(hours=20))
            trucks.append(truck)

        # In transit.
        for ago, eta in (
            (timedelta(hours=1), timedelta(minutes=40)),
            (timedelta(minutes=20), timedelta(hours=2)),
        ):
            truck, _ = self._trip(
                clients, 38, now - ago, eta=now + eta, telegram=telegram
            )  # fmt: skip
            trucks.append(truck)

        # Loading: half full (30 of 60) and just started.
        for places in (30, 3):
            truck = services.create_truck(
                plate=self._plate(), driver_phone=self._driver(), capacity_places=60,
                loading_starts=now - timedelta(hours=2), departure_date=timezone.localdate(),
            )  # fmt: skip
            self._fill(truck, clients, places)
            trucks.append(truck)

        # A few received cargo waiting for a truck.
        for client in clients[:3]:
            self._cargo(client, None)
        return trucks

    def _mark_lines(self, order, manager):
        lines = list(order.lines.order_by("id"))
        tomorrow = timezone.localdate() + timedelta(days=1)
        plan = [
            ("reached", tomorrow, "Приедет завтра после обеда"),
            ("reached", None, "Заберёт на неделе"),
            ("no_answer", None, ""),
            ("no_answer", None, "Абонент недоступен"),
        ]
        for line, (status, promised, comment) in zip(lines, plan, strict=False):
            orders.mark_line(line, status, manager, promised_date=promised, comment=comment)

    # --- helpers / output ---

    def _plate(self):
        letters = "АВЕКМНОРСТУХ"
        pick = lambda: self.rng.choice(letters)  # noqa: E731
        return f"{pick()}{self.rng.randint(100, 999)}{pick()}{pick()}125"

    def _driver(self):
        return f"+7{self.rng.choice(PREFIXES)}{self.rng.randint(0, 9999999):07d}"

    def _summary(self, clients, trucks, order, password_note):
        by_status = {s.label: Truck.objects.filter(status=s).count() for s in Truck.Status}
        cargo = {s.label: Cargo.objects.filter(status=s).count() for s in Cargo.Status}
        notes = {
            s.label: Notification.objects.filter(status=s).count() for s in Notification.Status
        }
        fmt = lambda d: ", ".join(f"{k} {v}" for k, v in d.items())  # noqa: E731
        with_tg = sum(c.telegram_chat_id is not None for c in clients)
        self.stdout.write(f"Клиентов: {len(clients)} (с Telegram {with_tg})")
        self.stdout.write(f"Машины: {fmt(by_status)}")
        self.stdout.write(f"Грузы: {fmt(cargo)}")
        self.stdout.write(f"Уведомления: {fmt(notes)}")
        self.stdout.write(f"{order}: строк {order.lines.count()}")
        self.stdout.write(f"Пользователи manager (Менеджер) и sklad (Склад): {password_note}")

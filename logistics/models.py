"""Domain models (SPEC §5). Status changes go only through ``logistics.services``."""

import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from logistics.phones import InvalidPhone, normalize_phone

DEFAULT_ROUTE = "Уссурийск → Владивосток"


class Counter(models.Model):
    """Named monotonically increasing counter for human-readable codes."""

    name = models.CharField("название", max_length=32, unique=True)
    value = models.PositiveIntegerField("значение", default=0)

    class Meta:
        verbose_name = "счётчик"
        verbose_name_plural = "счётчики"

    def __str__(self) -> str:
        return f"{self.name}={self.value}"

    @classmethod
    def next_value(cls, name: str) -> int:
        """Increment and return the counter; row lock makes it race-safe."""
        with transaction.atomic():
            cls.objects.get_or_create(name=name)
            counter = cls.objects.select_for_update().get(name=name)
            counter.value += 1
            counter.save(update_fields=["value"])
            return counter.value


def generate_link_token() -> str:
    return secrets.token_urlsafe(16)


class Client(models.Model):
    code = models.CharField("код", max_length=16, unique=True, editable=False)
    name = models.CharField("имя", max_length=200, blank=True)
    phone = models.CharField("телефон", max_length=20, unique=True)
    telegram_chat_id = models.BigIntegerField("Telegram chat id", null=True, blank=True)
    telegram_link_token = models.CharField(
        "токен привязки Telegram",
        max_length=64,
        unique=True,
        default=generate_link_token,
        editable=False,
    )
    notes = models.TextField("заметки", blank=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)

    class Meta:
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"
        ordering = ["code"]
        constraints = [
            # One chat belongs to one client; many clients may have no Telegram yet.
            models.UniqueConstraint(
                fields=["telegram_chat_id"],
                condition=models.Q(telegram_chat_id__isnull=False),
                name="client_unique_telegram_chat",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}".strip()

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        with transaction.atomic():
            if not self.code:
                self.code = f"GW-{Counter.next_value('client'):04d}"
            super().save(*args, **kwargs)

    @property
    def has_telegram(self) -> bool:
        return self.telegram_chat_id is not None

    def clean(self):
        super().clean()
        try:
            self.phone = normalize_phone(self.phone)
        except InvalidPhone as exc:
            raise ValidationError({"phone": "Неверный номер телефона"}) from exc


class Truck(models.Model):
    class Status(models.TextChoices):
        LOADING = "loading", "Грузится"
        IN_TRANSIT = "in_transit", "В пути"
        ARRIVED = "arrived", "Прибыла"
        CLOSED = "closed", "Закрыта"

    number = models.CharField("рейс", max_length=16, unique=True, editable=False)
    plate = models.CharField("госномер", max_length=20, blank=True)
    route = models.CharField("маршрут", max_length=120, default=DEFAULT_ROUTE)
    driver_phone = models.CharField("телефон водителя", max_length=20, blank=True)
    status = models.CharField(
        "статус", max_length=16, choices=Status.choices, default=Status.LOADING, db_index=True
    )
    loading_starts = models.DateTimeField("начало погрузки", null=True, blank=True)
    departure_date = models.DateField("плановая дата отправки", null=True, blank=True)
    eta = models.DateTimeField("ожидаемое прибытие", null=True, blank=True)
    departed_at = models.DateTimeField("отправлена", null=True, blank=True)
    arrived_at = models.DateTimeField("прибыла", null=True, blank=True)
    is_public = models.BooleanField("в публичном расписании", default=True)
    comment = models.TextField("комментарий", blank=True)
    created_at = models.DateTimeField("создана", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "машина"
        verbose_name_plural = "машины"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.number

    def save(self, *args, **kwargs):
        if self.driver_phone:
            self.driver_phone = normalize_phone(self.driver_phone)
        with transaction.atomic():
            if not self.number:
                year = timezone.localdate(self.created_at).year
                self.number = f"R-{year}-{Counter.next_value(f'truck-{year}'):03d}"
            super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.driver_phone:
            try:
                self.driver_phone = normalize_phone(self.driver_phone)
            except InvalidPhone as exc:
                raise ValidationError({"driver_phone": "Неверный номер телефона"}) from exc


class Cargo(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Принят"
        LOADED = "loaded", "Погружен"
        IN_TRANSIT = "in_transit", "В пути"
        READY = "ready", "Готов к выдаче"
        PICKED_UP = "picked_up", "Выдан"

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="cargos", verbose_name="клиент"
    )
    truck = models.ForeignKey(
        Truck,
        on_delete=models.PROTECT,
        related_name="cargos",
        null=True,
        blank=True,
        verbose_name="машина",
    )
    article = models.CharField("артикул", max_length=64, db_index=True)
    description = models.CharField("описание", max_length=255, blank=True)
    places = models.PositiveIntegerField("мест", default=1, validators=[MinValueValidator(1)])
    weight_kg = models.DecimalField(
        "вес, кг", max_digits=10, decimal_places=2, null=True, blank=True
    )
    volume_m3 = models.DecimalField(
        "объём, м³", max_digits=10, decimal_places=3, null=True, blank=True
    )
    amount_due = models.DecimalField(
        "к оплате, ₽",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        "статус", max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    ready_at = models.DateTimeField("готов к выдаче с", null=True, blank=True)
    picked_up_at = models.DateTimeField("выдан", null=True, blank=True)
    picked_up_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="выдал",
    )
    paid = models.BooleanField("оплачен", default=False)
    free_storage_until = models.DateField("бесплатное хранение до", null=True, blank=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)

    class Meta:
        verbose_name = "груз"
        verbose_name_plural = "грузы"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["truck", "status"], name="cargo_truck_status_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(places__gte=1), name="cargo_places_gte_1"),
            models.CheckConstraint(
                condition=models.Q(amount_due__gte=0), name="cargo_amount_due_gte_0"
            ),
        ]

    def __str__(self) -> str:
        return f"Арт. {self.article}"


class DailyOrder(models.Model):
    date = models.DateField("дата", unique=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="сформировал",
    )

    class Meta:
        verbose_name = "ордер на день"
        verbose_name_plural = "ордера на день"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Ордер {self.date:%d.%m.%Y}"

    @property
    def is_read_only(self) -> bool:
        """Orders for past local dates are read-only (SPEC §7)."""
        return self.date < timezone.localdate()


class DailyOrderLine(models.Model):
    class CallStatus(models.TextChoices):
        PENDING = "pending", "Не звонили"
        REACHED = "reached", "Позвонили"
        NO_ANSWER = "no_answer", "Не дозвонились"

    order = models.ForeignKey(
        DailyOrder, on_delete=models.CASCADE, related_name="lines", verbose_name="ордер"
    )
    cargo = models.ForeignKey(
        Cargo, on_delete=models.PROTECT, related_name="order_lines", verbose_name="груз"
    )
    article = models.CharField("артикул", max_length=64)
    client_name = models.CharField("клиент", max_length=200, blank=True)
    phone = models.CharField("телефон", max_length=20)
    weight_kg = models.DecimalField(
        "вес, кг", max_digits=10, decimal_places=2, null=True, blank=True
    )
    places = models.PositiveIntegerField("мест", default=1)
    amount_due = models.DecimalField("к оплате, ₽", max_digits=12, decimal_places=2, default=0)
    days_waiting = models.PositiveIntegerField("дней на складе", default=0)
    call_status = models.CharField(
        "статус звонка",
        max_length=16,
        choices=CallStatus.choices,
        default=CallStatus.PENDING,
        db_index=True,
    )
    promised_date = models.DateField("обещал приехать", null=True, blank=True)
    comment = models.TextField("комментарий", blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="изменил",
    )
    updated_at = models.DateTimeField("изменено", auto_now=True)

    class Meta:
        verbose_name = "строка ордера"
        verbose_name_plural = "строки ордера"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "cargo"], name="order_line_unique_cargo")
        ]

    def __str__(self) -> str:
        return f"{self.order} · арт. {self.article}"

    @property
    def is_picked_up(self) -> bool:
        """The cargo was handed out after the line was made; the call status stays as is."""
        return self.cargo.status == Cargo.Status.PICKED_UP


class ContactAttempt(models.Model):
    class Outcome(models.TextChoices):
        REACHED = "reached", "Позвонили"
        NO_ANSWER = "no_answer", "Не дозвонились"

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="contact_attempts", verbose_name="клиент"
    )
    cargo = models.ForeignKey(
        Cargo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_attempts",
        verbose_name="груз",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contact_attempts",
        verbose_name="сотрудник",
    )
    outcome = models.CharField("результат", max_length=16, choices=Outcome.choices)
    promised_date = models.DateField("обещал приехать", null=True, blank=True)
    comment = models.TextField("комментарий", blank=True)
    created_at = models.DateTimeField("когда", auto_now_add=True)

    class Meta:
        verbose_name = "звонок"
        verbose_name_plural = "журнал звонков"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.client} · {self.get_outcome_display()}"

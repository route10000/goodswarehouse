"""Notifications to clients (SPEC §5). Created only via ``notify.services``."""

from django.db import models
from django.utils import timezone


def local_today():
    return timezone.localdate()


class Notification(models.Model):
    class Kind(models.TextChoices):
        DEPARTED = "departed", "Машина выехала"
        ARRIVED = "arrived", "Груз на складе"
        REMINDER = "reminder", "Напоминание"
        STORAGE_ENDING = "storage_ending", "Заканчивается хранение"
        CUSTOM = "custom", "Произвольное"

    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"

    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    client = models.ForeignKey(
        "logistics.Client",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="клиент",
    )
    cargo = models.ForeignKey(
        "logistics.Cargo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="груз",
    )
    truck = models.ForeignKey(
        "logistics.Truck",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="машина",
    )
    kind = models.CharField("тип", max_length=16, choices=Kind.choices)
    channel = models.CharField(
        "канал", max_length=16, choices=Channel.choices, default=Channel.TELEGRAM
    )
    status = models.CharField(
        "статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    text = models.TextField("текст", blank=True)
    attempts = models.PositiveIntegerField("попыток", default=0)
    last_error = models.TextField("последняя ошибка", blank=True)
    created_at = models.DateTimeField("создано", default=timezone.now)
    created_on = models.DateField("дата (местная)", default=local_today, db_index=True)
    sent_at = models.DateTimeField("отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "уведомление"
        verbose_name_plural = "уведомления"
        ordering = ["-created_at"]
        constraints = [
            # SPEC §5: one notification of a kind per (client, truck) per local day.
            models.UniqueConstraint(
                fields=["client", "truck", "kind", "created_on"],
                condition=models.Q(truck__isnull=False) & ~models.Q(kind="custom"),
                name="notification_once_per_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} → {self.client}"

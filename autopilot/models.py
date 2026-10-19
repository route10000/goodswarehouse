"""«Автопилот» (SPEC §14.8): the simulator's state (one row) and its action journal."""

from django.db import models
from django.utils import timezone

from logistics.models import Cargo, Truck


class AutopilotState(models.Model):
    """Singleton (pk=1). ``sim_clock`` is the simulated «now» the rules act at."""

    SPEEDS = (1, 10, 60)
    COUNTERS = (
        "trucks_created",
        "cargo_added",
        "departed",
        "arrived",
        "picked_up",
        "orders_built",
    )

    running = models.BooleanField("работает", default=False)
    speed = models.PositiveSmallIntegerField(
        "скорость", default=1, choices=[(s, f"×{s}") for s in SPEEDS]
    )
    sim_clock = models.DateTimeField("время симуляции", null=True, blank=True)
    last_tick_at = models.DateTimeField("последний шаг (реальное время)", null=True, blank=True)
    # Sim date for which the once-a-day order rule has already run.
    order_checked_on = models.DateField("ордер проверен за", null=True, blank=True)
    trucks_created = models.PositiveIntegerField("рейсов создано", default=0)
    cargo_added = models.PositiveIntegerField("грузов добавлено", default=0)
    departed = models.PositiveIntegerField("отправлено", default=0)
    arrived = models.PositiveIntegerField("прибыло", default=0)
    picked_up = models.PositiveIntegerField("выдано", default=0)
    orders_built = models.PositiveIntegerField("ордеров сформировано", default=0)

    class Meta:
        verbose_name = "состояние автопилота"
        verbose_name_plural = "состояние автопилота"

    def __str__(self) -> str:
        return "Автопилот: работает" if self.running else "Автопилот: пауза"

    @classmethod
    def load(cls) -> "AutopilotState":
        state, _ = cls.objects.get_or_create(pk=1, defaults={"sim_clock": timezone.now()})
        return state


class AutopilotEvent(models.Model):
    class Kind(models.TextChoices):
        TRUCK_CREATED = "truck_created", "Рейс создан"
        CARGO_ADDED = "cargo_added", "Груз принят"
        DEPARTED = "departed", "Рейс отправлен"
        ARRIVED = "arrived", "Рейс прибыл"
        PICKED_UP = "picked_up", "Груз выдан"
        ORDER_BUILT = "order_built", "Ордер сформирован"
        SKIPPED = "skipped", "Пропуск"
        ERROR = "error", "Ошибка"

    created_at = models.DateTimeField("создано", default=timezone.now)
    sim_at = models.DateTimeField("время симуляции")
    kind = models.CharField("тип", max_length=16, choices=Kind.choices)
    message = models.CharField("сообщение", max_length=255)
    truck = models.ForeignKey(
        Truck, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cargo = models.ForeignKey(
        Cargo, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "событие автопилота"
        verbose_name_plural = "журнал автопилота"
        ordering = ["-id"]

    def __str__(self) -> str:
        return self.message

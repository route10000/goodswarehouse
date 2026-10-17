"""Bot runtime state (SPEC §8)."""

from django.db import models


class BotState(models.Model):
    """Tiny key/value store; ``run_bot`` keeps the getUpdates offset here."""

    key = models.CharField("ключ", max_length=64, unique=True)
    value = models.TextField("значение", blank=True)
    updated_at = models.DateTimeField("изменено", auto_now=True)

    class Meta:
        verbose_name = "состояние бота"
        verbose_name_plural = "состояние бота"

    def __str__(self) -> str:
        return f"{self.key}={self.value}"

    @classmethod
    def get_value(cls, key: str, default: str | None = None) -> str | None:
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set_value(cls, key: str, value: str) -> None:
        cls.objects.update_or_create(key=key, defaults={"value": value})

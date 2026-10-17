"""One Telegram chat belongs to at most one client (SPEC §8, bot linking)."""

from django.db import migrations, models


def clear_duplicate_chats(apps, schema_editor):
    """Keep the chat on the newest client, clear it on older duplicates."""
    Client = apps.get_model("logistics", "Client")
    duplicates = (
        Client.objects.exclude(telegram_chat_id=None)
        .values("telegram_chat_id")
        .annotate(n=models.Count("id"), keep=models.Max("id"))
        .filter(n__gt=1)
    )
    for row in duplicates:
        Client.objects.filter(telegram_chat_id=row["telegram_chat_id"]).exclude(
            pk=row["keep"]
        ).update(telegram_chat_id=None)


class Migration(migrations.Migration):
    dependencies = [
        ("logistics", "0002_staff_groups"),
    ]

    operations = [
        migrations.RunPython(clear_duplicate_chats, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="client",
            constraint=models.UniqueConstraint(
                condition=models.Q(("telegram_chat_id__isnull", False)),
                fields=("telegram_chat_id",),
                name="client_unique_telegram_chat",
            ),
        ),
    ]

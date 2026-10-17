"""Deep links for linking a client's Telegram chat (used by the staff panel)."""

from django.conf import settings
from django.db import transaction

from logistics.models import Client, generate_link_token


def _unique_token() -> str:
    while True:
        token = generate_link_token()
        if not Client.objects.filter(telegram_link_token=token).exists():
            return token


def deep_link(client: Client) -> str | None:
    """``https://t.me/<bot>?start=<token>``, or ``None`` if the bot username is not set.

    Creates the client's link token once if it is empty; an existing token is kept.
    """
    username = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if not username:
        return None
    if not client.telegram_link_token:
        with transaction.atomic():
            fresh = Client.objects.select_for_update().get(pk=client.pk)
            if not fresh.telegram_link_token:
                fresh.telegram_link_token = _unique_token()
                Client.objects.filter(pk=client.pk).update(
                    telegram_link_token=fresh.telegram_link_token
                )
            client.telegram_link_token = fresh.telegram_link_token
    return f"https://t.me/{username}?start={client.telegram_link_token}"

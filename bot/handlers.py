"""Telegram bot logic (SPEC §8) as pure functions: an ``Update`` dict in, replies out.

No network here: ``run_bot`` sends the replies. Texts use HTML parse mode, so every
piece of client, cargo and settings data is HTML-escaped. Only private chats are served.
The bot never changes cargo or truck statuses; it only links and unlinks chats.
"""

from dataclasses import dataclass
from decimal import Decimal
from html import escape

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from logistics.models import Cargo, Client, Truck
from logistics.phones import InvalidPhone, normalize_phone
from notify.messages import format_date, format_money, format_weight, plural_ru

BTN_CARGO = "📦 Мои грузы"
BTN_SCHEDULE = "🚚 Расписание машин"
BTN_ADDRESS = "📍 Адрес и часы"
BTN_SHARE = "📱 Поделиться номером"

MAIN_MENU = {
    "keyboard": [[{"text": BTN_CARGO}], [{"text": BTN_SCHEDULE}], [{"text": BTN_ADDRESS}]],
    "resize_keyboard": True,
    "is_persistent": True,
}
SHARE_KEYBOARD = {
    "keyboard": [[{"text": BTN_SHARE, "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}

ASK_PHONE = (
    "Чтобы получать сообщения о грузе, поделитесь номером телефона, "
    "который вы называли менеджеру. Нажмите кнопку ниже."
)
BAD_TOKEN = "Ссылка устарела или неверна. " + ASK_PHONE
NOT_OWN_CONTACT = "Отправьте, пожалуйста, свой номер: нажмите кнопку «Поделиться номером»."
STOPPED = "Уведомления отключены. Чтобы включить их снова, отправьте /start."
NO_CARGO = "Сейчас грузов нет"
NO_TRUCKS = "Сейчас машин в расписании нет"
USE_MENU = "Выберите пункт меню."


@dataclass(frozen=True)
class Reply:
    chat_id: int
    text: str
    reply_markup: dict | None = None


# ---------- linking ----------


def _first_name(client: Client) -> str:
    parts = client.name.split()
    return parts[0] if parts else ""


def _greeting(client: Client) -> str:
    name = _first_name(client)
    hello = f"Здравствуйте, {escape(name)}." if name else "Здравствуйте."
    return (
        f"{hello} Telegram привязан к клиенту {escape(client.code)}.\n"
        "Сюда придут сообщения о вашем грузе."
    )


def _link(client: Client, chat_id: int) -> None:
    """Attach ``chat_id`` to ``client``; one chat belongs to one client."""
    with transaction.atomic():
        Client.objects.filter(telegram_chat_id=chat_id).exclude(pk=client.pk).update(
            telegram_chat_id=None
        )
        Client.objects.filter(pk=client.pk).update(telegram_chat_id=chat_id)
    client.telegram_chat_id = chat_id


def _linked_client(chat_id: int) -> Client | None:
    return Client.objects.filter(telegram_chat_id=chat_id).first()


def _contact_phone(raw: str) -> str | None:
    """Telegram sends ``79141234567`` (no plus); also accept any format we normalize."""
    raw = (raw or "").strip()
    candidates = [raw] if raw.startswith("+") else [f"+{raw}", raw]
    for candidate in candidates:
        try:
            return normalize_phone(candidate)
        except InvalidPhone:
            continue
    return None


# ---------- menu texts ----------


def _cargo_where(cargo: Cargo) -> str:
    if cargo.status == Cargo.Status.READY:
        if cargo.free_storage_until:
            return f"на складе, бесплатное хранение до {format_date(cargo.free_storage_until)}"
        return "на складе"
    if cargo.truck is None:
        return ""
    where = f"рейс {escape(cargo.truck.number)}"
    if cargo.truck.eta:
        eta = timezone.localtime(cargo.truck.eta)
        where += f", прибытие {format_date(eta.date())} около {eta:%H:%M}"
    return where


def _cargo_block(cargo: Cargo) -> str:
    parts = []
    if cargo.description:
        parts.append(escape(cargo.description))
    parts.append(f"{cargo.places} {plural_ru(cargo.places, 'место', 'места', 'мест')}")
    if cargo.weight_kg is not None:
        parts.append(f"{format_weight(cargo.weight_kg)} кг")
    lines = [f"• Арт. {escape(cargo.article)} — {', '.join(parts)}"]
    status = escape(cargo.get_status_display())
    where = _cargo_where(cargo)
    lines.append(f"{status} · {where}" if where else status)
    if Decimal(cargo.amount_due) > 0 and not cargo.paid:
        lines.append(f"К оплате: {format_money(cargo.amount_due)} ₽")
    return "\n".join(lines)


def my_cargo_text(client: Client) -> str:
    cargos = (
        Cargo.objects.filter(client=client)
        .exclude(status=Cargo.Status.PICKED_UP)
        .select_related("truck")
        .order_by("created_at", "id")
    )
    blocks = [_cargo_block(c) for c in cargos]
    return "\n\n".join(blocks) if blocks else NO_CARGO


def _truck_line(truck: Truck) -> str:
    line = f"• {escape(truck.number)} — {escape(truck.get_status_display())}"
    if truck.status == Truck.Status.LOADING and truck.departure_date:
        line += f", отправка {format_date(truck.departure_date)}"
    elif truck.status == Truck.Status.IN_TRANSIT and truck.eta:
        eta = timezone.localtime(truck.eta)
        line += f", прибытие {format_date(eta.date())} около {eta:%H:%M}"
    return line


def _truck_sort_key(truck: Truck):
    if truck.status == Truck.Status.IN_TRANSIT:
        when = truck.eta.timestamp() if truck.eta else float("inf")
        return (0, when, truck.created_at.timestamp())
    when = truck.departure_date.toordinal() if truck.departure_date else float("inf")
    return (1, when, truck.created_at.timestamp())


def schedule_text() -> str:
    trucks = Truck.objects.filter(
        is_public=True, status__in=[Truck.Status.LOADING, Truck.Status.IN_TRANSIT]
    )
    lines = [_truck_line(t) for t in sorted(trucks, key=_truck_sort_key)]
    if not lines:
        return NO_TRUCKS
    route = escape(f"Уссурийск → {settings.WAREHOUSE_CITY}")
    return "\n".join([f"🚚 Ближайшие машины ({route})", "", *lines])


def address_text() -> str:
    lines = []
    if settings.WAREHOUSE_ADDRESS:
        lines.append(f"📍 {escape(settings.WAREHOUSE_ADDRESS)}")
    if settings.WAREHOUSE_HOURS:
        lines.append(f"🕘 {escape(settings.WAREHOUSE_HOURS)}")
    if settings.WAREHOUSE_PHONE:
        lines.append(f"📞 {escape(settings.WAREHOUSE_PHONE)}")
    return "\n".join(lines) if lines else "Адрес склада уточните у менеджера."


# ---------- dispatch ----------


def _on_start(chat_id: int, args: str) -> Reply:
    token = args.strip()
    if token:
        client = Client.objects.filter(telegram_link_token=token).first()
        if client is None:
            return Reply(chat_id, BAD_TOKEN, SHARE_KEYBOARD)
        _link(client, chat_id)
        return Reply(chat_id, _greeting(client), MAIN_MENU)
    client = _linked_client(chat_id)
    if client is not None:
        return Reply(chat_id, _greeting(client), MAIN_MENU)
    return Reply(chat_id, ASK_PHONE, SHARE_KEYBOARD)


def _on_contact(chat_id: int, sender_id, contact: dict) -> Reply:
    if sender_id is None or contact.get("user_id") != sender_id:
        return Reply(chat_id, NOT_OWN_CONTACT, SHARE_KEYBOARD)
    phone = _contact_phone(contact.get("phone_number", ""))
    client = Client.objects.filter(phone=phone).first() if phone else None
    if client is None:
        text = f"Номер не найден. Обратитесь к менеджеру: {escape(settings.WAREHOUSE_PHONE)}"
        return Reply(chat_id, text.rstrip(": "), REMOVE_KEYBOARD)
    _link(client, chat_id)
    return Reply(chat_id, _greeting(client), MAIN_MENU)


def _on_stop(chat_id: int) -> Reply:
    Client.objects.filter(telegram_chat_id=chat_id).update(telegram_chat_id=None)
    return Reply(chat_id, STOPPED, REMOVE_KEYBOARD)


def _on_text(chat_id: int, text: str) -> Reply:
    client = _linked_client(chat_id)
    keyboard = MAIN_MENU if client else SHARE_KEYBOARD
    if text == BTN_SCHEDULE:
        return Reply(chat_id, schedule_text(), keyboard)
    if text == BTN_ADDRESS:
        return Reply(chat_id, address_text(), keyboard)
    if client is None:
        return Reply(chat_id, ASK_PHONE, SHARE_KEYBOARD)
    if text == BTN_CARGO:
        return Reply(chat_id, my_cargo_text(client), MAIN_MENU)
    return Reply(chat_id, USE_MENU, MAIN_MENU)


def handle_update(update: dict) -> list[Reply]:
    """Replies for one Telegram ``Update``; unsupported updates give an empty list."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return []
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return []

    contact = msg.get("contact")
    if isinstance(contact, dict):
        return [_on_contact(chat_id, (msg.get("from") or {}).get("id"), contact)]

    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        command, _, args = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "/start":
            return [_on_start(chat_id, args)]
        if command == "/stop":
            return [_on_stop(chat_id)]
    return [_on_text(chat_id, text)]

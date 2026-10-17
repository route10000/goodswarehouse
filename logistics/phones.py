"""Phone helpers: every phone is stored in E.164 (``+79141234567``)."""

import phonenumbers

DEFAULT_REGION = "RU"


class InvalidPhone(ValueError):
    """Raised when a string cannot be parsed as a valid phone number."""


def normalize_phone(raw: str | None) -> str:
    """Return ``raw`` in E.164. Accepts ``8 914 …``, ``+7(914)…``, ``914…``."""
    text = (raw or "").strip()
    if not text:
        raise InvalidPhone("Пустой номер телефона")
    try:
        number = phonenumbers.parse(text, DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhone(f"Неверный номер телефона: {text}") from exc
    if not phonenumbers.is_valid_number(number):
        raise InvalidPhone(f"Неверный номер телефона: {text}")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def phone_last4(phone: str) -> str:
    """Last four digits of a phone, normalizing it first."""
    return normalize_phone(phone)[-4:]

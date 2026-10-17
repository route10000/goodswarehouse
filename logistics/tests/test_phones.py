import pytest

from logistics.phones import InvalidPhone, normalize_phone, phone_last4


@pytest.mark.parametrize(
    "raw",
    [
        "8 914 123 45 67",
        "+7(914)123-45-67",
        "+7 914 123-45-67",
        "9141234567",
        "79141234567",
        "  8(914)1234567  ",
    ],
)
def test_normalize_phone_accepts_russian_formats(raw):
    assert normalize_phone(raw) == "+79141234567"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "12345", "8914123456", None])
def test_normalize_phone_rejects_invalid(raw):
    with pytest.raises(InvalidPhone):
        normalize_phone(raw)


def test_invalid_phone_is_value_error():
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_phone_last4():
    assert phone_last4("+79141234567") == "4567"
    assert phone_last4("8 914 123 45 67") == "4567"

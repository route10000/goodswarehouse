from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError

from logistics.models import Cargo, Client, Counter, Truck
from logistics.phones import InvalidPhone

pytestmark = pytest.mark.django_db

VLAT = ZoneInfo("Asia/Vladivostok")


def test_client_codes_are_sequential():
    a = Client.objects.create(name="А", phone="+79141234567")
    b = Client.objects.create(name="Б", phone="+79141234568")
    assert a.code == "GW-0001"
    assert b.code == "GW-0002"


def test_client_code_not_reused_after_deletion():
    Client.objects.create(name="А", phone="+79141234567")
    b = Client.objects.create(name="Б", phone="+79141234568")
    b.delete()
    c = Client.objects.create(name="В", phone="+79141234569")
    assert c.code == "GW-0003"
    assert Counter.objects.get(name="client").value == 3


def test_client_code_kept_on_resave():
    a = Client.objects.create(name="А", phone="+79141234567")
    a.name = "АА"
    a.save()
    a.refresh_from_db()
    assert a.code == "GW-0001"
    assert Counter.objects.get(name="client").value == 1


def test_client_code_not_editable():
    assert Client._meta.get_field("code").editable is False
    assert Truck._meta.get_field("number").editable is False


def test_client_save_normalizes_phone():
    c = Client.objects.create(name="А", phone="8 914 123 45 67")
    c.refresh_from_db()
    assert c.phone == "+79141234567"


def test_client_save_rejects_invalid_phone():
    with pytest.raises(InvalidPhone):
        Client.objects.create(name="А", phone="12345")
    assert not Client.objects.exists()


def test_client_clean_normalizes_phone_and_reports_error():
    c = Client(name="А", phone="+7(914)123-45-67")
    c.full_clean()
    assert c.phone == "+79141234567"
    bad = Client(name="Б", phone="abc")
    with pytest.raises(ValidationError) as exc:
        bad.full_clean()
    assert "phone" in exc.value.message_dict


def test_client_link_token_unique_and_has_telegram():
    a = Client.objects.create(name="А", phone="+79141234567")
    b = Client.objects.create(name="Б", phone="+79141234568", telegram_chat_id=12345)
    assert a.telegram_link_token and a.telegram_link_token != b.telegram_link_token
    assert a.has_telegram is False
    assert b.has_telegram is True


def test_truck_numbers_sequential_per_year():
    t1 = Truck.objects.create(created_at=datetime(2026, 3, 1, 10, tzinfo=VLAT))
    t2 = Truck.objects.create(created_at=datetime(2026, 12, 31, 23, 30, tzinfo=VLAT))
    t3 = Truck.objects.create(created_at=datetime(2027, 1, 1, 0, 30, tzinfo=VLAT))
    t4 = Truck.objects.create(created_at=datetime(2026, 5, 1, 10, tzinfo=VLAT))
    assert [t1.number, t2.number, t3.number, t4.number] == [
        "R-2026-001",
        "R-2026-002",
        "R-2027-001",
        "R-2026-003",
    ]


def test_truck_number_not_reused_after_deletion():
    when = datetime(2026, 3, 1, 10, tzinfo=VLAT)
    Truck.objects.create(created_at=when)
    t2 = Truck.objects.create(created_at=when)
    t2.delete()
    t3 = Truck.objects.create(created_at=when)
    assert t3.number == "R-2026-003"


def test_truck_defaults():
    t = Truck.objects.create()
    assert t.status == Truck.Status.LOADING
    assert t.route == "Уссурийск → Владивосток"
    assert t.is_public is True


def test_cargo_defaults_and_labels():
    c = Client.objects.create(name="А", phone="+79141234567")
    cargo = Cargo.objects.create(client=c, article="88123")
    assert cargo.status == Cargo.Status.RECEIVED
    assert cargo.places == 1
    assert cargo.amount_due == 0
    assert cargo.get_status_display() == "Принят"
    assert Truck.Status.IN_TRANSIT.label == "В пути"


def test_cargo_places_must_be_positive():
    c = Client.objects.create(name="А", phone="+79141234567")
    cargo = Cargo(client=c, article="88123", places=0)
    with pytest.raises(ValidationError):
        cargo.full_clean()

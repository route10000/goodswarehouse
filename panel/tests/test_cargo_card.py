"""Cargo card ``/panel/cargo/<id>/``: fields, history, notifications, calls and editing."""

import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from logistics import services
from logistics.models import Cargo, ContactAttempt
from notify.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def png(name="photo.png"):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture
def cargo(ivan, truck):
    return services.add_cargo(
        ivan,
        "CARD-1",
        description="Куртки",
        places=3,
        weight_kg=Decimal("12.5"),
        volume_m3=Decimal("0.4"),
        amount_due=Decimal("1500"),
        track_number="YT123456",
        seller="Taobao Shop",
        packaging=Cargo.Packaging.BAG,
        fragile=True,
        note="Не кантовать",
        photo=png(),
        truck=truck,
    )


@pytest.fixture
def travelled(cargo, truck, manager):
    """The cargo's whole way: loaded → departed → arrived → picked up by the manager."""
    start = timezone.now() + timedelta(minutes=1)
    services.truck_depart(truck, now=start)
    services.truck_arrive(truck, now=start + timedelta(hours=3))
    cargo.refresh_from_db()
    services.cargo_pickup(cargo, manager, paid=True, now=start + timedelta(days=1))
    cargo.refresh_from_db()
    return cargo


def card(http, cargo):
    return http.get(f"/panel/cargo/{cargo.pk}/")


# ---------- content ----------


def test_card_shows_all_fields(mgr, cargo, ivan, truck):
    response = card(mgr, cargo)

    assert response.status_code == 200
    page = response.content.decode()
    for text in (
        "CARD-1",
        "Куртки",
        "YT123456",
        "Taobao Shop",
        "Мешок",
        "хрупкое",
        "Не кантовать",
        "Погружен",
        f"/panel/clients/{ivan.pk}/",
        f"/panel/trucks/{truck.pk}/",
        truck.number,
        cargo.photo.url,
    ):
        assert text in page, text


def test_card_404(mgr):
    assert mgr.get("/panel/cargo/999999/").status_code == 404


def test_card_requires_login(client, cargo):
    response = client.get(f"/panel/cargo/{cargo.pk}/")
    assert response.status_code == 302
    assert "/panel/login/" in response["Location"]


def test_history_in_order(mgr, travelled, manager):
    response = card(mgr, travelled)
    steps = response.context["history"]

    assert [step["key"] for step in steps] == [
        "received",
        "loaded",
        "departed",
        "ready",
        "picked_up",
    ]
    assert all(step["done"] for step in steps)
    times = [step["at"] for step in steps if step["at"]]
    assert times == sorted(times)
    assert manager.get_username() in response.content.decode()


def test_history_future_steps_not_done(mgr, cargo):
    steps = card(mgr, cargo).context["history"]

    assert [step["done"] for step in steps] == [True, True, False, False, False]


def test_card_lists_notifications_about_this_truck_and_calls(mgr, manager, travelled, ivan):
    other_truck = services.create_truck()
    Notification.objects.create(client=ivan, truck=other_truck, kind="custom", text="чужая")
    ContactAttempt.objects.create(
        client=ivan, cargo=travelled, staff=manager, outcome="reached", comment="приедет завтра"
    )
    ContactAttempt.objects.create(client=ivan, staff=manager, outcome="no_answer", comment="иное")

    response = card(mgr, travelled)

    kinds = sorted(n.kind for n in response.context["notifications"])
    assert kinds == ["arrived", "departed"]
    assert [a.comment for a in response.context["attempts"]] == ["приедет завтра"]


# ---------- permissions ----------


def test_warehouse_can_view_but_not_edit(wh, cargo):
    page = card(wh, cargo).content.decode()

    assert f"/panel/cargo/{cargo.pk}/edit/" not in page
    assert wh.get(f"/panel/cargo/{cargo.pk}/edit/").status_code == 403
    assert wh.post(f"/panel/cargo/{cargo.pk}/edit/", {"article": "X"}).status_code == 403
    cargo.refresh_from_db()
    assert cargo.article == "CARD-1"


def test_manager_sees_edit_button(mgr, cargo):
    assert f"/panel/cargo/{cargo.pk}/edit/" in card(mgr, cargo).content.decode()


def test_edit_404(mgr):
    assert mgr.get("/panel/cargo/999999/edit/").status_code == 404


# ---------- edit ----------


def edit_payload(**data):
    payload = {
        "article": "CARD-1",
        "description": "Куртки",
        "places": "3",
        "weight_kg": "12,5",
        "volume_m3": "0,4",
        "amount_due": "1 500",
        "track_number": "YT123456",
        "seller": "Taobao Shop",
        "packaging": "bag",
        "fragile": "on",
        "note": "Не кантовать",
    }
    payload.update(data)
    return payload


def test_edit_form_prefilled(mgr, cargo):
    response = mgr.get(f"/panel/cargo/{cargo.pk}/edit/")

    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["article"] == "CARD-1"
    assert form.initial["track_number"] == "YT123456"
    assert 'enctype="multipart/form-data"' in response.content.decode()


def test_edit_updates_fields(mgr, cargo):
    response = mgr.post(
        f"/panel/cargo/{cargo.pk}/edit/",
        edit_payload(article=" NEW-1 ", places="5", amount_due="2 000,50", fragile=""),
    )

    assert response.status_code == 302
    assert response["Location"] == f"/panel/cargo/{cargo.pk}/"
    cargo.refresh_from_db()
    assert cargo.article == "NEW-1"
    assert cargo.places == 5
    assert cargo.amount_due == Decimal("2000.50")
    assert cargo.fragile is False
    assert cargo.status == Cargo.Status.LOADED


def test_edit_invalid_keeps_data(mgr, cargo):
    response = mgr.post(f"/panel/cargo/{cargo.pk}/edit/", edit_payload(places="0"))

    assert response.status_code == 200
    assert response.context["form"].errors["places"]
    cargo.refresh_from_db()
    assert cargo.places == 3


def test_edit_replaces_photo(mgr, cargo):
    old = cargo.photo.name
    mgr.post(f"/panel/cargo/{cargo.pk}/edit/", edit_payload(photo=png("new.png")))

    cargo.refresh_from_db()
    assert cargo.photo.name != old
    assert "new" in cargo.photo.name
    assert cargo.photo.storage.exists(cargo.photo.name)
    assert not cargo.photo.storage.exists(old)


def test_edit_removes_photo(mgr, cargo):
    old = cargo.photo.name
    mgr.post(f"/panel/cargo/{cargo.pk}/edit/", edit_payload(remove_photo="on"))

    cargo.refresh_from_db()
    assert not cargo.photo
    assert not cargo.photo.storage.exists(old)


def test_edit_keeps_photo_when_untouched(mgr, cargo):
    old = cargo.photo.name
    mgr.post(f"/panel/cargo/{cargo.pk}/edit/", edit_payload(note="другое"))

    cargo.refresh_from_db()
    assert cargo.photo.name == old
    assert cargo.note == "другое"

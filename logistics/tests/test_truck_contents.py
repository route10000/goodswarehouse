"""Cargo's own fields, editing, and moving cargo in and out of a loading truck."""

import io
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from logistics import services
from logistics.models import Cargo, Truck
from logistics.services import InvalidTransition

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def png(name="photo.png"):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture
def ivan():
    client, _ = services.get_or_create_client("+79141234567", "Иван")
    return client


@pytest.fixture
def truck():
    return services.create_truck()


def statuses(*cargos):
    return [Cargo.objects.values_list("status", "truck_id").get(pk=c.pk) for c in cargos]


# ---------- new fields ----------


def test_cargo_defaults_for_new_fields(ivan):
    cargo = services.add_cargo(ivan, "A1")

    assert cargo.track_number == ""
    assert cargo.seller == ""
    assert cargo.packaging == Cargo.Packaging.BOX
    assert cargo.fragile is False
    assert cargo.note == ""
    assert not cargo.photo


def test_add_cargo_saves_new_fields(ivan, truck):
    cargo = services.add_cargo(
        ivan,
        "A1",
        truck=truck,
        track_number=" YT123456789CN ",
        seller="Taobao · Лавка Ли",
        packaging=Cargo.Packaging.BAG,
        fragile=True,
        note="Стекло, не кантовать",
        photo=png(),
    )

    cargo.refresh_from_db()
    assert cargo.track_number == "YT123456789CN"
    assert cargo.seller == "Taobao · Лавка Ли"
    assert cargo.packaging == "bag"
    assert cargo.fragile is True
    assert cargo.note == "Стекло, не кантовать"
    assert cargo.photo.name.startswith("cargo/")
    assert cargo.photo.name.endswith(".png")
    assert cargo.status == Cargo.Status.LOADED


def test_add_cargo_rejects_unknown_packaging(ivan):
    with pytest.raises(ValidationError):
        services.add_cargo(ivan, "A1", packaging="crate")

    assert not Cargo.objects.exists()


def test_packaging_labels():
    assert dict(Cargo.Packaging.choices) == {
        "box": "Коробка",
        "bag": "Мешок",
        "pallet": "Паллета",
        "other": "Другое",
    }


def test_track_number_is_indexed():
    assert Cargo._meta.get_field("track_number").db_index


# ---------- update_cargo ----------


def test_update_cargo_edits_plain_fields(ivan, truck):
    cargo = services.add_cargo(ivan, "A1", truck=truck)

    services.update_cargo(
        cargo,
        article=" A1-bis ",
        places=4,
        weight_kg=Decimal("3.5"),
        track_number="LP00123",
        seller="1688",
        packaging=Cargo.Packaging.PALLET,
        fragile=True,
        note="Паллета на 4 места",
    )

    stored = Cargo.objects.get(pk=cargo.pk)
    assert stored.article == "A1-bis"
    assert stored.places == 4
    assert stored.weight_kg == Decimal("3.50")
    assert stored.track_number == "LP00123"
    assert stored.seller == "1688"
    assert stored.packaging == "pallet"
    assert stored.fragile is True
    assert stored.note == "Паллета на 4 места"
    assert stored.status == Cargo.Status.LOADED
    assert stored.truck == truck
    assert cargo.article == "A1-bis"  # the caller's instance is refreshed


def test_update_cargo_validates_and_changes_nothing_on_error(ivan):
    cargo = services.add_cargo(ivan, "A1", places=2)

    with pytest.raises(ValidationError):
        services.update_cargo(cargo, places=0, note="x")

    stored = Cargo.objects.get(pk=cargo.pk)
    assert stored.places == 2
    assert stored.note == ""
    assert cargo.places == 2


@pytest.mark.parametrize("field", ["status", "truck", "ready_at", "picked_up_by", "id"])
def test_update_cargo_refuses_status_and_system_fields(ivan, field):
    cargo = services.add_cargo(ivan, "A1")

    with pytest.raises(ValueError):
        services.update_cargo(cargo, **{field: None})

    assert Cargo.objects.get(pk=cargo.pk).status == Cargo.Status.RECEIVED


def test_update_cargo_sets_photo(ivan):
    cargo = services.add_cargo(ivan, "A1")

    services.update_cargo(cargo, photo=png("p.png"))

    assert Cargo.objects.get(pk=cargo.pk).photo.name.startswith("cargo/")


# ---------- unassign_from_truck ----------


def test_unassign_returns_cargo_to_received(ivan, truck):
    cargo = services.add_cargo(ivan, "A1", truck=truck)

    services.unassign_from_truck(cargo)

    assert statuses(cargo) == [(Cargo.Status.RECEIVED, None)]
    assert cargo.truck is None and cargo.status == Cargo.Status.RECEIVED


def test_unassign_refused_after_departure(ivan, truck):
    cargo = services.add_cargo(ivan, "A1", truck=truck)
    services.truck_depart(truck)

    with pytest.raises(InvalidTransition):
        services.unassign_from_truck(cargo)

    assert statuses(cargo) == [(Cargo.Status.IN_TRANSIT, truck.pk)]


def test_unassign_refused_for_cargo_without_truck(ivan):
    cargo = services.add_cargo(ivan, "A1")

    with pytest.raises(InvalidTransition):
        services.unassign_from_truck(cargo)

    assert statuses(cargo) == [(Cargo.Status.RECEIVED, None)]


def test_unassign_reads_the_locked_row_not_the_callers_copy(ivan, truck):
    """A stale instance that still says «loaded» must not undo a departure."""
    cargo = services.add_cargo(ivan, "A1", truck=truck)
    stale = Cargo.objects.get(pk=cargo.pk)
    services.truck_depart(truck)

    with pytest.raises(InvalidTransition):
        services.unassign_from_truck(stale)

    assert stale.status == Cargo.Status.LOADED  # caller's copy untouched on error
    assert statuses(cargo) == [(Cargo.Status.IN_TRANSIT, truck.pk)]


def test_unassign_locks_truck_and_cargo_rows(ivan, truck, monkeypatch):
    cargo = services.add_cargo(ivan, "A1", truck=truck)
    locked = []
    real = services._locked
    monkeypatch.setattr(
        services, "_locked", lambda model, pk: locked.append(model) or real(model, pk)
    )

    services.unassign_from_truck(cargo)

    assert locked == [Truck, Cargo]


# ---------- assign_many / unassign_many ----------


def test_assign_many_loads_all_received(ivan, truck):
    a = services.add_cargo(ivan, "A1")
    b = services.add_cargo(ivan, "A2")

    count = services.assign_many([a.pk, b.pk, a.pk], truck)

    assert count == 2
    assert statuses(a, b) == [(Cargo.Status.LOADED, truck.pk)] * 2


def test_assign_many_is_all_or_nothing(ivan, truck):
    other = services.create_truck()
    a = services.add_cargo(ivan, "A1")
    b = services.add_cargo(ivan, "A2", truck=other)

    with pytest.raises(InvalidTransition):
        services.assign_many([a.pk, b.pk], truck)

    assert statuses(a, b) == [(Cargo.Status.RECEIVED, None), (Cargo.Status.LOADED, other.pk)]


def test_assign_many_refuses_missing_cargo(ivan, truck):
    a = services.add_cargo(ivan, "A1")

    with pytest.raises(InvalidTransition):
        services.assign_many([a.pk, 99999], truck)

    assert statuses(a) == [(Cargo.Status.RECEIVED, None)]


def test_assign_many_refuses_truck_that_left(ivan, truck):
    a = services.add_cargo(ivan, "A1")
    services.truck_depart(truck)

    with pytest.raises(InvalidTransition):
        services.assign_many([a.pk], truck)

    assert statuses(a) == [(Cargo.Status.RECEIVED, None)]


def test_assign_many_with_nothing_selected(truck):
    assert services.assign_many([], truck) == 0


def test_unassign_many_returns_all(ivan, truck):
    a = services.add_cargo(ivan, "A1", truck=truck)
    b = services.add_cargo(ivan, "A2", truck=truck)
    keep = services.add_cargo(ivan, "A3", truck=truck)

    count = services.unassign_many([a.pk, b.pk], truck)

    assert count == 2
    assert statuses(a, b, keep) == [
        (Cargo.Status.RECEIVED, None),
        (Cargo.Status.RECEIVED, None),
        (Cargo.Status.LOADED, truck.pk),
    ]


def test_unassign_many_is_all_or_nothing(ivan, truck):
    other = services.create_truck()
    a = services.add_cargo(ivan, "A1", truck=truck)
    foreign = services.add_cargo(ivan, "B1", truck=other)

    with pytest.raises(InvalidTransition):
        services.unassign_many([a.pk, foreign.pk], truck)

    assert statuses(a, foreign) == [
        (Cargo.Status.LOADED, truck.pk),
        (Cargo.Status.LOADED, other.pk),
    ]


def test_unassign_many_refused_after_departure(ivan, truck):
    a = services.add_cargo(ivan, "A1", truck=truck)
    services.truck_depart(truck)

    with pytest.raises(InvalidTransition):
        services.unassign_many([a.pk], truck)

    assert statuses(a) == [(Cargo.Status.IN_TRANSIT, truck.pk)]


# ---------- admin ----------


def test_admin_shows_new_fields(ivan, admin_client):
    cargo = services.add_cargo(ivan, "A1", track_number="YT1", seller="1688")

    change = admin_client.get(f"/admin/logistics/cargo/{cargo.pk}/change/").content.decode()
    listing = admin_client.get("/admin/logistics/cargo/", {"q": "YT1"}).content.decode()

    for name in ("track_number", "seller", "packaging", "fragile", "note", "photo"):
        assert f'name="{name}"' in change
    assert "YT1" in listing
    assert 'enctype="multipart/form-data"' in change

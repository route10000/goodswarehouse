"""Truck card: «Кузов» widget, removing cargo, loading received cargo, new cargo fields."""

import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from logistics import services
from logistics.models import Cargo

from .helpers import page_without_csrf

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def received(ivan, petr):
    """Cargo accepted in Ussuriysk and waiting for a truck."""
    return [
        services.add_cargo(ivan, "R-1", track_number="YT777", places=2),
        services.add_cargo(petr, "R-2", seller="1688"),
    ]


def ids(*cargos):
    return [str(c.pk) for c in cargos]


def state(cargo):
    return Cargo.objects.values_list("status", "truck_id").get(pk=cargo.pk)


# ---------- card ----------


def test_loading_card_for_manager_has_contents_controls(mgr, loaded_truck, received):
    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    page = response.content.decode()
    a1 = loaded_truck.cargos.get(article="A1")
    assert 'id="truck-body"' in page and "Кузов" in page
    assert "6 / 60" in page
    assert response.context["body"]["clients"] == 2
    assert (
        f'form="cargo-bulk" value="{a1.pk}"' in page or f'value="{a1.pk}" form="cargo-bulk"' in page
    )
    assert "Убрать из машины" in page
    assert ">Убрать<" in page
    assert "Добавить из принятых" in page
    assert 'id="received-count">2<' in page
    assert "Новый груз" in page
    assert "Импорт Excel" in page and "Ввести вручную" in page
    assert f'data-href="/panel/cargo/{a1.pk}/"' in page
    assert "panel-trucks.css" in page


def test_card_meta_line_shows_track_packaging_and_fragile(mgr, truck, ivan):
    services.add_cargo(
        ivan, "F-1", truck=truck, track_number="YT123", packaging="bag", fragile=True
    )

    page = mgr.get(f"/panel/trucks/{truck.pk}/").content.decode()

    assert "YT123" in page
    assert "Мешок" in page
    assert "хрупкое" in page


def test_loading_card_is_read_only_for_warehouse(wh, loaded_truck, received):
    response = wh.get(f"/panel/trucks/{loaded_truck.pk}/")

    page = page_without_csrf(response)
    assert 'id="truck-body"' in page
    assert "cargo-bulk" not in page
    assert ">Убрать<" not in page
    assert "Добавить из принятых" not in page
    assert "Новый груз" not in page
    assert "Ввести вручную" not in page


def test_non_loading_card_has_no_controls_for_manager(mgr, loaded_truck, received):
    services.truck_depart(loaded_truck)

    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/")

    page = page_without_csrf(response)
    assert 'id="truck-body"' in page
    assert "cargo-bulk" not in page
    assert ">Убрать<" not in page
    assert "Добавить из принятых" not in page
    assert "Новый груз" not in page
    assert "В пути" in page
    a1 = loaded_truck.cargos.get(article="A1")
    assert f'data-href="/panel/cargo/{a1.pk}/"' in page


def test_body_widget_fill_is_proportional(mgr, loaded_truck):
    body = mgr.get(f"/panel/trucks/{loaded_truck.pk}/").context["body"]

    assert body["places"] == 6 and body["capacity"] == 60
    assert len(body["cells"]) == 60
    assert sum(body["cells"]) == 6
    assert body["over"] == 0


def test_body_widget_warns_over_capacity(mgr, ivan):
    truck = services.create_truck(capacity_places=4)
    services.add_cargo(ivan, "BIG", truck=truck, places=7)

    response = mgr.get(f"/panel/trucks/{truck.pk}/")

    body = response.context["body"]
    assert body["over"] == 3
    assert all(body["cells"])
    page = response.content.decode()
    assert "7 / 4" in page
    assert "Перегруз" in page
    assert "truck-body__warn" in page


# ---------- remove from the truck ----------


def test_remove_one_row_via_htmx(mgr, loaded_truck, received):
    a1 = loaded_truck.cargos.get(article="A1")

    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": a1.pk}, **HX)

    assert response.status_code == 200
    assert state(a1) == (Cargo.Status.RECEIVED, None)
    page = response.content.decode()
    assert f'id="cargo-row-{a1.pk}" hx-swap-oob="delete"' in page
    assert 'id="truck-body" class="truck-body" hx-swap-oob="true"' in page
    assert "4 / 60" in page
    assert 'id="cargo-totals" hx-swap-oob="true"' in page
    assert 'id="truck-action" class="truck-action" hx-swap-oob="true"' in page
    assert 'id="received-count" hx-swap-oob="true">3<' in page
    assert 'id="received-list"' in page and "A1" in page  # back among the received


def test_remove_selected_rows(mgr, loaded_truck):
    a1, a2, b1 = (loaded_truck.cargos.get(article=a) for a in ("A1", "A2", "B1"))

    response = mgr.post(
        f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": ids(a1, b1)}, **HX
    )

    assert state(a1) == state(b1) == (Cargo.Status.RECEIVED, None)
    assert state(a2) == (Cargo.Status.LOADED, loaded_truck.pk)
    page = response.content.decode()
    assert "Убрано 2 груза" in page


def test_remove_last_row_shows_empty_state(mgr, truck, ivan):
    only = services.add_cargo(ivan, "ONLY", truck=truck)

    page = mgr.post(
        f"/panel/trucks/{truck.pk}/cargo/remove/", {"cargo": only.pk}, **HX
    ).content.decode()

    assert 'id="cargo-empty"' in page


def test_remove_with_foreign_cargo_changes_nothing(mgr, loaded_truck, ivan):
    other = services.create_truck()
    foreign = services.add_cargo(ivan, "X", truck=other)
    a1 = loaded_truck.cargos.get(article="A1")

    response = mgr.post(
        f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": ids(a1, foreign)}, **HX
    )

    assert response.status_code == 200
    assert state(a1) == (Cargo.Status.LOADED, loaded_truck.pk)
    assert state(foreign) == (Cargo.Status.LOADED, other.pk)
    page = response.content.decode()
    assert "toast st-error" in page
    assert 'hx-swap-oob="delete"' not in page


def test_remove_after_departure_is_refused(mgr, loaded_truck):
    a1 = loaded_truck.cargos.get(article="A1")
    services.truck_depart(loaded_truck)

    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": a1.pk}, **HX)

    assert state(a1) == (Cargo.Status.IN_TRANSIT, loaded_truck.pk)
    assert "уже не грузится" in response.content.decode()


def test_remove_with_nothing_selected(mgr, loaded_truck):
    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": "x"}, **HX)

    assert "Выберите грузы" in response.content.decode()
    assert loaded_truck.cargos.count() == 3


def test_remove_without_htmx_redirects_with_message(mgr, loaded_truck):
    a1 = loaded_truck.cargos.get(article="A1")

    response = mgr.post(
        f"/panel/trucks/{loaded_truck.pk}/cargo/remove/", {"cargo": a1.pk}, follow=True
    )

    assert response.redirect_chain[-1][0] == f"/panel/trucks/{loaded_truck.pk}/"
    assert any("Убран 1 груз" in str(m) for m in response.context["messages"])


# ---------- load from received ----------


def test_received_list_shows_only_received_without_truck(mgr, loaded_truck, received, ivan):
    picked = services.add_cargo(ivan, "GONE")
    Cargo.objects.filter(pk=picked.pk).update(status=Cargo.Status.PICKED_UP)

    response = mgr.get(f"/panel/trucks/{loaded_truck.pk}/received/", **HX)

    page = response.content.decode()
    assert "R-1" in page and "R-2" in page
    assert "GONE" not in page
    assert "A1" not in page


@pytest.mark.parametrize("query", ["r-1", "YT777", "4567"])
def test_received_search_by_article_track_phone(mgr, loaded_truck, received, query):
    page = mgr.get(
        f"/panel/trucks/{loaded_truck.pk}/received/", {"q": query}, **HX
    ).content.decode()

    assert "R-1" in page
    assert "R-2" not in page


def test_load_selected_received_cargo(mgr, loaded_truck, received):
    r1, r2 = received

    response = mgr.post(
        f"/panel/trucks/{loaded_truck.pk}/cargo/load/", {"cargo": ids(r1, r2)}, **HX
    )

    assert response.status_code == 200
    assert state(r1) == state(r2) == (Cargo.Status.LOADED, loaded_truck.pk)
    page = response.content.decode()
    assert 'hx-swap-oob="beforeend:#cargo-rows"' in page
    assert f'id="cargo-row-{r1.pk}"' in page
    assert "9 / 60" in page
    assert 'id="received-count" hx-swap-oob="true">0<' in page
    assert "Погружено 2 груза" in page


def test_load_is_all_or_nothing(mgr, loaded_truck, received):
    r1, _ = received
    a1 = loaded_truck.cargos.get(article="A1")

    response = mgr.post(
        f"/panel/trucks/{loaded_truck.pk}/cargo/load/", {"cargo": ids(r1, a1)}, **HX
    )

    assert state(r1) == (Cargo.Status.RECEIVED, None)
    assert "toast st-error" in response.content.decode()


def test_load_into_departed_truck_is_refused(mgr, loaded_truck, received):
    r1, _ = received
    services.truck_depart(loaded_truck)

    response = mgr.post(f"/panel/trucks/{loaded_truck.pk}/cargo/load/", {"cargo": r1.pk}, **HX)

    assert state(r1) == (Cargo.Status.RECEIVED, None)
    assert "уже не грузится" in response.content.decode()


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "cargo/remove/"), ("post", "cargo/load/"), ("get", "received/")],
)
def test_contents_views_are_for_managers(wh, loaded_truck, method, path):
    response = getattr(wh, method)(f"/panel/trucks/{loaded_truck.pk}/{path}")

    assert response.status_code == 403
    assert loaded_truck.cargos.count() == 3


# ---------- new cargo with its own fields ----------


def png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return SimpleUploadedFile("photo.png", buf.getvalue(), content_type="image/png")


def test_new_cargo_form_has_new_fields(mgr, truck):
    page = mgr.get(f"/panel/trucks/{truck.pk}/").content.decode()

    for name in ("track_number", "seller", "packaging", "fragile", "note", "photo"):
        assert f'name="{name}"' in page
    assert 'enctype="multipart/form-data"' in page


def test_new_cargo_with_own_fields_and_photo(mgr, truck, ivan):
    response = mgr.post(
        f"/panel/trucks/{truck.pk}/cargo/add/",
        {
            "phone": ivan.phone,
            "article": "N-1",
            "places": "1",
            "amount_due": "0",
            "track_number": "YT1",
            "seller": "Taobao",
            "packaging": "pallet",
            "fragile": "on",
            "note": "Осторожно",
            "photo": png(),
        },
        **HX,
    )

    assert response.status_code == 200
    cargo = Cargo.objects.get(article="N-1")
    assert (cargo.track_number, cargo.seller, cargo.packaging, cargo.fragile, cargo.note) == (
        "YT1",
        "Taobao",
        "pallet",
        True,
        "Осторожно",
    )
    assert cargo.photo.name.startswith("cargo/")
    page = response.content.decode()
    assert 'id="truck-body" class="truck-body" hx-swap-oob="true"' in page
    assert f'id="cargo-row-{cargo.pk}"' in page


def test_new_cargo_rejects_non_image_photo(mgr, truck, ivan):
    junk = SimpleUploadedFile("photo.png", b"not an image", content_type="image/png")

    mgr.post(
        f"/panel/trucks/{truck.pk}/cargo/add/",
        {"phone": ivan.phone, "article": "N-1", "places": "1", "photo": junk},
        **HX,
    )

    assert not Cargo.objects.exists()


def test_new_cargo_defaults(mgr, truck, ivan):
    mgr.post(
        f"/panel/trucks/{truck.pk}/cargo/add/",
        {"phone": ivan.phone, "article": "N-2", "places": "1", "amount_due": ""},
        **HX,
    )

    cargo = Cargo.objects.get(article="N-2")
    assert cargo.packaging == "box" and cargo.fragile is False
    assert cargo.amount_due == Decimal("0")


def test_standalone_new_cargo_page_takes_new_fields(mgr, ivan):
    page = mgr.get("/panel/cargo/new/").content.decode()
    assert 'enctype="multipart/form-data"' in page
    assert 'name="track_number"' in page

    mgr.post(
        "/panel/cargo/new/",
        {
            "phone": ivan.phone,
            "article": "S-1",
            "places": "1",
            "seller": "Pinduoduo",
            "packaging": "bag",
            "photo": png(),
        },
    )

    cargo = Cargo.objects.get(article="S-1")
    assert (cargo.seller, cargo.packaging, cargo.status) == ("Pinduoduo", "bag", "received")
    assert cargo.photo.name.startswith("cargo/")

import pytest
from django.contrib.auth import get_user_model
from django.test import Client as HttpClient

from logistics.models import Cargo, Truck
from panel.permissions import is_manager, is_warehouse

pytestmark = pytest.mark.django_db


def mutating(truck, client):
    """(method, url) of every view that changes data; managers only."""
    return [
        ("get", "/panel/trucks/new/"),
        ("post", "/panel/trucks/new/"),
        ("get", f"/panel/trucks/{truck.pk}/edit/"),
        ("post", f"/panel/trucks/{truck.pk}/edit/"),
        ("post", f"/panel/trucks/{truck.pk}/status/depart/"),
        ("post", f"/panel/trucks/{truck.pk}/status/arrive/"),
        ("post", f"/panel/trucks/{truck.pk}/status/close/"),
        ("post", f"/panel/trucks/{truck.pk}/cargo/add/"),
        ("get", "/panel/cargo/new/"),
        ("post", "/panel/cargo/new/"),
        ("get", f"/panel/trucks/{truck.pk}/import/"),
        ("post", f"/panel/trucks/{truck.pk}/import/"),
        ("post", f"/panel/trucks/{truck.pk}/import/apply/"),
        ("get", f"/panel/clients/{client.pk}/edit/"),
        ("post", f"/panel/clients/{client.pk}/edit/"),
    ]


def readable(truck, client):
    return [
        "/panel/trucks/",
        f"/panel/trucks/{truck.pk}/",
        "/panel/cargo/",
        "/panel/clients/",
        f"/panel/clients/{client.pk}/",
        "/panel/search/?q=abc",
        "/panel/clients/lookup/?phone=89141234567",
        "/panel/import/template.xlsx",
    ]


def test_group_helpers(manager, keeper):
    admin = get_user_model().objects.create_superuser("root", password="x")
    nobody = get_user_model().objects.create_user("nobody", password="x")

    assert is_manager(manager) and not is_warehouse(manager)
    assert is_warehouse(keeper) and not is_manager(keeper)
    assert is_manager(admin)
    assert not is_manager(nobody)


def test_warehouse_gets_403_on_every_mutating_view(wh, loaded_truck, ivan):
    for method, url in mutating(loaded_truck, ivan):
        response = getattr(wh, method)(url)
        assert response.status_code == 403, (method, url)
        assert "Недостаточно прав" in response.content.decode(), url

    loaded_truck.refresh_from_db()
    assert loaded_truck.status == Truck.Status.LOADING
    assert Cargo.objects.count() == 3


def test_anonymous_is_redirected_to_login(loaded_truck, ivan):
    http = HttpClient()
    urls = [(m, u) for m, u in mutating(loaded_truck, ivan)]
    urls += [("get", u) for u in readable(loaded_truck, ivan)]
    for method, url in urls:
        response = getattr(http, method)(url)
        assert response.status_code == 302, url
        assert response["Location"].startswith("/panel/login/?next="), url


def test_warehouse_can_view_everything(wh, loaded_truck, ivan):
    for url in readable(loaded_truck, ivan):
        assert wh.get(url).status_code in (200, 302), url


def test_warehouse_does_not_see_manager_controls(wh, loaded_truck):
    page = wh.get(f"/panel/trucks/{loaded_truck.pk}/").content.decode()

    assert "Отправить машину" not in page
    assert "Добавить груз" not in page
    assert "Импорт" not in page


def test_manager_sees_controls(mgr, loaded_truck):
    page = mgr.get(f"/panel/trucks/{loaded_truck.pk}/").content.decode()

    assert "Отправить машину" in page
    assert "Добавить груз" in page
    assert "Импорт из Excel" in page

"""Staff login (DESIGN «Вход в систему»): segment switch, group-based redirect, safe next."""

import re

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


def segment_input(html, next_url):
    pattern = r'<input[^>]*data-next="' + re.escape(next_url) + r'"[^>]*>'
    match = re.search(pattern, html)
    assert match, next_url
    return match.group(0)


def login(client, username, next_url=None):
    data = {"username": username, "password": "x"}
    if next_url is not None:
        data["next"] = next_url
    return client.post("/panel/login/", data)


@pytest.mark.usefixtures("keeper")
def test_warehouse_user_without_next_lands_on_pickup(client):
    response = login(client, "keeper")
    assert response.status_code == 302
    assert response["Location"] == "/panel/pickup/"


@pytest.mark.usefixtures("manager")
def test_manager_without_next_lands_on_ops(client):
    response = login(client, "manager")
    assert response["Location"] == "/panel/"


def test_user_in_both_groups_counts_as_manager(client, manager):
    from django.contrib.auth.models import Group

    manager.groups.add(Group.objects.get(name="Склад"))
    assert login(client, "manager")["Location"] == "/panel/"


def test_user_without_groups_lands_on_ops(client):
    get_user_model().objects.create_user("plain", password="x")
    assert login(client, "plain")["Location"] == "/panel/"


@pytest.mark.usefixtures("keeper")
def test_explicit_safe_next_wins_over_group(client):
    response = login(client, "keeper", "/panel/trucks/")
    assert response["Location"] == "/panel/trucks/"


@pytest.mark.usefixtures("manager")
def test_manager_can_choose_pickup_segment(client):
    assert login(client, "manager", "/panel/pickup/")["Location"] == "/panel/pickup/"


@pytest.mark.usefixtures("keeper")
@pytest.mark.parametrize("bad", ["https://evil.example/", "//evil.example/x"])
def test_unsafe_next_is_ignored(client, bad):
    response = login(client, "keeper", bad)
    assert response["Location"] == "/panel/pickup/"


def test_login_page_renders_segment_switch_and_back_link(client):
    html = client.get("/panel/login/").content.decode()
    assert "Вход для сотрудников" in html
    assert "Панель управления" in html
    assert "Склад" in html
    assert 'data-next="/panel/"' in html
    assert 'data-next="/panel/pickup/"' in html
    assert 'name="next" value=""' in html
    assert "← На сайт" in html
    assert 'href="/"' in html


def test_login_page_preselects_segment_from_next(client):
    html = client.get("/panel/login/?next=/panel/pickup/").content.decode()
    assert 'name="next" value="/panel/pickup/"' in html
    assert "checked" in segment_input(html, "/panel/pickup/")
    assert "checked" not in segment_input(html, "/panel/")


def test_login_page_defaults_to_panel_segment(client):
    html = client.get("/panel/login/").content.decode()
    assert "checked" in segment_input(html, "/panel/")
    assert "checked" not in segment_input(html, "/panel/pickup/")


@pytest.mark.usefixtures("keeper")
def test_wrong_password_shows_russian_error(client):
    response = client.post("/panel/login/", {"username": "keeper", "password": "nope"})
    assert response.status_code == 200
    assert "Неверный логин или пароль" in response.content.decode()

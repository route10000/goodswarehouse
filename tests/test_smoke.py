import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone


def test_time_zone_is_vladivostok():
    assert settings.TIME_ZONE == "Asia/Vladivostok"
    assert settings.USE_TZ is True
    assert settings.LANGUAGE_CODE == "ru-ru"


def test_pinned_now_stops_the_clock_for_local_dates_too(pinned_now):
    assert timezone.now() == pinned_now
    assert timezone.now() == pinned_now
    assert timezone.localdate() == timezone.localdate(pinned_now)


@pytest.mark.django_db
def test_public_home_ok(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "GoodsWarehouse" in response.content.decode()


@pytest.mark.django_db
def test_panel_redirects_anonymous_to_login(client):
    response = client.get("/panel/")
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/")


@pytest.mark.django_db
def test_panel_ok_for_logged_in_user(client):
    user = get_user_model().objects.create_user(username="staff", password="x")
    client.force_login(user)
    response = client.get("/panel/")
    assert response.status_code == 200
    assert "Ops-центр" in response.content.decode()


@pytest.mark.django_db
def test_panel_login_page_ok(client):
    response = client.get("/panel/login/")
    assert response.status_code == 200

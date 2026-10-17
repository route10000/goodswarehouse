from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client as HttpClient

from logistics import services


@pytest.fixture(autouse=True)
def _fast_passwords(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _user(username, group=None, **extra):
    user = get_user_model().objects.create_user(username, password="x", **extra)
    if group:
        user.groups.add(Group.objects.get(name=group))
    return user


@pytest.fixture
def manager(db):
    return _user("manager", "Менеджер")


@pytest.fixture
def keeper(db):
    """A «Склад» employee: may look at everything, may not change trucks or cargo."""
    return _user("keeper", "Склад")


@pytest.fixture
def mgr(manager):
    http = HttpClient()
    http.force_login(manager)
    return http


@pytest.fixture
def wh(keeper):
    http = HttpClient()
    http.force_login(keeper)
    return http


@pytest.fixture
def ivan(db):
    client, _ = services.get_or_create_client("+79141234567", "Иван")
    client.telegram_chat_id = 111
    client.save()
    return client


@pytest.fixture
def petr(db):
    client, _ = services.get_or_create_client("+79141234568", "Пётр")
    return client


@pytest.fixture
def truck(db):
    return services.create_truck(plate="А123ВС125")


@pytest.fixture
def loaded_truck(truck, ivan, petr):
    services.add_cargo(
        ivan, "A1", truck=truck, places=2, weight_kg=Decimal("10.5"), amount_due=Decimal("100")
    )
    services.add_cargo(
        ivan, "A2", truck=truck, places=1, weight_kg=Decimal("2"), amount_due=Decimal("200")
    )
    services.add_cargo(petr, "B1", truck=truck, places=3, amount_due=Decimal("50"))
    return truck

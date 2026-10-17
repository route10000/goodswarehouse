import pytest
from django.contrib import admin
from django.contrib.auth.models import Group

from logistics.models import Cargo, Client, ContactAttempt, DailyOrder, DailyOrderLine, Truck
from notify.models import Notification

pytestmark = pytest.mark.django_db


def perms(group_name):
    group = Group.objects.get(name=group_name)
    return set(group.permissions.values_list("content_type__app_label", "codename"))


def test_manager_group_has_all_domain_permissions():
    from django.contrib.auth.models import Permission

    expected = set(
        Permission.objects.filter(content_type__app_label__in=["logistics", "notify"]).values_list(
            "content_type__app_label", "codename"
        )
    )
    assert expected
    assert perms("Менеджер") == expected


def test_warehouse_group_permissions():
    got = perms("Склад")
    models = ["client", "truck", "cargo", "dailyorder", "dailyorderline", "contactattempt"]
    for model in models:
        assert ("logistics", f"view_{model}") in got
    assert ("notify", "view_notification") in got
    assert ("logistics", "change_cargo") in got
    assert ("logistics", "add_contactattempt") in got
    assert ("logistics", "change_dailyorderline") in got
    assert ("logistics", "delete_cargo") not in got
    assert ("logistics", "change_truck") not in got
    assert ("logistics", "add_client") not in got


@pytest.mark.parametrize(
    "model", [Client, Truck, Cargo, DailyOrder, DailyOrderLine, ContactAttempt, Notification]
)
def test_models_registered_in_admin(model):
    assert admin.site.is_registered(model)


@pytest.mark.parametrize(
    "model,field",
    [
        (Truck, "status"),
        (Cargo, "status"),
        (DailyOrderLine, "call_status"),
        (Notification, "status"),
    ],
)
def test_status_fields_read_only_in_admin(rf, admin_user, model, field):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert field in model_admin.get_readonly_fields(request)
    assert field in model_admin.get_readonly_fields(request, obj=None)


def test_admin_changelists_render(admin_client):
    Client.objects.create(name="Иван", phone="+79141234567")
    for model in [Client, Truck, Cargo, DailyOrder, DailyOrderLine, ContactAttempt, Notification]:
        url = f"/admin/{model._meta.app_label}/{model._meta.model_name}/"
        assert admin_client.get(url).status_code == 200, url


def test_client_admin_search_by_any_phone_format(admin_client):
    Client.objects.create(name="Иван", phone="+79141234567")
    response = admin_client.get("/admin/logistics/client/", {"q": "8 914 123 45 67"})
    assert "GW-0001" in response.content.decode()


@pytest.mark.parametrize("model", [DailyOrderLine, ContactAttempt])
def test_order_lines_and_calls_are_view_only_in_admin(rf, admin_user, model):
    """Their writes go only through ``logistics.orders`` (mark_line, add_line, ...)."""
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_view_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)

"""Sidebar navigation (DESIGN «Навигация сайдбара») and the stub sections it links to."""

import importlib.util
import re

import pytest
from django.conf import settings as django_settings
from django.test import Client as HttpClient

pytestmark = pytest.mark.django_db

DESKTOP_ORDER = ["ops", "analytics", "trucks", "cargo", "clients", "warehouse", "autopilot"]


def nav_items(page: str) -> list[str]:
    """Sections of the main sidebar list, in order."""
    return re.findall(r'data-nav="(\w+)"', page)


def more_items(page: str) -> list[str]:
    """Sections inside the mobile «Ещё» menu, in order."""
    return re.findall(r'data-nav-more="(\w+)"', page)


def nav_link(page: str, key: str) -> str:
    match = re.search(rf'<a [^>]*data-nav="{key}"[^>]*>.*?</a>', page, re.S)
    assert match, key
    return match.group(0)


# ---------- settings ----------


def load_settings(monkeypatch, **env):
    for key in ("DEBUG", "AUTOPILOT_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SECRET_KEY", "probe")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = django_settings.BASE_DIR / "config" / "settings.py"
    spec = importlib.util.spec_from_file_location("settings_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_autopilot_defaults_to_debug(monkeypatch):
    assert load_settings(monkeypatch, DEBUG="true").AUTOPILOT_ENABLED is True
    assert load_settings(monkeypatch).AUTOPILOT_ENABLED is False


def test_autopilot_env_overrides_debug(monkeypatch):
    off_in_debug = load_settings(monkeypatch, DEBUG="true", AUTOPILOT_ENABLED="false")
    assert off_in_debug.AUTOPILOT_ENABLED is False
    assert load_settings(monkeypatch, AUTOPILOT_ENABLED="true").AUTOPILOT_ENABLED is True


# ---------- sidebar ----------


def test_manager_sidebar_order_with_autopilot(mgr, settings):
    settings.AUTOPILOT_ENABLED = True
    page = mgr.get("/panel/trucks/").content.decode()
    assert nav_items(page) == DESKTOP_ORDER


def test_autopilot_hidden_when_disabled(mgr, settings):
    settings.AUTOPILOT_ENABLED = False
    page = mgr.get("/panel/trucks/").content.decode()
    assert nav_items(page) == DESKTOP_ORDER[:-1]
    assert "autopilot" not in more_items(page)
    assert "/panel/autopilot/" not in page


def test_autopilot_hidden_for_warehouse_staff(wh, settings):
    settings.AUTOPILOT_ENABLED = True
    page = wh.get("/panel/trucks/").content.decode()
    assert nav_items(page) == DESKTOP_ORDER[:-1]
    assert "/panel/autopilot/" not in page


def test_old_order_and_pickup_items_are_gone(mgr):
    page = mgr.get("/panel/trucks/").content.decode()
    assert "Ордер на день" not in page
    assert 'title="Выдача"' not in page


def test_warehouse_item_links_to_warehouse_and_carries_the_order_badge(mgr):
    page = mgr.get("/panel/trucks/").content.decode()
    link = nav_link(page, "warehouse")
    assert 'href="/panel/warehouse/"' in link
    assert 'id="order-badge"' in link
    assert page.count('id="order-badge"') == 1


@pytest.mark.parametrize("url", ["/panel/pickup/", "/panel/order/"])
def test_warehouse_item_is_active_on_pickup_and_order(mgr, url):
    page = mgr.get(url).content.decode()
    assert 'aria-current="page"' in nav_link(page, "warehouse")
    assert 'aria-current="page"' not in nav_link(page, "ops")


def test_mobile_more_menu_holds_the_rest(mgr, settings):
    settings.AUTOPILOT_ENABLED = True
    page = mgr.get("/panel/trucks/").content.decode()
    assert "Ещё" in page
    assert more_items(page) == ["analytics", "clients", "autopilot"]


def test_mobile_more_menu_without_autopilot(wh, settings):
    settings.AUTOPILOT_ENABLED = True
    page = wh.get("/panel/trucks/").content.decode()
    assert more_items(page) == ["analytics", "clients"]


def test_more_menu_is_marked_active_for_its_sections(mgr):
    page = mgr.get("/panel/clients/").content.decode()
    summary = re.search(r"<summary[^>]*>", page).group(0)
    assert "is-active" in summary
    page = mgr.get("/panel/trucks/").content.decode()
    summary = re.search(r"<summary[^>]*>", page).group(0)
    assert "is-active" not in summary


# ---------- analytics stub ----------


def test_analytics_requires_login():
    response = HttpClient().get("/panel/analytics/")
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/?next=")


@pytest.mark.parametrize("who", ["mgr", "wh"])
def test_analytics_empty_state(request, who):
    response = request.getfixturevalue(who).get("/panel/analytics/")
    assert response.status_code == 200
    assert "panel/analytics/index.html" in [t.name for t in response.templates]
    page = response.content.decode()
    assert "Аналитика появится здесь" in page
    assert 'aria-current="page"' in nav_link(page, "analytics")


# ---------- warehouse stub ----------


def test_warehouse_requires_login():
    response = HttpClient().get("/panel/warehouse/")
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/?next=")


def test_warehouse_redirects_to_pickup(wh):
    response = wh.get("/panel/warehouse/")
    assert response.status_code == 302
    assert response["Location"] == "/panel/pickup/"


@pytest.mark.parametrize("url", ["/panel/pickup/", "/panel/order/"])
def test_old_warehouse_urls_still_work(wh, url):
    assert wh.get(url).status_code == 200


# ---------- autopilot stub ----------


def test_autopilot_404_when_disabled(mgr, settings):
    settings.AUTOPILOT_ENABLED = False
    assert mgr.get("/panel/autopilot/").status_code == 404


def test_autopilot_requires_login(settings):
    settings.AUTOPILOT_ENABLED = True
    response = HttpClient().get("/panel/autopilot/")
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/?next=")


def test_autopilot_forbidden_for_warehouse(wh, settings):
    settings.AUTOPILOT_ENABLED = True
    response = wh.get("/panel/autopilot/")
    assert response.status_code == 403
    assert "Недостаточно прав" in response.content.decode()


def test_autopilot_page_for_manager(mgr, settings):
    settings.AUTOPILOT_ENABLED = True
    response = mgr.get("/panel/autopilot/")
    assert response.status_code == 200
    assert "panel/autopilot/index.html" in [t.name for t in response.templates]
    page = response.content.decode()
    assert "Автопилот" in page
    assert 'aria-current="page"' in nav_link(page, "autopilot")


# ---------- light-only theme ----------


def body_class(page: str) -> list[str]:
    return re.search(r'<body class="([^"]*)"', page).group(1).split()


def test_panel_pages_use_the_light_panel_theme(mgr):
    for url in ("/panel/", "/panel/trucks/", "/panel/analytics/"):
        classes = body_class(mgr.get(url).content.decode())
        assert "theme-panel" in classes, url
        assert "theme-dark" not in classes, url


def test_login_page_uses_the_light_panel_theme():
    classes = body_class(HttpClient().get("/panel/login/").content.decode())
    assert "theme-panel" in classes
    assert "theme-dark" not in classes

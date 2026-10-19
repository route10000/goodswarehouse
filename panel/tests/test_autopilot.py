"""«Автопилот» panel page and its POST actions (DESIGN «Автопилот»)."""

from datetime import timedelta

import pytest
from django.test import Client as HttpClient
from django.utils import timezone

from autopilot.models import AutopilotEvent, AutopilotState
from autopilot.templatetags.autopilot_tags import ap_codes

pytestmark = pytest.mark.django_db

PAGE = "/panel/autopilot/"
ACTIONS = ["start", "pause", "step", "speed", "reset", "sync", "tick"]


def url(action):
    return f"/panel/autopilot/{action}/"


@pytest.fixture(autouse=True)
def _enabled(settings):
    settings.AUTOPILOT_ENABLED = True


@pytest.fixture
def state(db):
    return AutopilotState.load()


def set_running(state, running=True):
    state.running = running
    state.save()


# ---------- access ----------


@pytest.mark.parametrize("action", ACTIONS)
def test_actions_404_when_disabled(mgr, settings, state, action):
    settings.AUTOPILOT_ENABLED = False
    assert mgr.post(url(action), {"speed": "10"}).status_code == 404


@pytest.mark.parametrize("action", ACTIONS)
def test_actions_need_login(action):
    response = HttpClient().post(url(action))
    assert response.status_code == 302
    assert response["Location"].startswith("/panel/login/")


@pytest.mark.parametrize("action", ACTIONS)
def test_actions_forbidden_for_warehouse(wh, state, action):
    assert wh.post(url(action), {"speed": "10"}).status_code == 403
    assert AutopilotState.load().running is False
    assert not AutopilotEvent.objects.exists()


@pytest.mark.parametrize("action", ACTIONS)
def test_actions_are_post_only(mgr, state, action):
    assert mgr.get(url(action)).status_code == 405


def test_unknown_action_is_404(mgr, state):
    assert mgr.post(url("explode")).status_code == 404


def test_actions_check_csrf(manager, state):
    http = HttpClient(enforce_csrf_checks=True)
    http.force_login(manager)
    assert http.post(url("start")).status_code == 403
    assert AutopilotState.load().running is False


# ---------- controls ----------


def test_start_sets_running_and_restarts_the_real_gap(mgr, state, pinned_now):
    state.last_tick_at = pinned_now - timedelta(hours=2)
    state.save()
    response = mgr.post(url("start"))
    assert response.status_code == 302
    assert response["Location"] == PAGE
    state.refresh_from_db()
    assert state.running is True
    assert state.last_tick_at == pinned_now  # the paused hours do not count as sim time


def test_pause_clears_running(mgr, state):
    set_running(state)
    assert mgr.post(url("pause")).status_code == 302
    assert AutopilotState.load().running is False


@pytest.mark.parametrize("speed", [1, 10, 60])
def test_speed_is_saved(mgr, state, speed):
    assert mgr.post(url("speed"), {"speed": str(speed)}).status_code == 302
    assert AutopilotState.load().speed == speed


@pytest.mark.parametrize("value", ["5", "", "x"])
def test_bad_speed_is_refused(mgr, state, value):
    assert mgr.post(url("speed"), {"speed": value}).status_code == 400
    assert AutopilotState.load().speed == 1


def test_step_runs_one_tick_while_paused(mgr, state):
    state.speed = 60
    state.save()
    start = state.sim_clock
    assert mgr.post(url("step")).status_code == 302
    state.refresh_from_db()
    assert state.running is False
    assert state.sim_clock - start == timedelta(minutes=5)  # one 5 s step at ×60
    assert AutopilotEvent.objects.exists()
    assert state.trucks_created == 1


def test_reset_zeroes_counters_and_resyncs_the_clock(mgr, state, pinned_now):
    AutopilotState.objects.filter(pk=state.pk).update(
        trucks_created=3,
        cargo_added=9,
        departed=2,
        arrived=1,
        picked_up=4,
        orders_built=1,
        sim_clock=pinned_now + timedelta(hours=9),
        order_checked_on=pinned_now.date() + timedelta(days=1),
    )
    assert mgr.post(url("reset")).status_code == 302
    state.refresh_from_db()
    assert [getattr(state, name) for name in AutopilotState.COUNTERS] == [0] * 6
    assert state.sim_clock == pinned_now
    assert state.last_tick_at == pinned_now
    assert state.order_checked_on is None


def test_sync_resyncs_only_the_clock(mgr, state, pinned_now):
    checked = pinned_now.date() + timedelta(days=1)
    AutopilotState.objects.filter(pk=state.pk).update(
        trucks_created=3,
        picked_up=4,
        sim_clock=pinned_now + timedelta(hours=9),
        order_checked_on=checked,
    )
    assert mgr.post(url("sync")).status_code == 302
    state.refresh_from_db()
    assert state.sim_clock == pinned_now
    assert state.last_tick_at == pinned_now
    assert (state.trucks_created, state.picked_up) == (3, 4)
    assert state.order_checked_on == checked


def test_page_shows_how_far_the_sim_runs_ahead(mgr, state, pinned_now):
    state.sim_clock = pinned_now + timedelta(hours=3, minutes=20, seconds=30)
    state.save()
    assert "опережает на 3 ч 20 мин" in mgr.get(PAGE).content.decode()


@pytest.mark.parametrize(
    ("ahead", "text"),
    [(timedelta(minutes=45), "опережает на 45 мин"), (timedelta(hours=26), "опережает на 26 ч")],
)
def test_ahead_formats(mgr, state, pinned_now, ahead, text):
    state.sim_clock = pinned_now + ahead
    state.save()
    page = mgr.get(PAGE).content.decode()
    assert text in page
    assert "26 ч 0 мин" not in page


@pytest.mark.parametrize("ahead", [timedelta(0), timedelta(minutes=10), timedelta(minutes=-30)])
def test_no_ahead_note_within_ten_minutes(mgr, state, pinned_now, ahead):
    state.sim_clock = pinned_now + ahead
    state.save()
    assert "опережает" not in mgr.get(PAGE).content.decode()


def test_tick_response_carries_the_ahead_note(mgr, state, pinned_now):
    state.running = True
    state.sim_clock = pinned_now + timedelta(hours=2)
    state.save()
    page = mgr.post(url("tick"), HTTP_HX_REQUEST="true").content.decode()
    assert "опережает на 2 ч" in page


# ---------- tick endpoint ----------


def test_tick_while_running_returns_oob_parts(mgr, state):
    set_running(state)
    response = mgr.post(url("tick"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert AutopilotEvent.objects.exists()
    page = response.content.decode()
    for part in ("ap-journal", "ap-counters", "ap-status"):
        assert f'id="{part}" hx-swap-oob="true"' in page
    assert 'Рейс <span class="mono">R-' in page


def test_tick_while_paused_stops_polling_without_ticking(mgr, state):
    response = mgr.post(url("tick"), HTTP_HX_REQUEST="true")
    assert response.status_code == 286  # htmx stops polling on 286
    assert not AutopilotEvent.objects.exists()
    page = response.content.decode()
    assert 'id="ap-status" hx-swap-oob="true"' in page
    assert "Пауза" in page
    assert 'hx-trigger="every 5s"' not in page


# ---------- page ----------


def test_page_shows_warning_and_controls(mgr, state):
    page = mgr.get(PAGE).content.decode()
    assert "Автопилот меняет реальные данные. Для демо и тестов." in page
    assert "Запустить" in page
    assert "Один шаг" in page
    assert ">Сбросить<" in page
    assert "Сбросить счётчики" not in page
    assert "Синхронизировать время" in page
    assert 'action="/panel/autopilot/sync/"' in page
    for label in ("×1", "×10", "×60"):
        assert label in page
    assert "Пауза" in page
    assert "Время симуляции" in page
    assert 'action="/panel/autopilot/start/"' in page
    assert "csrfmiddlewaretoken" in page


def test_page_running_shows_pause_and_polls(mgr, state):
    set_running(state)
    page = mgr.get(PAGE).content.decode()
    assert "Работает" in page
    assert 'action="/panel/autopilot/pause/"' in page
    assert 'hx-post="/panel/autopilot/tick/"' in page
    assert 'hx-trigger="every 5s"' in page


def test_page_paused_does_not_poll(mgr, state):
    page = mgr.get(PAGE).content.decode()
    assert 'hx-trigger="every 5s"' not in page
    assert 'hx-post="/panel/autopilot/tick/"' not in page


def test_current_speed_is_marked(mgr, state):
    state.speed = 10
    state.save()
    page = mgr.get(PAGE).content.decode()
    assert 'value="10" aria-pressed="true"' in page
    assert 'value="60" aria-pressed="false"' in page


def test_counters_are_shown(mgr, state):
    AutopilotState.objects.filter(pk=state.pk).update(trucks_created=7, picked_up=12)
    page = mgr.get(PAGE).content.decode()
    assert "Рейсов создано" in page
    assert "Выдано" in page
    assert ">7<" in page and ">12<" in page


def test_journal_shows_latest_50_newest_first(mgr, state):
    now = timezone.now()
    AutopilotEvent.objects.bulk_create(
        AutopilotEvent(sim_at=now, kind="skipped", message=f"Событие E{i:03d}") for i in range(60)
    )
    page = mgr.get(PAGE).content.decode()
    assert "E059" in page and "E010" in page
    assert "E009" not in page
    assert page.index("E059") < page.index("E010")


def test_journal_empty_state(mgr, state):
    assert "Журнал пуст" in mgr.get(PAGE).content.decode()


def test_codes_in_messages_are_mono_and_escaped():
    html = ap_codes("Рейс R-2026-012 отправлен <b> · CN2645-1234 · GW-0042")
    assert '<span class="mono">R-2026-012</span>' in html
    assert '<span class="mono">CN2645-1234</span>' in html
    assert '<span class="mono">GW-0042</span>' in html
    assert "<b>" not in html and "&lt;b&gt;" in html

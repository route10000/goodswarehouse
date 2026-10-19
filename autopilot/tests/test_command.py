"""``manage.py autopilot``: --steps, --speed, --loop respects the running flag."""

import io
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from autopilot.management.commands import autopilot as command
from autopilot.models import AutopilotEvent, AutopilotState
from logistics.models import Truck

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _enabled(settings):
    settings.AUTOPILOT_ENABLED = True


def run(*args):
    out = StringIO()
    call_command("autopilot", *args, stdout=out)
    return out.getvalue()


def test_refuses_when_disabled(settings):
    settings.AUTOPILOT_ENABLED = False
    with pytest.raises(CommandError, match="выключен"):
        run("--steps", "1")
    assert not AutopilotEvent.objects.exists()


def test_steps_run_that_many_ticks(monkeypatch):
    calls = []
    real_tick = command.engine.tick

    def counting_tick(*args, **kwargs):
        calls.append(kwargs.get("elapsed"))
        return real_tick(*args, **kwargs)

    monkeypatch.setattr(command.engine, "tick", counting_tick)
    out = run("--steps", "3", "--interval", "5")
    assert len(calls) == 3
    assert all(c.total_seconds() == 5 for c in calls)  # each step counts as one interval
    assert Truck.objects.exists()
    assert "Шагов: 3" in out


def test_steps_advance_the_sim_clock_even_without_waiting():
    state = AutopilotState.load()
    start = state.sim_clock
    run("--steps", "2", "--speed", "60", "--interval", "5")
    state.refresh_from_db()
    assert (state.sim_clock - start).total_seconds() >= 2 * 5 * 60


def test_speed_is_saved():
    run("--steps", "1", "--speed", "10")
    assert AutopilotState.load().speed == 10


def test_speed_must_be_allowed():
    with pytest.raises(CommandError):
        run("--steps", "1", "--speed", "5")


def test_loop_ticks_only_while_running(monkeypatch):
    state = AutopilotState.load()
    state.running = False
    state.save()
    ticks, sleeps = [], []
    monkeypatch.setattr(command.engine, "tick", lambda *a, **k: ticks.append(1) or [])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:  # after two paused rounds, someone presses «Запустить»
            AutopilotState.objects.filter(pk=state.pk).update(running=True)
        if len(sleeps) == 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(command.time, "sleep", fake_sleep)
    out = run("--loop", "--interval", "2")
    assert sleeps == [2, 2, 2, 2]
    assert len(ticks) == 2  # rounds 3 and 4 only
    assert "пауз" in out.lower()


def test_output_survives_a_console_without_arrows():
    """A Windows console in cp1251 has no «→»: the command must not crash on it."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1251")
    call_command("autopilot", "--steps", "1", stdout=out)
    out.flush()
    text = raw.getvalue().decode("cp1251")
    assert "Шагов: 1" in text
    assert "Принят груз" in text  # lines with an arrow keep their Cyrillic


def test_skipped_tick_is_reported_quietly(monkeypatch):
    monkeypatch.setattr(command.engine, "tick", lambda *a, **k: command.engine.SKIPPED)
    out = run("--steps", "2")
    assert "Шагов: 2" in out
    assert "пропущен" in out

"""«Автопилот» (SPEC §14.8): demo simulator, managers only, 404 while switched off.

The page shows the controls, the session counters and the journal. Controls are plain
POST forms that redirect back. While running, the page polls ``tick/`` every 5 s: each
poll runs one tick and swaps status, counters and journal out of band; a poll that finds
the simulator paused answers 286, which makes htmx stop polling.
"""

import random
from datetime import timedelta

from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from autopilot import engine
from autopilot.models import AutopilotEvent, AutopilotState
from panel.permissions import autopilot_enabled, manager_required

JOURNAL_SIZE = 50
STOP_POLLING = 286  # htmx: cancel the polling trigger
AHEAD_NOTE_AFTER = timedelta(minutes=10)  # show «опережает на …» beyond this gap

COUNTER_LABELS = (
    ("trucks_created", "Рейсов создано"),
    ("cargo_added", "Грузов добавлено"),
    ("departed", "Отправлено"),
    ("arrived", "Прибыло"),
    ("picked_up", "Выдано"),
    ("orders_built", "Ордеров"),
)


def _ahead(state: AutopilotState, now) -> str:
    """«опережает на 3 ч 20 мин» when the sim clock runs over 10 minutes ahead of real time."""
    if state.sim_clock is None or state.sim_clock - now <= AHEAD_NOTE_AFTER:
        return ""
    hours, minutes = divmod(int((state.sim_clock - now).total_seconds() // 60), 60)
    parts = ([f"{hours} ч"] if hours else []) + ([f"{minutes} мин"] if minutes else [])
    return "опережает на " + " ".join(parts)


def _context(state: AutopilotState, **extra) -> dict:
    return {
        "nav_active": "autopilot",
        "state": state,
        "ahead": _ahead(state, timezone.now()),
        "speeds": AutopilotState.SPEEDS,
        "counters": [(label, getattr(state, name)) for name, label in COUNTER_LABELS],
        "events": AutopilotEvent.objects.all()[:JOURNAL_SIZE],
        **extra,
    }


@manager_required
def _page(request):
    return render(request, "panel/autopilot/index.html", _context(AutopilotState.load()))


def autopilot(request):
    if not autopilot_enabled():
        raise Http404("Автопилот выключен")
    return _page(request)


# ---------- actions ----------


def _start(request, state):
    state.running = True
    state.last_tick_at = timezone.now()  # the paused time does not turn into sim time
    state.save(update_fields=["running", "last_tick_at"])


def _pause(request, state):
    state.running = False
    state.save(update_fields=["running"])


def _step(request, state):
    engine.tick(state, timezone.now(), random.Random(), elapsed=engine.DEFAULT_STEP)


def _speed(request, state):
    try:
        speed = int(request.POST.get("speed", ""))
    except ValueError:
        speed = None
    if speed not in AutopilotState.SPEEDS:
        return HttpResponseBadRequest("Скорость: 1, 10 или 60")
    state.speed = speed
    state.save(update_fields=["speed"])
    return None


def _reset(request, state):
    """Start the session over: zero counters, sim clock back to real time, order rule re-armed."""
    for name in AutopilotState.COUNTERS:
        setattr(state, name, 0)
    state.order_checked_on = None
    state.save(update_fields=[*AutopilotState.COUNTERS, "order_checked_on"])
    engine.resync_clock(state, timezone.now())


def _sync(request, state):
    engine.resync_clock(state, timezone.now())


def _tick(request, state):
    status = 200
    if state.running:
        engine.tick(state, timezone.now(), random.Random())
    else:
        status = STOP_POLLING
    html = render_to_string("panel/autopilot/_tick.html", _context(state), request=request)
    return HttpResponse(html, status=status)


ACTIONS = {
    "start": _start,
    "pause": _pause,
    "step": _step,
    "speed": _speed,
    "reset": _reset,
    "sync": _sync,
    "tick": _tick,
}


@manager_required
@require_POST
def _action(request, action):
    handler = ACTIONS.get(action)
    if handler is None:
        raise Http404("Нет такого действия")
    response = handler(request, AutopilotState.load())
    return response or redirect("panel:autopilot")


def autopilot_action(request, action):
    if not autopilot_enabled():
        raise Http404("Автопилот выключен")
    return _action(request, action)

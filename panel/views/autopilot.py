"""«Автопилот» (SPEC §14.8): demo simulator, managers only, 404 while switched off.

Stub: the controls and the log arrive in a later task.
"""

from django.http import Http404
from django.shortcuts import render

from panel.permissions import autopilot_enabled, manager_required


@manager_required
def _page(request):
    return render(request, "panel/autopilot/index.html", {"nav_active": "autopilot"})


def autopilot(request):
    if not autopilot_enabled():
        raise Http404("Автопилот выключен")
    return _page(request)

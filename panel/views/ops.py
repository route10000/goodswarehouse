"""Ops-центр (`/panel/`): page, HTMX partials and the scene JSON for the 3D view (T9).

All views are read-only and open to both staff groups. Aggregations live in
``panel.ops_data``; the JSON contract is documented in ``docs/SCENE_API.md``.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from panel import ops_data


def _selected_id(request) -> int | None:
    try:
        return int(request.GET.get("truck", ""))
    except ValueError:
        return None


def _panels_context(now, items, selected) -> dict:
    return {
        "now": now,
        "kpis": ops_data.kpis(now),
        "pipeline": ops_data.pipeline(now),
        "fleet": items,
        "timeline": ops_data.day_timeline(now, items),
        "selected": selected,
        "steps": ops_data.journey(selected, now) if selected else [],
    }


@require_GET
@login_required
def ops_center(request):
    now = timezone.now()
    items = ops_data.fleet(now)
    selected = ops_data.default_truck(items)
    context = _panels_context(now, items, selected)
    context["nav_active"] = "ops"
    context["scene"] = ops_data.scene(now, items)
    return render(request, "panel/ops.html", context)


@require_GET
@login_required
def ops_live(request):
    """Every-30s refresh: KPI row (main swap) plus pipeline, fleet, timeline out of band.

    With ``?truck=<id>`` the drawer and tracker of that truck are refreshed too.
    """
    now = timezone.now()
    items = ops_data.fleet(now)
    pk = _selected_id(request)
    selected = next((i for i in items if i.id == pk), None)
    if selected is None and pk is not None:
        selected = ops_data.fleet_item(pk, now)
    return render(request, "panel/ops/_live.html", _panels_context(now, items, selected))


@require_GET
@login_required
def ops_truck(request, pk):
    """Drawer for one truck (main swap) plus its tracker out of band."""
    now = timezone.now()
    item = ops_data.fleet_item(pk, now)
    if item is None:
        raise Http404
    context = {"now": now, "selected": item, "steps": ops_data.journey(item, now)}
    return render(request, "panel/ops/_truck.html", context)


@require_GET
@login_required
def scene_api(request):
    payload = ops_data.scene(timezone.now())
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})

"""«Склад» (SPEC §14.6): tabs «Выдача · Ордер на звонок · На складе» on one page.

``?tab=pickup|order|stock`` picks the tab; the last one is kept in the session and
pickup is the default. The pickup and order tabs reuse their views' context builders;
their HTMX endpoints and actions stay under ``/panel/pickup/`` and ``/panel/order/``.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import render
from django.utils import timezone

from logistics.models import Cargo
from panel.ops_data import BLOCKS, block_key

from .common import count_phrase, warehouse_url
from .order import order_context, parse_tab_day
from .pickup import pickup_context

TABS = (("pickup", "Выдача"), ("order", "Ордер на звонок"), ("stock", "На складе"))
TAB_KEYS = {key for key, _label in TABS}
DEFAULT_TAB = "pickup"
SESSION_KEY = "warehouse_tab"
# Same age blocks as the Ops-центр scene, spelled out for the column headers.
BLOCK_CAPTIONS = {"A": "до 2 дней", "B": "3–4 дня", "C": "5–6 дней", "D": "7+ дней"}


def current_tab(request) -> str:
    tab = request.GET.get("tab", "")
    if tab in TAB_KEYS:
        request.session[SESSION_KEY] = tab
        return tab
    remembered = request.session.get(SESSION_KEY, "")
    return remembered if remembered in TAB_KEYS else DEFAULT_TAB


def stock_blocks() -> list[dict]:
    """Ready cargo in blocks A–D by local days on the warehouse, oldest first."""
    today = timezone.localdate()
    cargos = (
        Cargo.objects.filter(status=Cargo.Status.READY)
        .select_related("client")
        .order_by(F("ready_at").asc(nulls_last=True), "id")
    )
    items = {key: [] for key, _caption, _max in BLOCKS}
    for cargo in cargos:
        days = (today - timezone.localdate(cargo.ready_at)).days if cargo.ready_at else 0
        cargo.days = max(0, days)
        cargo.overdue = bool(cargo.free_storage_until and cargo.free_storage_until < today)
        items[block_key(cargo.days)].append(cargo)
    result = []
    for key, _caption, _max in BLOCKS:
        places = sum(cargo.places for cargo in items[key])
        result.append(
            {
                "key": key,
                "label": f"Блок {key}",
                "caption": BLOCK_CAPTIONS[key],
                "places_text": count_phrase(places, "место", "места", "мест"),
                "cargo_text": count_phrase(len(items[key]), "груз", "груза", "грузов"),
                "items": items[key],
            }
        )
    return result


@login_required
def warehouse(request):
    tab = current_tab(request)
    context = {
        "nav_active": "warehouse",
        "tab": tab,
        "tabs": [
            {"key": key, "label": label, "url": warehouse_url(key), "current": key == tab}
            for key, label in TABS
        ],
        "tab_label": dict(TABS)[tab],
    }
    if tab == "pickup":
        context.update(pickup_context(request))
    elif tab == "order":
        context.update(order_context(request, parse_tab_day(request)))
    else:
        blocks = stock_blocks()
        context["blocks"] = blocks
        context["stock_empty"] = not any(b["items"] for b in blocks)
    return render(request, "panel/warehouse/page.html", context)

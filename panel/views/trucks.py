"""Trucks: list, create/edit, card with status actions and the truck contents (HTMX):
the «Кузов» widget, removing cargo, loading received cargo, adding new cargo."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from logistics import services
from logistics.models import Cargo, Truck
from logistics.services import InvalidTransition
from panel.forms import CargoForm, TruckForm
from panel.permissions import is_manager, manager_required
from public.templatetags.gw_format import ru_date, ru_datetime

from .common import any_of, count_phrase, paginate, phone_q, plural
from .lookup import lookup_context

STEPS = [
    (Truck.Status.LOADING, "Грузится"),
    (Truck.Status.IN_TRANSIT, "В пути"),
    (Truck.Status.ARRIVED, "Прибыла"),
    (Truck.Status.CLOSED, "Закрыта"),
]
# action key → (status it starts from, button label, service name, success toast)
ACTIONS = {
    "depart": (
        Truck.Status.LOADING,
        "Отправить машину",
        "truck_depart",
        "Машина {number} отправлена. Сообщения клиентам поставлены в очередь.",
    ),
    "arrive": (
        Truck.Status.IN_TRANSIT,
        "Машина прибыла",
        "truck_arrive",
        "Машина {number} прибыла. Грузы готовы к выдаче.",
    ),
    "close": (
        Truck.Status.ARRIVED,
        "Закрыть рейс",
        "truck_close",
        "Рейс {number} закрыт.",
    ),
}
NOT_LOADING = "Машина {number} уже не грузится. Груз можно добавить только в машину на погрузке"
NOTHING_SELECTED = "Выберите грузы"
# The «Кузов» widget always draws this many boxes; each stands for capacity / 60 places.
BODY_CELLS = 60
RECEIVED_LIMIT = 50


def _active_cargos(truck):
    return truck.cargos.exclude(status=Cargo.Status.PICKED_UP)


def truck_counts(truck) -> dict:
    """Who hears about the next step: clients with Telegram and cargo going to the call list."""
    active = _active_cargos(truck)
    return {
        "telegram_clients": active.filter(client__telegram_chat_id__isnull=False)
        .values("client")
        .distinct()
        .count(),
        "call_cargo": active.filter(client__telegram_chat_id__isnull=True).count(),
        "ready_cargo": truck.cargos.filter(status=Cargo.Status.READY).count(),
    }


def _consequence(key: str, counts: dict) -> str:
    if key == "close":
        ready = counts["ready_cargo"]
        if not ready:
            return "Рейс закроется. Все грузы уже выданы."
        left = plural(ready, "остаётся", "остаются", "остаются")
        return (
            f"Рейс закроется вручную. {count_phrase(ready, 'груз', 'груза', 'грузов')} "
            f"{left} на складе и ждут выдачи."
        )
    n, m = counts["telegram_clients"], counts["call_cargo"]
    clients = f"{count_phrase(n, 'клиент', 'клиента', 'клиентов')} " + plural(
        n, "получит", "получат", "получат"
    )
    cargos = f"{count_phrase(m, 'груз', 'груза', 'грузов')} " + plural(
        m, "попадёт", "попадут", "попадут"
    )
    return f"{clients} сообщение в Telegram, {cargos} в ордер на звонок"


def next_action(truck) -> dict | None:
    for key, (status, label, _service, _toast) in ACTIONS.items():
        if truck.status == status:
            counts = truck_counts(truck)
            return {
                "key": key,
                "label": label,
                "url": reverse("panel:truck_status", args=[truck.pk, key]),
                "consequence": _consequence(key, counts),
                **counts,
            }
    return None


def steps(truck) -> list[dict]:
    current = [status for status, _ in STEPS].index(truck.status)
    captions = {
        Truck.Status.LOADING: ru_datetime(truck.loading_starts) or "",
        Truck.Status.IN_TRANSIT: ru_datetime(truck.departed_at)
        or (f"план {ru_date(truck.departure_date)}" if truck.departure_date else ""),
        Truck.Status.ARRIVED: ru_datetime(truck.arrived_at)
        or (f"ETA {ru_datetime(truck.eta)}" if truck.eta else ""),
        Truck.Status.CLOSED: "",
    }
    result = []
    for index, (status, label) in enumerate(STEPS):
        state = "done" if index < current else "current" if index == current else "future"
        result.append({"label": label, "state": state, "caption": captions[status]})
    return result


def cargo_totals(truck) -> dict:
    sums = truck.cargos.aggregate(
        places=Coalesce(Sum("places"), 0),
        weight=Sum("weight_kg"),
        amount=Sum("amount_due"),
    )
    cent = Decimal("0.01")
    return {
        "places": sums["places"],
        "weight": (sums["weight"] or Decimal("0")).quantize(cent),
        "amount": (sums["amount"] or Decimal("0")).quantize(cent),
        "count": truck.cargos.count(),
    }


def truck_body(truck, totals) -> dict:
    """«Кузов»: places against capacity, weight, clients and the boxes to fill."""
    capacity = truck.capacity_places
    places = totals["places"]
    filled = 0
    if places:
        filled = min(BODY_CELLS, max(1, round(places * BODY_CELLS / capacity)))
    return {
        "places": places,
        "capacity": capacity,
        "weight": totals["weight"],
        "clients": truck.cargos.values("client").distinct().count(),
        "cells": [index < filled for index in range(BODY_CELLS)],
        "over": max(0, places - capacity),
    }


def received_context(text: str = "") -> dict:
    """Received cargo that waits for a truck, optionally filtered by article/track/phone."""
    waiting = Cargo.objects.filter(status=Cargo.Status.RECEIVED, truck__isnull=True)
    found = waiting.select_related("client").order_by("created_at", "id")
    text = (text or "").strip()
    if text:
        found = found.filter(
            any_of(
                Q(article__icontains=text),
                Q(track_number__icontains=text),
                Q(client__code__iexact=text),
                phone_q(text, "client__phone"),
            )
        )
    total = found.count()
    return {
        "received": list(found[:RECEIVED_LIMIT]),
        "received_found": total,
        "received_more": max(0, total - RECEIVED_LIMIT),
        "received_q": text,
        "received_all": waiting.count(),
    }


# ---------- list ----------


@login_required
def truck_list(request):
    status = request.GET.get("status", "")
    if status not in Truck.Status.values:
        status = ""
    trucks = Truck.objects.annotate(
        places_total=Coalesce(Sum("cargos__places"), 0),
        clients_total=Count("cargos__client", distinct=True),
    ).order_by("-created_at", "-id")
    if status:
        trucks = trucks.filter(status=status)
    by_status = dict(Truck.objects.values_list("status").annotate(n=Count("id")))
    filters = [{"value": "", "label": "Все", "count": sum(by_status.values())}]
    filters += [
        {"value": value, "label": label, "count": by_status.get(value, 0)}
        for value, label in Truck.Status.choices
    ]
    page = paginate(request, trucks)
    return render(
        request,
        "panel/trucks/list.html",
        {
            "nav_active": "trucks",
            "page": page,
            "trucks": page.object_list,
            "status": status,
            "filters": filters,
        },
    )


# ---------- create / edit ----------


@manager_required
def truck_create(request):
    form = TruckForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        truck = services.create_truck(**form.cleaned_data)
        messages.success(request, f"Машина {truck.number} создана.")
        return redirect("panel:truck", pk=truck.pk)
    return render(
        request, "panel/trucks/form.html", {"nav_active": "trucks", "form": form, "truck": None}
    )


@manager_required
def truck_edit(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    form = TruckForm(request.POST or None, instance=truck)
    if request.method == "POST" and form.is_valid():
        edited = form.save(commit=False)
        # Only the form's own fields: a status changed meanwhile must not be overwritten.
        edited.save(update_fields=TruckForm.Meta.fields)
        messages.success(request, f"Машина {edited.number} сохранена.")
        return redirect("panel:truck", pk=pk)
    return render(
        request,
        "panel/trucks/form.html",
        {"nav_active": "trucks", "form": form, "truck": Truck.objects.get(pk=pk)},
    )


# ---------- card ----------


def _card_context(request, truck, form=None):
    cargos = truck.cargos.select_related("client").order_by("created_at", "id")
    can_add = truck.status == Truck.Status.LOADING
    manage = can_add and is_manager(request.user)
    if form is None and can_add:
        form = CargoForm()
    totals = cargo_totals(truck)
    context = {
        "nav_active": "trucks",
        "truck": truck,
        "steps": steps(truck),
        "action": next_action(truck),
        "cargos": cargos,
        "totals": totals,
        "body": truck_body(truck, totals),
        "can_add": can_add,
        "manage": manage,
        "form": form,
        "lookup": lookup_context(""),
    }
    if manage:
        context.update(received_context())
    return context


@login_required
def truck_detail(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    return render(request, "panel/trucks/detail.html", _card_context(request, truck))


@require_POST
@manager_required
def truck_status(request, pk, action):
    if action not in ACTIONS:
        raise Http404
    truck = get_object_or_404(Truck, pk=pk)
    _status, _label, service_name, toast = ACTIONS[action]
    try:
        getattr(services, service_name)(truck, user=request.user)
    except InvalidTransition as exc:
        messages.error(request, f"{exc}. Статус не изменён.")
    else:
        messages.success(request, toast.format(number=truck.number))
    return redirect("panel:truck", pk=truck.pk)


# ---------- add cargo ----------


def create_cargo(form: CargoForm, truck: Truck | None) -> Cargo:
    """Find or create the client and register the cargo, all in one transaction."""
    data = form.cleaned_data
    with transaction.atomic():
        client, _ = services.get_or_create_client(data["phone"], data.get("name", ""))
        return services.add_cargo(
            client,
            data["article"],
            description=data.get("description", ""),
            places=data["places"],
            weight_kg=data.get("weight_kg"),
            volume_m3=data.get("volume_m3"),
            amount_due=data["amount_due"],
            track_number=data.get("track_number", ""),
            seller=data.get("seller", ""),
            packaging=data.get("packaging") or Cargo.Packaging.BOX,
            fragile=data.get("fragile", False),
            note=data.get("note", ""),
            photo=data.get("photo"),
            truck=truck,
        )


@require_POST
@manager_required
def truck_cargo_add(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    form = CargoForm(request.POST, request.FILES)
    added = None
    if truck.status != Truck.Status.LOADING:
        form.is_valid()
        form.add_error(None, NOT_LOADING.format(number=truck.number))
    elif form.is_valid():
        try:
            added = create_cargo(form, truck)
        except InvalidTransition as exc:
            form.add_error(None, f"{exc}. Груз не добавлен")

    if not request.headers.get("HX-Request"):
        if added:
            messages.success(request, f"Груз {added.article} добавлен.")
        else:
            errors = [e for field in form.errors.values() for e in field]
            messages.error(request, " ".join(errors) or "Груз не добавлен.")
        return redirect("panel:truck", pk=truck.pk)

    if added:
        added = Cargo.objects.select_related("client").get(pk=added.pk)
        form = CargoForm()
    context = {
        **_contents_context(request, truck),
        "form": form,
        "added": added,
        "lookup": lookup_context(
            "" if added else request.POST.get("phone", ""),
            "" if added else request.POST.get("name", ""),
        ),
        "cargo": added,
    }
    return render(request, "panel/trucks/_cargo_add.html", context)


# ---------- truck contents: remove, load from received ----------


def _contents_context(request, truck) -> dict:
    """Everything the out-of-band refresh of the card needs after the contents change."""
    totals = cargo_totals(truck)
    return {
        "truck": truck,
        "manage": True,
        "totals": totals,
        "body": truck_body(truck, totals),
        "action": next_action(truck),
        **received_context(request.POST.get("q", "")),
    }


def _selected_ids(request) -> list[str]:
    return [value for value in request.POST.getlist("cargo") if value.isdigit()]


def _contents_response(request, truck, *, toast, error, removed=(), added=()):
    if not request.headers.get("HX-Request"):
        (messages.error if error else messages.success)(request, toast)
        return redirect("panel:truck", pk=truck.pk)
    context = {"toast": toast, "error": error, "changed": not error}
    if not error:
        context.update(_contents_context(request, truck))
        context["removed"] = list(removed)
        context["added_rows"] = list(
            Cargo.objects.filter(pk__in=list(added))
            .select_related("client")
            .order_by("created_at", "id")
        )
    return render(request, "panel/trucks/_contents_response.html", context)


@require_POST
@manager_required
def truck_cargo_remove(request, pk):
    """Take the selected (or one) cargo out of a loading truck: all or nothing."""
    truck = get_object_or_404(Truck, pk=pk)
    selected = _selected_ids(request)
    if not selected:
        return _contents_response(request, truck, toast=NOTHING_SELECTED, error=True)
    try:
        count = services.unassign_many(selected, truck)
    except InvalidTransition as exc:
        return _contents_response(request, truck, toast=f"{exc}. Ничего не убрано", error=True)
    verb = plural(count, "Убран", "Убрано", "Убрано")
    toast = f"{verb} {count_phrase(count, 'груз', 'груза', 'грузов')}. Они снова в принятых"
    return _contents_response(request, truck, toast=toast, error=False, removed=selected)


@require_POST
@manager_required
def truck_cargo_load(request, pk):
    """Load the selected received cargo into the truck: all or nothing."""
    truck = get_object_or_404(Truck, pk=pk)
    selected = _selected_ids(request)
    if not selected:
        return _contents_response(request, truck, toast=NOTHING_SELECTED, error=True)
    try:
        count = services.assign_many(selected, truck)
    except InvalidTransition as exc:
        return _contents_response(request, truck, toast=f"{exc}. Ничего не погружено", error=True)
    verb = plural(count, "Погружен", "Погружено", "Погружено")
    toast = f"{verb} {count_phrase(count, 'груз', 'груза', 'грузов')} в {truck.number}"
    return _contents_response(request, truck, toast=toast, error=False, added=selected)


@manager_required
def truck_received(request, pk):
    """HTMX: the list of received cargo for «Добавить из принятых», with search."""
    truck = get_object_or_404(Truck, pk=pk)
    context = {"truck": truck, **received_context(request.GET.get("q", ""))}
    return render(request, "panel/trucks/_received_list.html", context)

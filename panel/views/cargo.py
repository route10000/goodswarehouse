"""Cargo: list with filters and the stand-alone add form (with or without a truck)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render

from logistics.models import Cargo, Truck
from logistics.services import InvalidTransition
from panel.forms import CargoForm
from panel.permissions import manager_required

from .common import any_of, paginate, phone_q
from .lookup import lookup_context
from .trucks import create_cargo


def cargo_search_q(text: str) -> Q:
    return any_of(
        Q(article__icontains=text),
        Q(client__code__iexact=text),
        phone_q(text, "client__phone"),
    )


@login_required
def cargo_list(request):
    status = request.GET.get("status", "")
    if status not in Cargo.Status.values:
        status = ""
    truck_id = request.GET.get("truck", "")
    truck_id = int(truck_id) if truck_id.isdigit() else None
    text = request.GET.get("q", "").strip()

    cargos = Cargo.objects.select_related("client", "truck").order_by("-created_at", "-id")
    if status:
        cargos = cargos.filter(status=status)
    if truck_id:
        cargos = cargos.filter(truck_id=truck_id)
    if text:
        cargos = cargos.filter(cargo_search_q(text))

    by_status = dict(Cargo.objects.values_list("status").annotate(n=Count("id")))
    filters = [{"value": "", "label": "Все", "count": sum(by_status.values())}]
    filters += [
        {"value": value, "label": label, "count": by_status.get(value, 0)}
        for value, label in Cargo.Status.choices
    ]
    return render(
        request,
        "panel/cargo/list.html",
        {
            "nav_active": "cargo",
            "page": paginate(request, cargos),
            "status": status,
            "truck_id": truck_id,
            "q": text,
            "filters": filters,
            "trucks": Truck.objects.order_by("-created_at", "-id"),
        },
    )


@manager_required
def cargo_create(request):
    initial = {}
    if request.GET.get("truck", "").isdigit():
        initial["truck"] = int(request.GET["truck"])
    form = CargoForm(request.POST or None, with_truck=True, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            cargo = create_cargo(form, form.cleaned_data.get("truck"))
        except InvalidTransition as exc:
            form.add_error("truck", f"{exc}. Выберите машину на погрузке")
        else:
            messages.success(request, f"Груз {cargo.article} добавлен.")
            return redirect("panel:cargo")
    return render(
        request,
        "panel/cargo/new.html",
        {
            "nav_active": "cargo",
            "form": form,
            "lookup": lookup_context(request.POST.get("phone", ""), request.POST.get("name", "")),
        },
    )

"""Manifest import into a loading truck: upload → preview → confirm (rows kept in session)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from logistics.imports import ImportPreview, apply_manifest, build_template, parse_manifest
from logistics.models import Truck
from logistics.services import InvalidTransition
from panel.forms import ManifestUploadForm
from panel.permissions import manager_required

from .common import count_phrase

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def session_key(truck) -> str:
    return f"manifest:{truck.pk}"


def summary(preview: ImportPreview) -> str:
    rows = count_phrase(len(preview.rows), "строка", "строки", "строк")
    new = count_phrase(preview.new_client_count, "новый клиент", "новых клиента", "новых клиентов")
    return f"{rows}, {preview.error_count} с ошибками, {new}"


def _loading_truck_or_redirect(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    if truck.status != Truck.Status.LOADING:
        messages.error(request, f"Машина {truck.number} уже не грузится. Импорт недоступен.")
        return truck, redirect("panel:truck", pk=truck.pk)
    return truck, None


@manager_required
def truck_import(request, pk):
    truck, refusal = _loading_truck_or_redirect(request, pk)
    if refusal:
        return refusal
    form = ManifestUploadForm(request.POST or None, request.FILES or None)
    preview = None
    if request.method == "POST":
        request.session.pop(session_key(truck), None)
        if form.is_valid():
            preview = parse_manifest(form.cleaned_data["file"])
            if not preview.has_errors:
                request.session[session_key(truck)] = preview.to_dict()
    return render(
        request,
        "panel/trucks/import.html",
        {
            "nav_active": "trucks",
            "truck": truck,
            "form": form,
            "preview": preview,
            "summary": summary(preview) if preview and preview.rows else "",
        },
    )


@require_POST
@manager_required
def truck_import_apply(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    data = request.session.pop(session_key(truck), None)
    if not data:
        messages.error(request, "Нет проверенного файла. Загрузите манифест ещё раз.")
        return redirect("panel:truck", pk=truck.pk)
    try:
        count = apply_manifest(ImportPreview.from_dict(data), truck)
    except (InvalidTransition, ValueError, ValidationError) as exc:
        text = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, f"Импорт отменён, ничего не добавлено: {text}")
    else:
        messages.success(
            request,
            f"Импортировано {count_phrase(count, 'груз', 'груза', 'грузов')} "
            f"в машину {truck.number}.",
        )
    return redirect("panel:truck", pk=truck.pk)


@login_required
def import_template(request):
    response = HttpResponse(build_template(), content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="manifest-template.xlsx"'
    return response

"""Manifest import and manual entry into a loading truck (SPEC §10, §14.5).

An uploaded ``.xlsx`` or «Ввести вручную» opens the same editable grid. The grid posts
its cells as one list per column; «Проверить», «+ строка» and × re-render it without
saving, «Сохранить» validates again and saves every row in one transaction. Validation
lives in ``logistics.imports``. A clean preview is also kept in the session, so the
older «apply» endpoint can confirm it without the file.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from logistics.imports import (
    CELL_KEYS,
    ImportPreview,
    apply_manifest,
    build_template,
    parse_manifest,
    parse_rows,
)
from logistics.models import Cargo, Truck
from logistics.services import InvalidTransition
from panel.forms import ManifestUploadForm
from panel.permissions import manager_required

from .common import count_phrase, plural

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MANUAL_ROWS = 5
FRAGILE_OPTIONS = [("", "нет"), ("да", "да")]
# (cell key, header, kind, css class)
GRID_COLUMNS = [
    ("article", "Артикул", "text", "g-article"),
    ("phone", "Телефон", "text", "g-phone"),
    ("name", "Имя", "text", "g-name"),
    ("description", "Описание", "text", "g-wide"),
    ("places", "Мест", "num", "g-num"),
    ("weight", "Вес, кг", "num", "g-num"),
    ("volume", "Объём, м³", "num", "g-num"),
    ("amount", "Сумма, ₽", "num", "g-money"),
    ("track_number", "Трек", "text", "g-track"),
    ("seller", "Продавец", "text", "g-name"),
    ("packaging", "Упаковка", "select", "g-select"),
    ("fragile", "Хрупкое", "select", "g-flag"),
    ("note", "Заметка", "text", "g-wide"),
]
REQUIRED_CELLS = {"article", "phone"}


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


# ---------- grid ----------


def rows_from_post(post) -> list[dict]:
    """The grid posts one list per column; zip them back into rows of cell texts."""
    columns = {key: post.getlist(key) for key in CELL_KEYS}
    count = max((len(values) for values in columns.values()), default=0)
    return [
        {key: (values[n] if n < len(values) else "") for key, values in columns.items()}
        for n in range(count)
    ]


def empty_row() -> dict:
    return {key: "" for key in CELL_KEYS} | {"packaging": Cargo.Packaging.BOX}


def _options(key: str) -> list[tuple[str, str]]:
    return list(Cargo.Packaging.choices) if key == "packaging" else FRAGILE_OPTIONS


def _grid_rows(rows: list[dict], preview: ImportPreview, show_errors: bool) -> list[dict]:
    by_line = {row.line: row for row in preview.rows}
    result = []
    for n, cells in enumerate(rows, start=1):
        parsed = by_line.get(n)
        values = parsed.raw if parsed else cells
        invalid = set(parsed.invalid) if (parsed and show_errors) else set()
        row_cells = []
        for key, label, kind, css in GRID_COLUMNS:
            value = values.get(key, "") or ""
            cell = {"key": key, "label": label, "kind": kind, "css": css, "value": value}
            cell["invalid"] = key in invalid
            if kind == "select":
                options = _options(key)
                known = {option for option, _ in options}
                cell["options"] = options
                cell["unknown"] = value if value not in known else ""
            row_cells.append(cell)
        result.append(
            {
                "n": n,
                "index": n - 1,
                "cells": row_cells,
                "errors": parsed.errors if (parsed and show_errors) else [],
                "new_client": bool(parsed and parsed.new_client),
            }
        )
    return result


def _totals(preview: ImportPreview) -> dict:
    rows = preview.rows
    return {
        "rows": len(rows),
        "places": sum(row.places or 0 for row in rows),
        "weight": sum((row.weight_kg for row in rows if row.weight_kg is not None), start=0),
        "amount": sum((row.amount_due for row in rows if row.amount_due is not None), start=0),
    }


def _grid_context(rows, *, checked: bool, mode: str) -> dict:
    preview = parse_rows(rows)
    has_rows = bool(preview.rows)
    return {
        "grid": _grid_rows(rows, preview, checked),
        "grid_preview": preview,
        "columns": [
            {"key": key, "label": label, "css": css, "required": key in REQUIRED_CELLS}
            for key, label, _kind, css in GRID_COLUMNS
        ],
        "checked": checked,
        "mode": mode,
        "grid_errors": preview.errors if checked else [],
        "grid_row_errors": checked and preview.error_count > 0,
        "summary": summary(preview) if (checked and has_rows) else "",
        "totals": _totals(preview),
        "save_count": len(preview.rows),
    }


def _render(request, truck, context, form=None):
    context = {"nav_active": "trucks", "truck": truck, "form": form, **context}
    if request.headers.get("HX-Request") and "grid" in context:
        return render(request, "panel/trucks/_import_grid.html", context)
    if context["form"] is None:
        context["form"] = ManifestUploadForm()
    return render(request, "panel/trucks/import.html", context)


def _remember(request, truck, preview: ImportPreview) -> None:
    """Keep only a clean preview in the session (for the «apply» endpoint)."""
    if preview.has_errors:
        request.session.pop(session_key(truck), None)
    else:
        request.session[session_key(truck)] = preview.to_dict()


def _saved(request, truck, count: int):
    messages.success(
        request,
        f"{plural(count, 'Сохранён', 'Сохранено', 'Сохранено')} "
        f"{count_phrase(count, 'груз', 'груза', 'грузов')} в машину {truck.number}.",
    )
    target = reverse("panel:truck", args=[truck.pk])
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = target
        return response
    return redirect(target)


def _grid_post(request, truck):
    rows = rows_from_post(request.POST) or [empty_row()]
    mode = "manual" if request.POST.get("mode") == "manual" else "file"
    checked = request.POST.get("checked") == "1"

    if "delete" in request.POST:
        index = request.POST.get("delete", "")
        if index.isdigit() and int(index) < len(rows):
            del rows[int(index)]
        rows = rows or [empty_row()]
        return _render(request, truck, _grid_context(rows, checked=checked, mode=mode))
    if "add_row" in request.POST:
        rows.append(empty_row())
        return _render(request, truck, _grid_context(rows, checked=checked, mode=mode))

    context = _grid_context(rows, checked=True, mode=mode)
    preview = context["grid_preview"]
    _remember(request, truck, preview)
    if "save" in request.POST and not preview.has_errors:
        try:
            count = apply_manifest(preview, truck)
        except (InvalidTransition, ValueError, ValidationError) as exc:
            text = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            context["grid_errors"] = [f"Ничего не сохранено: {text}"]
        else:
            request.session.pop(session_key(truck), None)
            return _saved(request, truck, count)
    return _render(request, truck, context)


@manager_required
def truck_import(request, pk):
    truck, refusal = _loading_truck_or_redirect(request, pk)
    if refusal:
        return refusal

    if request.method == "POST" and "upload" not in request.POST and not request.FILES:
        return _grid_post(request, truck)

    form = ManifestUploadForm(request.POST or None, request.FILES or None)
    context = {}
    if request.method == "POST":
        request.session.pop(session_key(truck), None)
        if form.is_valid():
            parsed = parse_manifest(form.cleaned_data["file"])
            if parsed.errors:
                context["file_errors"] = parsed.errors
            else:
                rows = [row.raw for row in parsed.rows]
                context = _grid_context(rows, checked=True, mode="file")
                _remember(request, truck, context["grid_preview"])
    elif request.GET.get("manual"):
        context = _grid_context(
            [empty_row() for _ in range(MANUAL_ROWS)], checked=False, mode="manual"
        )
    return _render(request, truck, context, form)


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

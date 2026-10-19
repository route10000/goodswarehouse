"""Clients: search list, new client, card (summary, cargo, notifications, calls, Telegram
link with a QR code) and edit."""

from decimal import Decimal
from io import BytesIO

import segno
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe

from logistics import services
from logistics.models import Cargo, Client
from panel.forms import ClientForm, NewClientForm
from panel.permissions import manager_required

from .common import any_of, paginate, phone_q

HISTORY_LIMIT = 20


def client_search_q(text: str) -> Q:
    return any_of(
        Q(name__icontains=text),
        Q(code__icontains=text),
        phone_q(text, "phone"),
    )


@login_required
def client_list(request):
    text = request.GET.get("q", "").strip()
    clients = Client.objects.annotate(
        cargo_total=Count("cargos"),
        cargo_active=Count("cargos", filter=~Q(cargos__status=Cargo.Status.PICKED_UP)),
    ).order_by("code")
    if text:
        clients = clients.filter(client_search_q(text))
    return render(
        request,
        "panel/clients/list.html",
        {"nav_active": "clients", "page": paginate(request, clients), "q": text},
    )


def bot_link(client) -> str | None:
    """Deep link from the bot app; ``None`` while the bot is not configured or not built."""
    try:
        from bot.links import deep_link
    except ImportError:
        return None
    return deep_link(client)


def qr_code(link: str):
    return segno.make(link, error="m")


def qr_inline(link: str) -> str:
    """Inline SVG for the card; colours come from CSS (``.qr``, ``.qr__path``)."""
    svg = qr_code(link).svg_inline(
        scale=4, border=2, light=None, svgclass="qr", lineclass="qr__path", omitsize=True
    )
    return mark_safe(svg.replace("<svg ", '<svg role="img" aria-label="QR-код ссылки на бота" ', 1))


def client_summary(client) -> dict:
    cargos = client.cargos.order_by()
    waiting = cargos.exclude(status=Cargo.Status.PICKED_UP).filter(paid=False)
    return {
        "total": cargos.count(),
        "ready": cargos.filter(status=Cargo.Status.READY).count(),
        "amount": waiting.aggregate(total=Sum("amount_due"))["total"] or Decimal("0"),
        "last_call": client.contact_attempts.order_by("-created_at", "-id").first(),
    }


@manager_required
def client_create(request):
    form = NewClientForm(request.POST or None)
    existing = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            client, created = services.get_or_create_client(data["phone"], data["name"])
            if created and data["notes"]:
                client.notes = data["notes"]
                client.save(update_fields=["notes"])
        if created:
            messages.success(request, f"Клиент {client.code} создан.")
            return redirect("panel:client", pk=client.pk)
        existing = client
    return render(
        request,
        "panel/clients/new.html",
        {"nav_active": "clients", "form": form, "existing": existing},
    )


QR_FORMATS = {
    "svg": ("image/svg+xml", {"scale": 10, "border": 4}),
    "png": ("image/png", {"scale": 10, "border": 4}),
}


@login_required
def client_qr(request, pk, fmt):
    """«Скачать QR»: the bot link as an SVG or PNG file; 404 while the bot is not set up."""
    if fmt not in QR_FORMATS:
        raise Http404
    client = get_object_or_404(Client, pk=pk)
    link = bot_link(client)
    if not link:
        raise Http404
    content_type, options = QR_FORMATS[fmt]
    buffer = BytesIO()
    qr_code(link).save(buffer, kind=fmt, **options)
    response = HttpResponse(buffer.getvalue(), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{client.code}-telegram.{fmt}"'
    return response


@login_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    link = bot_link(client)
    return render(
        request,
        "panel/clients/detail.html",
        {
            "nav_active": "clients",
            "client": client,
            "bot_link": link,
            "qr_svg": qr_inline(link) if link else "",
            "summary": client_summary(client),
            "cargos": client.cargos.select_related("truck").order_by("-created_at", "-id"),
            "notifications": client.notifications.select_related("truck").order_by(
                "-created_at", "-id"
            )[:HISTORY_LIMIT],
            "attempts": client.contact_attempts.select_related("staff", "cargo").order_by(
                "-created_at", "-id"
            )[:HISTORY_LIMIT],
        },
    )


@manager_required
def client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        edited = form.save(commit=False)
        # Only the form's fields: the bot may link Telegram to this client meanwhile.
        edited.save(update_fields=ClientForm.Meta.fields)
        messages.success(request, f"Клиент {edited.code} сохранён.")
        return redirect("panel:client", pk=pk)
    return render(
        request,
        "panel/clients/form.html",
        {"nav_active": "clients", "form": form, "client": Client.objects.get(pk=pk)},
    )

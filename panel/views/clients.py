"""Clients: search list, card (cargo, notifications, calls, Telegram link) and edit."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from logistics.models import Cargo, Client
from panel.forms import ClientForm
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


@login_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    return render(
        request,
        "panel/clients/detail.html",
        {
            "nav_active": "clients",
            "client": client,
            "bot_link": bot_link(client),
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

"""Command search in the top bar: jump straight to an exact match, else grouped results."""

import re
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from logistics.models import Cargo, Client, Truck

from .clients import client_search_q
from .common import any_of, exact_phone, phone_q

TRUCK_RE = re.compile(r"^R-\d{4}-\d+$", re.IGNORECASE)
CODE_RE = re.compile(r"^GW-\d+$", re.IGNORECASE)
GROUP_LIMIT = 10


def exact_match(text: str) -> str | None:
    """URL of the one object ``text`` names exactly, in priority order, or ``None``."""
    if TRUCK_RE.match(text):
        truck = Truck.objects.filter(number__iexact=text).first()
        if truck:
            return reverse("panel:truck", args=[truck.pk])
    if CODE_RE.match(text):
        client = Client.objects.filter(code__iexact=text).first()
        if client:
            return reverse("panel:client", args=[client.pk])
    phone = exact_phone(text)
    if phone:
        client = Client.objects.filter(phone=phone).first()
        if client:
            return reverse("panel:client", args=[client.pk])
    if Cargo.objects.filter(article__iexact=text).exists():
        return f"{reverse('panel:cargo')}?{urlencode({'q': text})}"
    return None


@login_required
def search(request):
    text = request.GET.get("q", "").strip()
    if text:
        target = exact_match(text)
        if target:
            return redirect(target)
    clients = cargos = trucks = []
    if text:
        clients = list(Client.objects.filter(client_search_q(text)).order_by("code")[:GROUP_LIMIT])
        cargos = list(
            Cargo.objects.filter(
                any_of(
                    Q(article__icontains=text),
                    Q(description__icontains=text),
                    phone_q(text, "client__phone"),
                )
            )
            .select_related("client", "truck")
            .order_by("-created_at", "-id")[:GROUP_LIMIT]
        )
        trucks = list(
            Truck.objects.filter(
                Q(number__icontains=text) | Q(plate__icontains=text) | Q(route__icontains=text)
            ).order_by("-created_at", "-id")[:GROUP_LIMIT]
        )
    return render(
        request,
        "panel/search.html",
        {
            "search_query": text,
            "clients": clients,
            "cargos": cargos,
            "trucks": trucks,
            "found": bool(clients or cargos or trucks),
        },
    )

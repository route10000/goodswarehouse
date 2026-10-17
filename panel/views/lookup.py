"""Live client lookup by phone for the add-cargo forms (HTMX, debounced in the template)."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from logistics.models import Client

from .common import exact_phone


def lookup_context(phone_raw: str, name: str = "") -> dict:
    """``found`` (existing client), ``new`` (valid phone, nobody has it) or ``empty``."""
    phone = exact_phone(phone_raw or "")
    if not phone:
        return {"state": "empty", "name": name}
    client = Client.objects.filter(phone=phone).first()
    if client:
        return {"state": "found", "client": client, "name": name}
    return {"state": "new", "name": name}


@login_required
def client_lookup(request):
    context = {"lookup": lookup_context(request.GET.get("phone", ""), request.GET.get("name", ""))}
    return render(request, "panel/includes/client_lookup.html", context)

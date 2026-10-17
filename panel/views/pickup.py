"""Выдача (SPEC §10): find a client, tick their ready cargo, hand it out in one go.

Statuses change only through ``logistics.services.cargo_pickup`` (which also closes an
arrived truck once its last cargo is out). Both staff groups may hand out cargo.
"""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from logistics import services
from logistics.models import Cargo, Client
from logistics.services import InvalidTransition
from public.templatetags.gw_format import ru_date, ru_datetime

from .common import any_of, count_phrase, phone_q

CLIENT_LIMIT = 20
MIN_PREFIX = 3
CODE_RE = re.compile(r"^gw-?(\d{1,6})$", re.IGNORECASE)
WAITING = [Cargo.Status.RECEIVED, Cargo.Status.LOADED, Cargo.Status.IN_TRANSIT]


def _code_q(q: str) -> Q | None:
    match = CODE_RE.match(q)
    if match:
        return Q(code=f"GW-{int(match.group(1)):04d}")
    return None


def _article_cargos(q: str):
    exact = Cargo.objects.filter(article__iexact=q)
    if exact.exists() or len(q) < MIN_PREFIX:
        return exact
    return Cargo.objects.filter(article__istartswith=q).exclude(status=Cargo.Status.PICKED_UP)


def find_clients(q: str) -> tuple[list[Client], set[str]]:
    """Clients matching an article, a phone (any format) or a client code."""
    q = q.strip()
    if not q:
        return [], set()
    by_article = _article_cargos(q)
    articles = {a.lower() for a in by_article.values_list("article", flat=True)}
    clients = Client.objects.filter(
        any_of(
            Q(pk__in=by_article.values("client_id")),
            _code_q(q),
            Q(code__iexact=q),
            phone_q(q, "phone"),
        )
    ).order_by("code")[:CLIENT_LIMIT]
    return list(clients), articles


def _explain(cargo: Cargo) -> str:
    if cargo.status == Cargo.Status.PICKED_UP:
        when = ru_date(cargo.picked_up_at)
        return f"Уже выдан {when}" if when else "Уже выдан"
    if cargo.status == Cargo.Status.IN_TRANSIT:
        eta = cargo.truck.eta if cargo.truck else None
        tail = f", ожидается {ru_datetime(eta)}" if eta else ""
        return f"В пути, ещё не на складе{tail}"
    if cargo.status == Cargo.Status.LOADED:
        return "Погружен в машину в Уссурийске, ещё не отправлен"
    return "Принят в Уссурийске, ещё не отправлен"


def client_cargo(client: Client, articles: set[str]) -> dict:
    today = timezone.localdate()
    cargos = client.cargos.select_related("truck").order_by("ready_at", "created_at", "id")
    ready = list(cargos.filter(status=Cargo.Status.READY))
    for cargo in ready:
        cargo.days = (today - timezone.localdate(cargo.ready_at)).days if cargo.ready_at else 0
        cargo.overdue = bool(cargo.free_storage_until and cargo.free_storage_until < today)
    other = list(cargos.filter(status__in=WAITING))
    if articles:  # the searched article was already handed out: say so
        picked = cargos.filter(status=Cargo.Status.PICKED_UP)
        other += list(picked.filter(any_of(*(Q(article__iexact=a) for a in articles))))
    for cargo in other:
        cargo.explanation = _explain(cargo)
    return {
        "client": client,
        "ready": ready,
        "other": other,
        "places": sum(c.places for c in ready),
        "amount": sum((c.amount_due for c in ready), start=0),
    }


@login_required
def pickup(request):
    q = request.GET.get("q", "").strip()
    context = {"nav_active": "pickup", "q": q, "searched": bool(q), "clients": [], "result": None}
    client_pk = request.GET.get("client", "")
    if client_pk.isdigit():
        client = get_object_or_404(Client, pk=client_pk)
        context["result"] = client_cargo(client, set())
        context["searched"] = True
    elif q:
        clients, articles = find_clients(q)
        if len(clients) == 1:
            context["result"] = client_cargo(clients[0], articles)
        context["clients"] = clients
    template = "panel/pickup/_results.html" if request.headers.get("HX-Request") else None
    return render(request, template or "panel/pickup/page.html", context)


@require_POST
@login_required
def pickup_issue(request):
    client = get_object_or_404(Client, pk=request.POST.get("client") or 0)
    back = f"{reverse('panel:pickup')}?client={client.pk}"
    ids = [int(x) for x in request.POST.getlist("cargo") if x.isdigit()]
    if not ids:
        messages.error(request, "Отметьте грузы, которые выдаёте")
        return redirect(back)
    cargos = list(Cargo.objects.filter(pk__in=ids, client=client).order_by("id"))
    if len(cargos) != len(set(ids)):
        messages.error(request, "Среди отмеченных есть чужой груз. Ничего не выдано")
        return redirect(back)
    paid = bool(request.POST.get("paid"))
    try:
        with transaction.atomic():
            for cargo in cargos:
                services.cargo_pickup(cargo, request.user, paid=paid)
    except InvalidTransition as exc:
        messages.error(request, f"{exc}. Ничего не выдано")
        return redirect(back)
    messages.success(request, f"Выдано {count_phrase(len(cargos), 'груз', 'груза', 'грузов')}")
    return redirect("panel:pickup")

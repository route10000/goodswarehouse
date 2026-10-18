"""Helpers shared by panel views: pagination, Russian plurals, phone-aware lookups."""

import re

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils.http import urlencode

from logistics.phones import InvalidPhone, normalize_phone

PAGE_SIZE = 50
MIN_PHONE_DIGITS = 4


def paginate(request, queryset, per_page: int = PAGE_SIZE):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for ``n``: 1 груз, 2 груза, 5 грузов (11–14 → many)."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count_phrase(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"


def exact_phone(text: str) -> str | None:
    try:
        return normalize_phone(text)
    except InvalidPhone:
        return None


def phone_q(text: str, field: str) -> Q | None:
    """Exact match for a full phone in any format, else a digits substring (≥ 4 digits)."""
    full = exact_phone(text)
    if full:
        return Q(**{field: full})
    digits = re.sub(r"\D", "", text)
    if len(digits) >= MIN_PHONE_DIGITS:
        return Q(**{f"{field}__contains": digits})
    return None


def any_of(*conditions: Q | None) -> Q:
    result = Q(pk__in=[])
    for condition in conditions:
        if condition is not None:
            result |= condition
    return result


def warehouse_url(tab: str, **params) -> str:
    """«Склад» page on ``tab`` with extra query params (empty values are left out)."""
    query = {"tab": tab, **{key: value for key, value in params.items() if value}}
    return f"{reverse('panel:warehouse')}?{urlencode(query)}"

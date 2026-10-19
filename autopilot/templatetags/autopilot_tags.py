"""«Автопилот» journal helpers."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Trip R-2026-012, client GW-0042, articles like CN2645-1234.
CODE = re.compile(r"\b(R-\d{4}-\d{3,}|GW-\d{4,}|[A-Z]{2}\d{2,}-\d{3,})\b")


@register.filter
def ap_codes(message) -> str:
    """Escape a journal message and set trip, client and article codes in mono."""
    return mark_safe(CODE.sub(r'<span class="mono">\1</span>', escape(message)))

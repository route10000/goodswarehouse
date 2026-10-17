"""Ops-центр template helpers."""

from django import template

from panel.views.common import plural

register = template.Library()


@register.filter
def ru_plural(value, forms: str) -> str:
    """``{{ n|ru_plural:"место,места,мест" }}`` → the Russian plural form for ``n``."""
    one, few, many = (form.strip() for form in forms.split(","))
    try:
        n = int(value)
    except (TypeError, ValueError):
        return many
    return plural(n, one, few, many)

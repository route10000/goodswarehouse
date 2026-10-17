"""Panel-wide template context: the «Ордер на день» sidebar badge."""

from django.utils.functional import SimpleLazyObject


def order_badge(request):
    """Lines still to call in today's order; lazy, so only pages with the sidebar query it."""
    if not request.path.startswith("/panel/") or not request.user.is_authenticated:
        return {}

    def remaining():
        from panel.views.order import remaining_lines

        return remaining_lines()

    return {"order_remaining": SimpleLazyObject(remaining)}

"""Panel roles (SPEC §5): «Менеджер» (or superuser) may change data; «Склад» only looks.

Every panel view needs a logged-in user. Views that change trucks, cargo or clients are
wrapped in ``manager_required``; everyone else gets a panel-styled 403 page.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

MANAGER_GROUP = "Менеджер"
WAREHOUSE_GROUP = "Склад"


def _group_names(user) -> set[str]:
    cached = getattr(user, "_gw_groups", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gw_groups = cached
    return cached


def is_manager(user) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return user.is_superuser or MANAGER_GROUP in _group_names(user)


def is_warehouse(user) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return WAREHOUSE_GROUP in _group_names(user) and not is_manager(user)


def forbidden(request):
    return render(request, "panel/403.html", status=403)


def manager_required(view):
    """Login first (redirect), then a 403 page for anyone who is not a manager."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_manager(request.user):
            return forbidden(request)
        return view(request, *args, **kwargs)

    return login_required(wrapper)

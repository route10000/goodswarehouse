"""Staff login (DESIGN «Вход в систему»): segment switch and a group-based landing page."""

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.shortcuts import resolve_url
from django.urls import reverse

from panel.permissions import is_warehouse

PICKUP_SEGMENT = "warehouse"
PANEL_SEGMENT = "panel"


class StaffLoginView(auth_views.LoginView):
    """Django's LoginView (safe ``next`` validation kept); without ``next`` «Склад» → Выдача."""

    template_name = "panel/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        if is_warehouse(self.request.user):
            return reverse("panel:warehouse")
        return resolve_url(settings.LOGIN_REDIRECT_URL)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pickup_url = reverse("panel:pickup")
        next_url = context.get(self.redirect_field_name) or ""
        context["segment"] = PICKUP_SEGMENT if next_url.startswith(pickup_url) else PANEL_SEGMENT
        context["panel_url"] = reverse("panel:ops")
        context["pickup_url"] = pickup_url
        return context

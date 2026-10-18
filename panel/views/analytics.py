"""«Аналитика» (DESIGN v1.0-final «Графики»). Stub: the charts arrive in a later task."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def analytics(request):
    return render(request, "panel/analytics/index.html", {"nav_active": "analytics"})

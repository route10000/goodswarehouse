from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def ops_center(request):
    return render(request, "panel/ops.html", {"nav_active": "ops"})

"""«Склад» (SPEC §14.6): will hold the tabs «Выдача · Ордер на звонок · На складе».

Until then the section opens the pickup page; ``/panel/pickup/`` and ``/panel/order/`` stay.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def warehouse(request):
    return redirect("panel:pickup")

"""«Аналитика» (`/panel/analytics/?period=7|30|90`, DESIGN v1.0-final «Графики»).

Read-only and open to both staff groups. Aggregations live in ``panel.analytics_data``;
the charts are drawn by ``static/js/analytics.js`` from the embedded JSON.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from panel import analytics_data


@require_GET
@login_required
def analytics(request):
    days = analytics_data.parse_period(request.GET.get("period"))
    data = analytics_data.analytics(days)
    return render(
        request,
        "panel/analytics/index.html",
        {
            "nav_active": "analytics",
            "analytics": data,
            "chart_json": analytics_data.chart_json(data),
        },
    )

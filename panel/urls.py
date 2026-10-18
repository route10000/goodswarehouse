from django.contrib.auth import views as auth_views
from django.urls import path

from .views import cargo, clients, imports, lookup, ops, search, trucks
from .views import order as order_views
from .views import pickup as pickup_views
from .views.auth import StaffLoginView

app_name = "panel"

urlpatterns = [
    path("login/", StaffLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", ops.ops_center, name="ops"),
    path("ops/live/", ops.ops_live, name="ops_live"),
    path("ops/truck/<int:pk>/", ops.ops_truck, name="ops_truck"),
    path("api/scene/", ops.scene_api, name="ops_scene"),
    path("search/", search.search, name="search"),
    # trucks
    path("trucks/", trucks.truck_list, name="trucks"),
    path("trucks/new/", trucks.truck_create, name="truck_new"),
    path("trucks/<int:pk>/", trucks.truck_detail, name="truck"),
    path("trucks/<int:pk>/edit/", trucks.truck_edit, name="truck_edit"),
    path("trucks/<int:pk>/status/<slug:action>/", trucks.truck_status, name="truck_status"),
    path("trucks/<int:pk>/cargo/add/", trucks.truck_cargo_add, name="truck_cargo_add"),
    path("trucks/<int:pk>/import/", imports.truck_import, name="truck_import"),
    path("trucks/<int:pk>/import/apply/", imports.truck_import_apply, name="truck_import_apply"),
    path("import/template.xlsx", imports.import_template, name="import_template"),
    # cargo
    path("cargo/", cargo.cargo_list, name="cargo"),
    path("cargo/new/", cargo.cargo_create, name="cargo_new"),
    # clients
    path("clients/", clients.client_list, name="clients"),
    path("clients/lookup/", lookup.client_lookup, name="client_lookup"),
    path("clients/<int:pk>/", clients.client_detail, name="client"),
    path("clients/<int:pk>/edit/", clients.client_edit, name="client_edit"),
    # daily call order (T7)
    path("order/", order_views.order_today, name="order"),
    path("order/<str:day>/", order_views.order_day, name="order_day"),
    path("order/<str:day>/build/", order_views.order_build, name="order_build"),
    path("order/<str:day>/add/", order_views.order_add, name="order_add"),
    path("order/<str:day>/candidates/", order_views.order_candidates, name="order_candidates"),
    path("order/<str:day>/print/", order_views.order_print, name="order_print"),
    path("order/<str:day>/export.xlsx", order_views.order_export, name="order_export"),
    path("order/<str:day>/line/<int:pk>/", order_views.line_row, name="order_line"),
    path("order/<str:day>/line/<int:pk>/mark/", order_views.line_mark, name="order_line_mark"),
    path(
        "order/<str:day>/line/<int:pk>/remove/", order_views.line_remove, name="order_line_remove"
    ),
    # pickup (T7)
    path("pickup/", pickup_views.pickup, name="pickup"),
    path("pickup/issue/", pickup_views.pickup_issue, name="pickup_issue"),
]

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .views import cargo, clients, imports, lookup, search, trucks

app_name = "panel"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="panel/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.ops_center, name="ops"),
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
]

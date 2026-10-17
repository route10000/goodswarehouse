from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("schedule/", views.schedule, name="schedule"),
    path("track/", views.track, name="track"),
    path("contacts/", views.contacts, name="contacts"),
]

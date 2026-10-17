"""Notification admin: read-only status, the sender owns delivery fields."""

from django.contrib import admin

from notify.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "client", "kind", "status", "truck", "attempts", "sent_at"]
    list_filter = ["status", "kind", "channel"]
    search_fields = ["client__phone", "client__code", "client__name", "truck__number"]
    list_select_related = ["client", "truck"]
    raw_id_fields = ["client", "cargo", "truck"]
    readonly_fields = [
        "status",
        "attempts",
        "last_error",
        "created_at",
        "created_on",
        "sent_at",
    ]

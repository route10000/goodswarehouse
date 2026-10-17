"""Admin. Statuses are read-only here: they change only via ``logistics.services``.

Order lines and the call journal are view-only: ``logistics.orders`` writes them.
"""

from django.contrib import admin

from logistics.models import (
    Cargo,
    Client,
    ContactAttempt,
    Counter,
    DailyOrder,
    DailyOrderLine,
    Truck,
)
from logistics.phones import InvalidPhone, normalize_phone


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "phone", "has_telegram", "created_at"]
    search_fields = ["code", "name", "phone"]
    readonly_fields = ["code", "telegram_link_token", "created_at"]

    @admin.display(boolean=True, description="Telegram")
    def has_telegram(self, obj):
        return obj.has_telegram

    def get_search_results(self, request, queryset, search_term):
        results, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        try:
            phone = normalize_phone(search_term)
        except InvalidPhone:
            return results, may_have_duplicates
        return results | queryset.filter(phone=phone), True


@admin.register(Truck)
class TruckAdmin(admin.ModelAdmin):
    list_display = ["number", "route", "status", "departure_date", "eta", "plate", "is_public"]
    list_filter = ["status", "is_public"]
    search_fields = ["number", "plate", "driver_phone"]
    readonly_fields = ["number", "status", "departed_at", "arrived_at", "created_at"]


@admin.register(Cargo)
class CargoAdmin(admin.ModelAdmin):
    list_display = [
        "article",
        "client",
        "truck",
        "status",
        "places",
        "weight_kg",
        "amount_due",
        "paid",
        "free_storage_until",
    ]
    list_filter = ["status", "paid"]
    search_fields = ["article", "client__phone", "client__code", "client__name", "truck__number"]
    list_select_related = ["client", "truck"]
    autocomplete_fields = ["client"]
    # truck is read-only too: assigning it must go through services.assign_to_truck.
    readonly_fields = [
        "truck",
        "status",
        "ready_at",
        "picked_up_at",
        "picked_up_by",
        "free_storage_until",
        "created_at",
    ]


class ViewOnlyAdmin(admin.ModelAdmin):
    """Rows written only by ``logistics.orders`` (build/add/remove/mark): view only here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DailyOrderLineInline(admin.TabularInline):
    model = DailyOrderLine
    extra = 0
    can_delete = False
    fields = ["article", "client_name", "phone", "amount_due", "call_status", "promised_date"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyOrder)
class DailyOrderAdmin(admin.ModelAdmin):
    list_display = ["date", "created_at", "created_by"]
    readonly_fields = ["created_at"]
    date_hierarchy = "date"
    inlines = [DailyOrderLineInline]


@admin.register(DailyOrderLine)
class DailyOrderLineAdmin(ViewOnlyAdmin):
    list_display = ["order", "article", "client_name", "phone", "call_status", "promised_date"]
    list_filter = ["call_status", "order__date"]
    search_fields = ["article", "phone", "client_name"]
    list_select_related = ["order"]
    raw_id_fields = ["cargo"]
    readonly_fields = ["call_status", "updated_by", "updated_at"]


@admin.register(ContactAttempt)
class ContactAttemptAdmin(ViewOnlyAdmin):
    list_display = ["created_at", "client", "cargo", "staff", "outcome", "promised_date"]
    list_filter = ["outcome"]
    search_fields = ["client__phone", "client__code", "client__name", "cargo__article"]
    list_select_related = ["client", "cargo", "staff"]
    autocomplete_fields = ["client"]
    raw_id_fields = ["cargo"]
    readonly_fields = ["created_at"]


@admin.register(Counter)
class CounterAdmin(admin.ModelAdmin):
    list_display = ["name", "value"]
    readonly_fields = ["name", "value"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

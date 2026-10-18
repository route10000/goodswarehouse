"""Panel template helpers: roles, status pill classes, phone display, query strings."""

import phonenumbers
from django import template

from logistics.models import Cargo, ContactAttempt, Truck
from notify.models import Notification
from panel import permissions

register = template.Library()

TRUCK_PILL = {
    Truck.Status.LOADING: "st-loading",
    Truck.Status.IN_TRANSIT: "st-transit",
    Truck.Status.ARRIVED: "st-ready",
    Truck.Status.CLOSED: "st-done",
}
CARGO_PILL = {
    Cargo.Status.RECEIVED: "st-loading",
    Cargo.Status.LOADED: "st-loading",
    Cargo.Status.IN_TRANSIT: "st-transit",
    Cargo.Status.READY: "st-ready",
    Cargo.Status.PICKED_UP: "st-done",
}
NOTIFICATION_PILL = {
    Notification.Status.PENDING: "st-transit",
    Notification.Status.SENT: "st-ready",
    Notification.Status.FAILED: "st-error",
    Notification.Status.SKIPPED: "st-done",
}
ATTEMPT_PILL = {
    ContactAttempt.Outcome.REACHED: "st-ready",
    ContactAttempt.Outcome.NO_ANSWER: "st-error",
}


@register.filter
def is_manager(user) -> bool:
    return permissions.is_manager(user)


@register.filter
def can_use_autopilot(user) -> bool:
    return permissions.can_use_autopilot(user)


@register.filter
def truck_pill(status) -> str:
    return TRUCK_PILL.get(status, "")


@register.filter
def cargo_pill(status) -> str:
    return CARGO_PILL.get(status, "")


@register.filter
def notification_pill(status) -> str:
    return NOTIFICATION_PILL.get(status, "")


@register.filter
def attempt_pill(outcome) -> str:
    return ATTEMPT_PILL.get(outcome, "")


@register.filter
def phone(value) -> str:
    """``+79141234567`` → ``+7 914 123-45-67``; anything unparsable is shown as is."""
    if not value:
        return ""
    try:
        number = phonenumbers.parse(value, "RU")
    except phonenumbers.NumberParseException:
        return value
    if not phonenumbers.is_valid_number(number):
        return value
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


@register.simple_tag(takes_context=True)
def query(context, **changes) -> str:
    """Current query string with ``changes`` applied; ``None`` / "" removes a key."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    if "page" not in changes:
        params.pop("page", None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"

"""«Автопилот» engine: the simulated clock and each rule (seeded rng, fixed real time)."""

import random
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from autopilot import engine
from autopilot.models import AutopilotEvent, AutopilotState
from logistics import orders, services
from logistics.models import Cargo, Client, DailyOrder, Truck
from logistics.phones import normalize_phone
from notify.models import Notification

pytestmark = pytest.mark.django_db

Kind = AutopilotEvent.Kind


def local(day, hh, mm=0):
    return timezone.make_aware(datetime.combine(day, time(hh, mm)))


REAL_NOW = local(datetime(2026, 10, 19).date(), 12, 0)


@pytest.fixture
def state():
    s = AutopilotState.load()
    s.sim_clock = REAL_NOW
    s.last_tick_at = REAL_NOW - timedelta(seconds=5)
    s.save()
    return s


@pytest.fixture
def rng():
    return random.Random(7)


def make_ctx(state, rng, now=REAL_NOW, real_now=REAL_NOW):
    state.sim_clock = now
    return engine.Tick(state=state, now=now, real_now=real_now, rng=rng)


@pytest.fixture
def ivan(db):
    client, _ = services.get_or_create_client("+79141234567", "Иван Ли")
    client.telegram_chat_id = 111
    client.save()
    return client


@pytest.fixture
def petr(db):
    client, _ = services.get_or_create_client("+79141234568", "Пётр Ким")
    return client


def loading_truck(capacity=40, loading_starts=REAL_NOW, **extra):
    return services.create_truck(
        capacity_places=capacity, loading_starts=loading_starts, departure_date=REAL_NOW.date()
    )


def fill(truck, client, places):
    return services.add_cargo(client, f"T{truck.pk}-{places}", places=places, truck=truck)


def events(kind=None):
    qs = AutopilotEvent.objects.order_by("id")
    if kind:
        qs = qs.filter(kind=kind)
    return list(qs)


# ---------- clock ----------


def test_clock_advances_by_real_elapsed_times_speed(state):
    state.speed = 10
    state.last_tick_at = REAL_NOW - timedelta(seconds=30)
    engine.advance_clock(state, REAL_NOW)
    assert state.sim_clock == REAL_NOW + timedelta(minutes=5)
    assert state.last_tick_at == REAL_NOW


def test_clock_advance_is_capped_at_ten_sim_minutes(state):
    state.speed = 60
    state.last_tick_at = REAL_NOW - timedelta(hours=1)
    engine.advance_clock(state, REAL_NOW)
    assert state.sim_clock == REAL_NOW + timedelta(minutes=10)


def test_first_tick_uses_the_default_step(state):
    state.speed = 60
    state.last_tick_at = None
    engine.advance_clock(state, REAL_NOW)
    assert state.sim_clock == REAL_NOW + timedelta(minutes=5)  # 5 s × 60


def test_explicit_elapsed_overrides_the_real_gap(state):
    state.speed = 1
    state.last_tick_at = REAL_NOW - timedelta(hours=5)
    engine.advance_clock(state, REAL_NOW, elapsed=timedelta(seconds=5))
    assert state.sim_clock == REAL_NOW + timedelta(seconds=5)


def test_clock_never_goes_back(state):
    state.last_tick_at = REAL_NOW + timedelta(minutes=3)  # real clock moved back
    engine.advance_clock(state, REAL_NOW)
    assert state.sim_clock == REAL_NOW


def test_missing_sim_clock_starts_at_real_now(state):
    state.sim_clock = None
    state.last_tick_at = REAL_NOW - timedelta(seconds=1)
    engine.advance_clock(state, REAL_NOW)
    assert state.sim_clock == REAL_NOW + timedelta(seconds=1)


# ---------- loading trucks ----------


def test_creates_a_loading_truck_when_none(state, rng):
    ctx = make_ctx(state, rng)
    engine.ensure_loading_trucks(ctx)
    truck = Truck.objects.get()
    assert truck.status == Truck.Status.LOADING
    assert 40 <= truck.capacity_places <= 60
    assert truck.departure_date == REAL_NOW.date()
    assert truck.loading_starts == REAL_NOW
    departure = REAL_NOW + engine.MAX_LOADING
    assert departure + timedelta(hours=2) <= truck.eta <= departure + timedelta(hours=4)
    assert normalize_phone(truck.driver_phone) == truck.driver_phone
    [event] = events(Kind.TRUCK_CREATED)
    assert event.truck == truck
    assert truck.number in event.message
    assert event.sim_at == REAL_NOW
    assert ctx.counts["trucks_created"] == 1


def test_no_second_truck_while_the_first_is_under_half_full(state, rng, ivan):
    fill(loading_truck(capacity=40), ivan, 19)
    engine.ensure_loading_trucks(make_ctx(state, rng))
    assert Truck.objects.count() == 1
    assert events() == []


def test_second_truck_once_the_first_is_half_full(state, rng, ivan):
    fill(loading_truck(capacity=40), ivan, 20)
    engine.ensure_loading_trucks(make_ctx(state, rng))
    assert Truck.objects.filter(status=Truck.Status.LOADING).count() == 2


def test_never_more_than_two_loading_trucks(state, rng, ivan):
    fill(loading_truck(capacity=40), ivan, 30)
    fill(loading_truck(capacity=40), ivan, 30)
    engine.ensure_loading_trucks(make_ctx(state, rng))
    assert Truck.objects.count() == 2


# ---------- received cargo ----------


def test_adds_one_to_three_cargo_into_the_loading_truck(state, rng, ivan):
    truck = loading_truck(capacity=60)
    ctx = make_ctx(state, rng)
    engine.receive_cargo(ctx)
    added = list(Cargo.objects.all())
    assert 1 <= len(added) <= 3
    for cargo in added:
        assert cargo.status == Cargo.Status.LOADED
        assert cargo.truck == truck
        assert cargo.article and cargo.description and cargo.track_number and cargo.seller
        assert cargo.weight_kg > 0 and cargo.amount_due > 0
    assert len(events(Kind.CARGO_ADDED)) == len(added)
    assert ctx.counts["cargo_added"] == len(added)


def test_cargo_waits_when_no_truck_has_room(state, rng, ivan):
    truck = loading_truck(capacity=1)
    fill(truck, ivan, 1)
    engine.receive_cargo(make_ctx(state, rng))
    new = Cargo.objects.exclude(truck=truck)
    assert new.exists()
    assert all(c.status == Cargo.Status.RECEIVED and c.truck is None for c in new)
    assert all("ждёт машину" in e.message for e in events(Kind.CARGO_ADDED))


def test_new_clients_have_valid_phones_and_about_60_percent_telegram(state, rng):
    ctx = make_ctx(state, rng)
    clients = [engine.new_client(ctx) for _ in range(60)]
    for client in clients:
        assert client.phone.startswith("+79")
        assert normalize_phone(client.phone) == client.phone
        assert client.name
    share = sum(c.has_telegram for c in clients) / len(clients)
    assert 0.45 <= share <= 0.75


def test_cargo_goes_to_existing_or_new_clients(state, ivan, petr):
    rng = random.Random(3)
    loading_truck(capacity=60)
    for _ in range(10):
        engine.receive_cargo(make_ctx(state, rng))
    owners = set(Cargo.objects.values_list("client_id", flat=True))
    assert {ivan.pk, petr.pk} & owners
    assert owners - {ivan.pk, petr.pk}


# ---------- departures ----------


def test_truck_at_85_percent_departs_through_the_service(state, rng, ivan, petr):
    truck = loading_truck(capacity=40)
    fill(truck, ivan, 20)
    fill(truck, petr, 14)  # 34 / 40 = 85 %
    ctx = make_ctx(state, rng)
    engine.depart_trucks(ctx)
    truck.refresh_from_db()
    assert truck.status == Truck.Status.IN_TRANSIT
    assert truck.departed_at == REAL_NOW
    assert REAL_NOW + timedelta(hours=2) <= truck.eta <= REAL_NOW + timedelta(hours=4)
    assert set(truck.cargos.values_list("status", flat=True)) == {Cargo.Status.IN_TRANSIT}
    notes = {n.client_id: n.status for n in Notification.objects.filter(kind="departed")}
    assert notes == {ivan.pk: Notification.Status.PENDING, petr.pk: Notification.Status.SKIPPED}
    [event] = events(Kind.DEPARTED)
    assert event.message == f"Рейс {truck.number} отправлен (34 места)"
    assert ctx.counts["departed"] == 1


def test_truck_below_85_percent_and_young_stays(state, rng, ivan):
    truck = loading_truck(capacity=40, loading_starts=REAL_NOW - timedelta(hours=2))
    fill(truck, ivan, 33)
    engine.depart_trucks(make_ctx(state, rng))
    truck.refresh_from_db()
    assert truck.status == Truck.Status.LOADING
    assert events() == []


def test_truck_loading_for_over_three_sim_hours_departs(state, rng, ivan):
    truck = loading_truck(capacity=40, loading_starts=REAL_NOW - timedelta(hours=3, minutes=1))
    fill(truck, ivan, 5)
    engine.depart_trucks(make_ctx(state, rng))
    truck.refresh_from_db()
    assert truck.status == Truck.Status.IN_TRANSIT


def test_empty_old_truck_does_not_depart(state, rng):
    truck = loading_truck(capacity=40, loading_starts=REAL_NOW - timedelta(hours=5))
    engine.depart_trucks(make_ctx(state, rng))
    truck.refresh_from_db()
    assert truck.status == Truck.Status.LOADING


# ---------- arrivals ----------


def in_transit(client, eta, places=3):
    truck = loading_truck()
    fill(truck, client, places)
    services.truck_depart(truck, now=REAL_NOW - timedelta(hours=3))
    truck.eta = eta
    truck.save(update_fields=["eta"])
    return truck


def test_truck_with_eta_passed_arrives_through_the_service(state, rng, ivan):
    truck = in_transit(ivan, REAL_NOW - timedelta(minutes=1))
    ctx = make_ctx(state, rng)
    engine.arrive_trucks(ctx)
    truck.refresh_from_db()
    assert truck.status == Truck.Status.ARRIVED
    assert truck.arrived_at == REAL_NOW
    cargo = truck.cargos.get()
    assert cargo.status == Cargo.Status.READY
    assert cargo.ready_at == REAL_NOW
    assert Notification.objects.filter(kind="arrived", truck=truck, client=ivan).exists()
    [event] = events(Kind.ARRIVED)
    assert truck.number in event.message
    assert ctx.counts["arrived"] == 1


def test_truck_with_eta_ahead_keeps_driving(state, rng, ivan):
    truck = in_transit(ivan, REAL_NOW + timedelta(minutes=1))
    engine.arrive_trucks(make_ctx(state, rng))
    truck.refresh_from_db()
    assert truck.status == Truck.Status.IN_TRANSIT


# ---------- pickup ----------


def ready_cargo(client, ready_at, article):
    truck = loading_truck()
    cargo = services.add_cargo(client, article, places=2, truck=truck)
    services.truck_depart(truck, now=ready_at - timedelta(hours=3))
    services.truck_arrive(truck, now=ready_at)
    cargo.refresh_from_db()
    return cargo


def test_pickup_goes_through_the_service_as_autopilot_user(state, ivan):
    cargo = ready_cargo(ivan, REAL_NOW - timedelta(days=1), "OLD1")
    rng = random.Random(1)
    ctx = make_ctx(state, rng)
    for _ in range(10):  # 0–2 per tick: a few ticks are enough
        engine.pickup_cargo(ctx)
    cargo.refresh_from_db()
    assert cargo.status == Cargo.Status.PICKED_UP
    assert cargo.picked_up_at == REAL_NOW
    assert cargo.paid is True
    user = cargo.picked_up_by
    assert user.username == "autopilot"
    assert not user.has_usable_password()
    assert user.groups.filter(name="Менеджер").exists()
    [event] = events(Kind.PICKED_UP)
    assert "OLD1" in event.message
    assert ctx.counts["picked_up"] == 1
    assert cargo.truck.status == Truck.Status.CLOSED  # the service closes an emptied truck


def test_pickup_takes_at_most_two_per_tick(state, ivan):
    for i in range(6):
        ready_cargo(ivan, REAL_NOW - timedelta(days=i + 1), f"R{i}")
    rng = random.Random(5)
    for _ in range(5):
        before = Cargo.objects.filter(status=Cargo.Status.PICKED_UP).count()
        engine.pickup_cargo(make_ctx(state, rng))
        after = Cargo.objects.filter(status=Cargo.Status.PICKED_UP).count()
        assert 0 <= after - before <= 2


def test_pickup_prefers_older_cargo(state, ivan):
    old = ready_cargo(ivan, REAL_NOW - timedelta(days=5), "OLD")
    new = ready_cargo(ivan, REAL_NOW - timedelta(minutes=10), "NEW")
    rng = random.Random(11)
    picks = {old.pk: 0, new.pk: 0}
    for _ in range(300):
        [chosen] = engine.pick_weighted([old, new], REAL_NOW, rng, 1)
        picks[chosen.pk] += 1
    assert picks[old.pk] > 5 * picks[new.pk]
    assert picks[new.pk] > 0


def test_pick_weighted_returns_distinct_cargo(state, ivan):
    cargos = [ready_cargo(ivan, REAL_NOW - timedelta(days=d), f"D{d}") for d in (1, 2, 3)]
    chosen = engine.pick_weighted(cargos, REAL_NOW, random.Random(2), 2)
    assert len({c.pk for c in chosen}) == 2


def test_pickup_skips_cargo_ready_after_the_sim_clock(state, ivan):
    cargo = ready_cargo(ivan, REAL_NOW + timedelta(hours=1), "FUTURE")
    rng = random.Random(1)
    for _ in range(10):
        engine.pickup_cargo(make_ctx(state, rng))
    cargo.refresh_from_db()
    assert cargo.status == Cargo.Status.READY


def test_autopilot_user_is_reused(db):
    first = engine.autopilot_user()
    assert engine.autopilot_user() == first
    assert get_user_model().objects.filter(username="autopilot").count() == 1


# ---------- daily order ----------


def test_builds_todays_order_after_0830_when_dates_match(state, rng, ivan, pinned_now):
    today = timezone.localdate()
    ready_cargo(ivan, local(today, 8) - timedelta(days=4), "WAIT")
    ctx = make_ctx(state, rng, now=local(today, 8, 31), real_now=pinned_now)
    engine.build_order(ctx)
    order = DailyOrder.objects.get(date=today)
    assert order.lines.count() == 1
    [event] = events(Kind.ORDER_BUILT)
    assert "1 строка" in event.message
    assert ctx.counts["orders_built"] == 1
    assert state.order_checked_on == today
    engine.build_order(ctx)  # once per sim day
    assert len(events()) == 1


def test_no_order_before_0830(state, rng, pinned_now):
    today = timezone.localdate()
    engine.build_order(make_ctx(state, rng, now=local(today, 8, 29), real_now=pinned_now))
    assert not DailyOrder.objects.exists()
    assert events() == []
    assert state.order_checked_on is None


def test_order_skipped_when_sim_date_is_not_today(state, rng, pinned_now):
    tomorrow = timezone.localdate() + timedelta(days=1)
    ctx = make_ctx(state, rng, now=local(tomorrow, 9), real_now=pinned_now)
    engine.build_order(ctx)
    assert not DailyOrder.objects.exists()
    [event] = events(Kind.SKIPPED)
    assert "пропущен" in event.message
    assert ctx.counts["orders_built"] == 0
    assert state.order_checked_on == tomorrow


def test_existing_order_is_not_rebuilt(state, rng, pinned_now):
    today = timezone.localdate()
    orders.build_daily_order(today)
    ctx = make_ctx(state, rng, now=local(today, 9), real_now=pinned_now)
    engine.build_order(ctx)
    assert ctx.counts["orders_built"] == 0
    assert events(Kind.ORDER_BUILT) == []


# ---------- errors ----------


def test_invalid_transition_is_logged_not_raised(state, rng, ivan, monkeypatch):
    truck = loading_truck(capacity=10)
    fill(truck, ivan, 10)

    def refuse(*args, **kwargs):
        raise services.InvalidTransition("Машину нельзя отправить")

    monkeypatch.setattr(services, "truck_depart", refuse)
    ctx = make_ctx(state, rng)
    engine.depart_trucks(ctx)
    truck.refresh_from_db()
    assert truck.status == Truck.Status.LOADING
    assert truck.eta is None  # the eta write rolled back with the failed action
    [event] = events(Kind.ERROR)
    assert "Машину нельзя отправить" in event.message
    assert ctx.counts["departed"] == 0


def test_order_error_is_logged_not_raised(state, rng, monkeypatch, pinned_now):
    def refuse(*args, **kwargs):
        raise orders.OrderError("Ордер только для чтения")

    monkeypatch.setattr(orders, "build_daily_order", refuse)
    today = timezone.localdate()
    engine.build_order(make_ctx(state, rng, now=local(today, 9), real_now=pinned_now))
    [event] = events(Kind.ERROR)
    assert "только для чтения" in event.message


def test_unexpected_error_in_one_rule_does_not_stop_the_tick(state, rng, monkeypatch):
    def boom(ctx):
        raise RuntimeError("сбой")

    monkeypatch.setattr(engine, "arrive_trucks", boom)
    engine.tick(state, REAL_NOW, rng)
    assert Truck.objects.exists()  # later rules still ran
    assert any("сбой" in e.message for e in events(Kind.ERROR))


# ---------- tick ----------


def test_tick_runs_the_rules_and_saves_counters(state, rng):
    state.speed = 60
    state.last_tick_at = REAL_NOW - timedelta(seconds=5)
    state.save()
    created = engine.tick(state, REAL_NOW, rng)
    saved = AutopilotState.objects.get(pk=state.pk)
    assert saved.sim_clock == REAL_NOW + timedelta(minutes=5)
    assert saved.last_tick_at == REAL_NOW
    assert saved.trucks_created == 1
    assert saved.cargo_added == Cargo.objects.count() >= 1
    assert created and all(e.sim_at == saved.sim_clock for e in created)
    assert all(e.created_at == REAL_NOW for e in created)
    assert state.trucks_created == 1  # the caller's instance is refreshed


def test_counters_accumulate_over_ticks(state):
    rng = random.Random(9)
    for i in range(40):
        engine.tick(state, REAL_NOW + timedelta(seconds=5 * i), rng)
    saved = AutopilotState.objects.get(pk=state.pk)
    assert saved.cargo_added == Cargo.objects.count()
    assert saved.trucks_created == Truck.objects.count()
    assert saved.departed == Truck.objects.exclude(status=Truck.Status.LOADING).count()


def test_tick_prunes_the_journal_to_500(state, rng):
    AutopilotEvent.objects.bulk_create(
        AutopilotEvent(sim_at=REAL_NOW, kind=Kind.SKIPPED, message=f"старое {i}")
        for i in range(520)
    )
    engine.tick(state, REAL_NOW, rng)
    assert AutopilotEvent.objects.count() == engine.KEEP_EVENTS == 500
    assert not AutopilotEvent.objects.filter(message="старое 0").exists()
    assert AutopilotEvent.objects.filter(kind=Kind.TRUCK_CREATED).exists()


def test_first_tick_creates_plausible_cargo(state):
    engine.tick(state, REAL_NOW, random.Random(42))
    first = [e.message for e in events()]
    assert first
    places = list(Cargo.objects.order_by("id").values_list("places", "weight_kg"))
    assert all(p >= 1 and w > Decimal("0") for p, w in places)
    assert Client.objects.count() >= 1


# ---------- exclusive tick ----------


def test_second_tick_with_the_same_snapshot_is_skipped(state, rng):
    first = AutopilotState.objects.get(pk=state.pk)
    second = AutopilotState.objects.get(pk=state.pk)  # same last_tick_at snapshot
    assert engine.tick(first, REAL_NOW, rng) != engine.SKIPPED
    trucks, events_before = Truck.objects.count(), AutopilotEvent.objects.count()
    clock = AutopilotState.objects.get(pk=state.pk).sim_clock
    assert engine.tick(second, REAL_NOW, rng) == engine.SKIPPED
    assert Truck.objects.count() == trucks
    assert AutopilotEvent.objects.count() == events_before
    assert AutopilotState.objects.get(pk=state.pk).sim_clock == clock


def test_tick_within_a_second_of_the_last_is_skipped(state, rng):
    state.last_tick_at = REAL_NOW - timedelta(milliseconds=500)
    state.save()
    assert engine.tick(state, REAL_NOW, rng) == engine.SKIPPED
    assert not Truck.objects.exists()


def test_tick_a_second_later_runs(state, rng):
    state.last_tick_at = REAL_NOW - timedelta(seconds=1)
    state.save()
    assert engine.tick(state, REAL_NOW, rng) != engine.SKIPPED
    assert Truck.objects.exists()


def test_tick_that_loses_the_conditional_update_is_skipped(state, rng, monkeypatch):
    """SQLite ignores select_for_update: the claim also needs last_tick_at unchanged."""
    real_advance = engine.advance_clock

    def rival_wins_first(locked, real_now, elapsed=None):
        AutopilotState.objects.filter(pk=locked.pk).update(last_tick_at=real_now)
        return real_advance(locked, real_now, elapsed)

    monkeypatch.setattr(engine, "advance_clock", rival_wins_first)
    assert engine.tick(state, REAL_NOW, rng) == engine.SKIPPED
    assert not Truck.objects.exists()
    assert AutopilotState.objects.get(pk=state.pk).sim_clock == REAL_NOW


def test_resync_clock_puts_sim_time_back_to_real_now(state):
    state.sim_clock = REAL_NOW + timedelta(hours=7)
    state.trucks_created = 4
    state.save()
    engine.resync_clock(state, REAL_NOW)
    saved = AutopilotState.objects.get(pk=state.pk)
    assert saved.sim_clock == REAL_NOW
    assert saved.last_tick_at == REAL_NOW
    assert saved.trucks_created == 4


def test_explicit_steps_back_to_back_both_run(state):
    """«Один шаг» and ``--steps`` are deliberate ticks: no 1 s gap needed between them."""
    rng = random.Random(4)
    step = engine.DEFAULT_STEP
    assert engine.tick(state, REAL_NOW, rng, elapsed=step) != engine.SKIPPED
    assert engine.tick(state, REAL_NOW, rng, elapsed=step) != engine.SKIPPED
    assert AutopilotState.objects.get(pk=state.pk).sim_clock == REAL_NOW + 2 * step


def test_explicit_step_with_a_stale_snapshot_is_skipped(state, rng, monkeypatch):
    """Two «Один шаг» at once: the one that read the old row loses the conditional write."""
    stale = AutopilotState.objects.get(pk=state.pk)
    engine.tick(state, REAL_NOW, rng, elapsed=engine.DEFAULT_STEP)

    class StaleRead:  # what the racing tick read before the winner wrote
        def get(self, **kwargs):
            return stale

    monkeypatch.setattr(AutopilotState.objects, "select_for_update", lambda: StaleRead())
    assert engine.tick(stale, REAL_NOW, rng, elapsed=engine.DEFAULT_STEP) == engine.SKIPPED
    assert AutopilotState.objects.get(pk=state.pk).sim_clock == REAL_NOW + engine.DEFAULT_STEP

"""Run the «Автопилот» simulator: ``autopilot [--loop] [--interval 5] [--steps N] [--speed]``.

Without ``--loop`` it runs ``--steps`` ticks (default 1) right away, each counted as one
``--interval`` of real time. With ``--loop`` it ticks every ``--interval`` seconds while
the «running» flag is on (set from the panel) and waits while it is paused.
"""

import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from autopilot import engine
from autopilot.models import AutopilotState
from panel.permissions import autopilot_enabled


class Command(BaseCommand):
    help = "Автопилот: симулятор рейсов и грузов для демо. Меняет реальные данные."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Работать в фоне до Ctrl+C.")
        parser.add_argument("--interval", type=float, default=5, help="Секунд между шагами.")
        parser.add_argument("--steps", type=int, default=None, help="Сколько шагов сделать.")
        parser.add_argument(
            "--speed", type=int, choices=AutopilotState.SPEEDS, help="Скорость симуляции."
        )

    def handle(self, *args, loop=False, interval=5, steps=None, speed=None, **options):
        if not autopilot_enabled():
            raise CommandError("Автопилот выключен (AUTOPILOT_ENABLED=false)")
        if interval <= 0:
            raise CommandError("--interval должен быть больше нуля")
        state = AutopilotState.load()
        if speed:
            state.speed = speed
            state.save(update_fields=["speed"])
        rng = random.Random()
        if loop:
            self._loop(state, rng, interval)
            return
        step = timedelta(seconds=interval)
        steps = 1 if steps is None else steps
        for _ in range(steps):
            self._report(engine.tick(state, timezone.now(), rng, elapsed=step))
        self.stdout.write(f"Шагов: {steps}. Время симуляции: {self._clock(state)}")

    def _loop(self, state, rng, interval):
        self.stdout.write(f"Автопилот в фоне, шаг {interval:g} с. Остановить: Ctrl+C")
        paused_said = False
        try:
            while True:
                state.refresh_from_db()
                if state.running:
                    paused_said = False
                    self._report(engine.tick(state, timezone.now(), rng))
                elif not paused_said:
                    self.stdout.write("Автопилот на паузе: нажмите «Запустить» в панели")
                    paused_said = True
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Остановлено.")

    def _report(self, events):
        if events == engine.SKIPPED:
            self._say("Шаг пропущен: его уже сделал другой процесс")
            return
        for event in events:
            self._say(f"{self._clock_of(event.sim_at)} · {event.message}")

    def _say(self, text):
        """Write a line; a console without «→» (cp1251) gets «?» instead of a crash."""
        try:
            self.stdout.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self.stdout._out, "encoding", None) or "ascii"
            self.stdout.write(text.encode(encoding, "replace").decode(encoding))

    @staticmethod
    def _clock_of(value):
        return timezone.localtime(value).strftime("%d.%m %H:%M")

    def _clock(self, state):
        return self._clock_of(state.sim_clock) if state.sim_clock else "—"

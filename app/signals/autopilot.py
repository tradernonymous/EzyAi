import contextlib
import time
from datetime import datetime, timezone

from .. import constants
from . import engine as signal_engine


class AutoPilot:
    def __init__(self, hub, chat_id, style, mode):
        self.hub = hub
        self.chat_id = chat_id
        self.style = style
        self.mode = mode
        self.recent = []
        self.last_run = 0.0
        self.last_signal = None

    def _utc_today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def run(self, daily_counters, lock=None):
        """Scan one random pair. daily_counters is shared with the Service
        and serialized to disk from another thread, so every mutation of it
        happens under `lock` when one is supplied."""
        now = time.time()
        # Mark the attempt first: a failing feed must back off to the normal
        # cadence instead of retrying on every tick.
        self.last_run = now
        guard = lock if lock is not None else contextlib.nullcontext()
        profile = constants.MODE_PROFILE[self.mode]
        # quota is per chat: one busy user must not consume another's limit
        key = f"{self.chat_id}:{self.style}:{self.mode}"
        today = self._utc_today()
        with guard:
            counter = dict(daily_counters.get(key) or {})
        if counter.get("date") != today:
            counter = {"date": today, "count": 0}
        if counter["count"] >= profile["daily_limit"]:
            return None, "daily signal limit reached"

        pair = self.hub.random_symbol(exclude=self.recent)
        analysis, signal = signal_engine.quick_analyze(pair, self.style, self.mode, self.hub)
        self.recent.append(pair)
        self.recent = self.recent[-6:]

        if not signal:
            return None, None

        counter["count"] += 1
        with guard:
            daily_counters[key] = counter
        self.last_signal = signal
        return signal, None

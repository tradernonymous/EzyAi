import random
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
        self.rng = random.Random()
        self.recent = []
        self.last_run = 0.0
        self.last_signal = None

    def _utc_today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def run(self, daily_counters):
        now = time.time()
        profile = constants.MODE_PROFILE[self.mode]
        key = f"{self.style}:{self.mode}"
        today = self._utc_today()
        counter = daily_counters.get(key, {})
        if counter.get("date") != today:
            counter = {"date": today, "count": 0}
        if counter["count"] >= profile["daily_limit"]:
            return None, "daily signal limit reached"

        pair = self.hub.random_symbol(exclude=self.recent)
        analysis, signal = signal_engine.quick_analyze(pair, self.style, self.mode, self.hub)
        self.recent.append(pair)
        self.recent = self.recent[-6:]
        self.last_run = now

        if not signal:
            return None, None

        counter["count"] += 1
        daily_counters[key] = counter
        self.last_signal = signal
        return signal, None
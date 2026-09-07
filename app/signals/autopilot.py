import contextlib
import random
import time
from datetime import datetime, timezone

from .. import constants
from . import engine as signal_engine


class AutoPilot:
    def __init__(self, hub, chat_id, style, mode, batch=None):
        self.hub = hub
        self.chat_id = chat_id
        self.style = style
        self.mode = mode
        self.batch = batch or constants.SCAN_BATCH
        self.recent = []
        self.last_run = 0.0
        self.last_signal = None
        # Phase-2 ranked scanner: every chat gets a deterministic shuffle so
        # simultaneous rows fan out across the universe instead of hammering
        # the same symbols; scanning advances a round-robin cursor.
        rng = random.Random(chat_id)
        self._order = list(constants.ALL_UNIVERSE)
        rng.shuffle(self._order)
        self._cursor = 0

    def _utc_today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _slice(self, n):
        """Next n pairs to scan, skipping anything already analysed recently.
        Distinct from the legacy random picker: no symbol is repeated across
        consecutive runs for the same chat, and short feeds never stall the
        full scan."""
        order = self._order
        out = []
        i = self._cursor
        seen = set(self.recent)
        while len(out) < n and len(out) < len(order):
            cand = order[i % len(order)]
            i += 1
            if cand in seen:
                continue
            out.append(cand)
        self._cursor = i % len(order)
        return out

    def run(self, daily_counters, lock=None):
        """Scan SCAN_BATCH pairs, keep the single best candidate. Scanned
        pairs are remembered so the cursor genuinely rotates; a raw always
        beats a qualified signal with a lower confidence, and no pair is
        emitted when nothing clears the threshold -- honest silence."""
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

        best = None
        for pair in self._slice(self.batch):
            try:
                analysis, signal = signal_engine.quick_analyze(
                    pair, self.style, self.mode, self.hub)
            except Exception:
                # one bad feed (regexp flag, empty slice, rate-limit blip)
                # never kills the whole scan
                continue
            self.recent.append(pair)
            self.recent = self.recent[-6:]
            if signal and (best is None or signal["confidence"] > best["confidence"]):
                best = signal

        if best is None:
            return None, None

        counter["count"] += 1
        with guard:
            daily_counters[key] = counter
        self.last_signal = best
        return best, None
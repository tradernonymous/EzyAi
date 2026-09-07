import contextlib
import json
import logging
import random
import time
from datetime import datetime, timezone

from .. import constants
from ..data import quality
from ..data.provider import DataHub
from . import engine as signal_engine

logger = logging.getLogger(__name__)


def lifecycle_block(store, chat_id, pair, style, signal):
    """Phase-4 cooldown gate shared by watch ticks and autopilot.

    Scans the chat's recent history for (pair, style):
      * an OPEN signal (any direction) suppresses new ones -- one position
        at a time per symbol,
      * after it resolves, price must travel at half an ATR away from the
        last entry before an identical re-entry (zone re-arm), so a setup
        that never broke down is not re-announced at the same price,
      * shadow captures are informational and never block.

    store None (test seams, broken DB) passes everything. Returns
    (blocked, reason)."""
    if store is None:
        return False, None
    try:
        rows = store.query(
            "SELECT * FROM signals WHERE chat_id=? AND pair=? AND style=? "
            "ORDER BY created_at DESC, id DESC LIMIT 10",
            (int(chat_id), str(pair).upper(), style))
    except Exception as exc:
        logger.warning("lifecycle gate query failed: %s: %s",
                       type(exc).__name__, exc)
        return False, None
    if not rows:
        return False, None
    for r in rows:
        if r.get("status") == "open":
            return True, (f"prior {style} signal for {pair} is still open "
                          f"(entry {r.get('entry')})")
    last = rows[0]
    if last.get("status") == "shadow":
        return False, None
    atr = _atr_of(last)
    cur = (signal.get("component_scores") or {}).get("close")
    if atr and cur is not None and \
            abs(float(cur) - float(last["entry"])) <= 0.5 * atr:
        return True, (f"price still inside the last {style} zone for {pair} "
                      f"(entry {last['entry']:.6g} \u00b1 "
                      f"{0.5 * atr:.4g}) \u2014 wait for the re-arm")
    return False, None


def _atr_of(row):
    try:
        data = json.loads(row.get("component_scores") or "{}")
    except Exception:
        return None
    atr = data.get("atr")
    return float(atr) if atr else None


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
        self._order = [p for p in constants.ALL_UNIVERSE
                       if quality.style_allowed(p, style)]
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

    def run(self, daily_counters, lock=None, store=None):
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
        best_analysis = None
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
            # 3C emission gate: scalping requires a real-time feed (crypto,
            # or FX/metals on OANDA); a feed that fell through to synthetic
            # data must never emit either. Only enforced on DataHub: other
            # hubs are test seams and signal logic must run against them
            # regardless of provenance.
            if isinstance(self.hub, DataHub):
                ok, why = quality.may_emit(
                    pair, self.style, source=analysis.get("data_source"),
                    data_mode=analysis.get("data_mode"))
                if not ok:
                    logger.warning("autopilot suppressed %s %s: %s", pair,
                                   self.style, why)
                    continue
            if signal and (best is None or signal["confidence"] > best["confidence"]):
                best, best_analysis = signal, analysis

        if best is None:
            return None, None

        # 4C cooldown runs BEFORE the daily cap is spent: a signal that is
        # suppressed by an open position or an un-re-armed zone never
        # consumes the user's allowance.
        blocked, why = lifecycle_block(store, self.chat_id, best["pair"],
                                       best["style"], best)
        if blocked:
            logger.info("autopilot cooldown %s %s: %s", best["pair"],
                        best["style"], why)
            return None, "signal cooldown"

        counter["count"] += 1
        with guard:
            daily_counters[key] = counter
        self.last_signal = best
        return best, None
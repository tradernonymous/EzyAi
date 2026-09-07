"""First-touch resolver for open signals (Phase 1, brief 1.5-1.9).

Mirrors the offline backtest's conservative `_outcome` walk, extended to
two take profits and a time-based expiry per style:

  * bars strictly after the signal timestamp (no lookahead into the bar
    the signal was created on; same rule as scripts/backtest.py),
  * SL and a TP printing in the SAME bar resolve to SL and are flagged
    `same_candle_ambig=1` (brief 1.8),
  * TP1 booked first locks +rr; the ladder may still upgrade to TP2, but
    a later SL never erodes the booked TP1,
  * TP2 -> +2*rr,
  * no touch within the style window and the fetched data actually
    covering that window -> 'expired' at the latest close, price-based R.

A fetch failure or short candle history always leaves the signal 'open'
so a bogus datapoint can never fabricate an outcome.
"""
import asyncio
import logging
import time

from .. import constants

logger = logging.getLogger(__name__)

# How long a style may run before it is expired (brief 1.6).
EXPIRY_S = {"scalping": 6 * 3600, "intraday": 36 * 3600, "swing": 14 * 86400}
# Covers the longest window on the finest timeframe (swing daily: 14 bars).
FETCH_LIMIT = 500
# Minimum gap between resolve attempts per signal (tick runs every 30 s).
MIN_INTERVAL = 60.0


def _bam_sides(c):
    """The side a traded quote actually prints on: longs fill on the bid,
    shorts on the ask (Phase 1D). Returns the per-side dict when the candle
    carries OANDA bid/ask data, else None (mid-only feeds unchanged)."""
    bid, ask = c.get("bid"), c.get("ask")
    if bid and ask and bid.get("h") is not None and ask.get("h") is not None:
        return {"long": bid, "short": ask}
    return None


class Resolver:
    def __init__(self, store, hub):
        self.store = store
        self.hub = hub
        self.last_attempt = {}  # signal id -> last attempt epoch

    async def run(self):
        open_sigs = self.store.open_signals()
        if not open_sigs:
            return
        now = time.time()
        due = []
        for sig in open_sigs:
            if now - self.last_attempt.get(sig["id"], 0.0) < MIN_INTERVAL:
                continue
            self.last_attempt[sig["id"]] = now
            due.append(sig)
        if not due:
            return
        sem = asyncio.Semaphore(4)

        async def one(sig):
            async with sem:
                try:
                    await asyncio.to_thread(self._resolve_one, sig, now)
                except Exception as exc:  # one bad signal never stops the pass
                    logger.warning("resolve failed id=%s %s: %s: %s",
                                   sig["id"], sig["pair"],
                                   type(exc).__name__, exc)

        await asyncio.gather(*(one(s) for s in due))

    def _resolve_one(self, sig, now):
        style = sig["style"]
        tf = constants.STYLE_PROFILE[style]["base_tf"]
        try:
            candles, _mode = self.hub.fetch_klines_ex(sig["pair"], tf,
                                                      FETCH_LIMIT)
        except Exception as exc:
            logger.warning("resolve fetch failed %s %s: %s",
                           sig["pair"], tf, exc)
            return
        if not candles:
            return
        outcome = _walk(sig, candles)
        if outcome is not None:
            status, exit_price, r, ambig = outcome
            self.store.mark_resolved(sig["id"], status, exit_price, r, ambig)
            return
        # No first touch: expire on the style window once only if the
        # fetched history actually reaches the expiry point, so a stale or
        # truncated feed can never fabricate an 'expired' resolution.
        if now - sig["created_at"] < EXPIRY_S[style]:
            return
        last = candles[-1]
        need_ms = (sig["created_at"] + EXPIRY_S[style]
                   - constants.INTERVALS[tf]) * 1000
        if last["ts"] < need_ms:
            return
        sides = _bam_sides(last)
        exit_price = (sides[side]["c"] if sides and sides[side]["c"] is not None
                      else last["close"])
        risk = abs(sig["entry"] - sig["stop_loss"])
        if risk <= 0:
            return
        r = ((exit_price - sig["entry"]) / risk
             if sig["direction"] == "long"
             else (sig["entry"] - exit_price) / risk)
        self.store.mark_resolved(sig["id"], "expired", exit_price, r)


def _walk(sig, candles):
    """Return (status, exit_price, r_multiple, ambig) or None while open."""
    side = sig["direction"]
    entry = float(sig["entry"])
    sl = float(sig["stop_loss"])
    tp1 = float(sig["tp1"])
    tp2 = float(sig["tp2"])
    rr = float(sig["rr_target"])
    t0_ms = int(float(sig["created_at"]) * 1000)
    bars = [c for c in candles if c["ts"] > t0_ms]
    if not bars:
        return None

    def touched(c):
        sides = _bam_sides(c)
        if sides:
            h, lo = sides[side]["h"], sides[side]["l"]
        else:
            h, lo = c["high"], c["low"]
        if side == "long":
            return lo <= sl, h >= tp1, h >= tp2
        return h >= sl, lo <= tp1, lo <= tp2

    for j, c in enumerate(bars):
        hit_sl, hit_t1, hit_t2 = touched(c)
        if hit_sl and (hit_t1 or hit_t2):
            return "sl", sl, -1.0, 1  # same-bar ambiguity -> SL, flagged
        if hit_sl:
            return "sl", sl, -1.0, 0
        if hit_t2:
            return "tp2", tp2, 2.0 * rr, 0
        if hit_t1:
            # TP1 locked: only TP2 can improve it later.
            for c2 in bars[j + 1:]:
                _sl, _t1, _t2 = touched(c2)
                if _t2:
                    return "tp2", tp2, 2.0 * rr, 0
            return "tp1", tp1, rr, 0
    return None
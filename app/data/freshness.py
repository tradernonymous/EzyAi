"""Freshness gates (3B): before any analysis a candle set must be recent,
gap-free and free of implausible prints. On any failure the pair is skipped
and the reason logged -- degraded data is never analysed silently.

Tolerances are deliberately permissive: the gate exists to catch a broken
feed or a weekend-quoted Yahoo series, not to reject a legitimate quiet
bar. Daily bars close at 00:00 UTC so they are allowed to be ~3 days old
across a holiday long weekend.
"""

from __future__ import annotations

from typing import Optional

# Maximum age of the last closed bar, in multiples of the timeframe.
STALE_MULT = {
    "1m": 4.0, "5m": 4.0, "15m": 4.0, "30m": 4.0,
    "1h": 4.0, "4h": 4.0, "1d": 3.5,
}

# A gap between consecutive bars longer than this (in timeframe units) means
# the series has holes the indicators will read as a trend.
GAP_MULT = {
    "1m": 3.0, "5m": 3.0, "15m": 3.0, "30m": 3.0,
    "1h": 3.0, "4h": 3.0, "1d": 9.0,  # weekends/hr holidays are normal on daily
}

# A single bar whose range exceeds this multiple of the prior ATR is a bad
# print: reject on the spot rather than letting ADX/ATR chase it.
SPIKE_ATR = 8.0

# Minimum number of bars to have any ATR baseline for the spike check.
SPIKE_MIN = 30


def _interval_s(tf):
    """Seconds per timeframe unit. Returns None for unknown TFs so the gate
    degrades to a no-op rather than a crash."""
    return {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400}.get(tf)


def check(candles, tf, now=None):
    """Return (ok, None) or (False, reason) for a candle series + timeframe.

    `now` is injectable for tests; defaults to wall clock (ms)."""
    if not candles:
        return False, "empty candle series"
    unit = _interval_s(tf)
    if unit is None:
        return True, None  # unknown timeframe: nothing to check against
    now = now * 1000.0 if now and now < 1e11 else (now or _now_ms())
    last = candles[-1]
    age = (now - last["ts"]) / 1000.0
    if age > unit * STALE_MULT.get(tf, 4.0):
        return False, (f"{tf} last bar {age / 60.0:.0f}m old > "
                       f"{unit * STALE_MULT.get(tf, 4.0) / 60.0:.0f}m tolerance")

    max_gap = unit * GAP_MULT.get(tf, 3.0)
    window = candles[-min(len(candles), 60):]
    prev = window[0]
    for cur in window[1:]:
        d = (cur["ts"] - prev["ts"]) / 1000.0
        if 0 < d <= max_gap:
            prev = cur
            continue
        if d <= 0:
            return False, f"non-monotonic timestamps at {cur['ts']}"
        return False, (f"{tf} gap of {d / unit:.1f}x the bar size before "
                       f"{cur['ts']}")
    if len(window) < 3:
        return True, None
    last_range = candles[-1]["high"] - candles[-1]["low"]
    if last_range <= 0:
        return True, None
    closes = [c["close"] for c in candles[-1 - min(len(candles) - 1, 200): -1]]
    if len(closes) < SPIKE_MIN:
        return True, None
    # ATR via Wilder's smoother over the pre-spike closes' true ranges.
    prev_bars = candles[-1 - min(len(candles) - 1, 200): -1]
    trs = []
    for i, c in enumerate(prev_bars):
        hi, lo = c["high"], c["low"]
        pc = prev_bars[i - 1]["close"] if i else c["close"]
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    atr = sum(trs) / len(trs)
    if atr > 0 and last_range / atr > SPIKE_ATR:
        return False, (f"implausible print: last {tf} range "
                       f"{last_range / atr:.1f}x prior ATR")
    return True, None


def _now_ms():
    import time
    return time.time() * 1000.0
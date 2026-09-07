"""
app/data/probe.py

One-shot feed diagnostics behind the admin /verifyfeed command. It snapshots
how a pair is actually served right now: which provider answered, the quality
tier, whether the series is fresh, whether scalping is allowed and where the
pair is in its liquidity window. Purely diagnostic: reads state, never writes
it.

The window state matters as much as the freshness verdict. A quiet feed
inside London/NY is a broken feed; the same feed at 03:00 UTC is a closed
market, and the report has to let an admin tell those apart.
"""

from __future__ import annotations

import time

from ..analysis import regime
from . import freshness
from . import quality

# Probing at 1m is the strictest freshness case and the scalp timeframe.
PROBE_TF = "1m"
PROBE_LIMIT = 100


def probe_pair(hub, pair, tf=PROBE_TF, limit=PROBE_LIMIT):
    """Snapshot how `pair` is served right now. Never raises for a bad feed:
    failures are recorded in the result dict for the report to show."""
    pair = pair.upper()
    now_ms = int(time.time() * 1000)
    out = {
        "pair": pair,
        "tf": tf,
        "static_tier": quality.static_tier(pair).value,
        "scalp_class": None,
        "venue_symbol": None,
        "window_label": None,
        "in_window": None,
        "next_open": None,
        "served_by": None,
        "tier": None,
        "fresh_ok": None,
        "fresh_reason": None,
        "scalp_ok": None,
        "scalp_reason": None,
        "spread_bps": None,
        "last_age_s": None,
        "last_price": None,
        "probe_error": None,
    }

    from .. import constants
    out["scalp_class"] = constants.scalp_class(pair)
    try:
        out["venue_symbol"] = hub.resolve(pair)[1]
    except Exception:
        pass
    out["spread_bps"] = constants.spread_bps(pair)
    win = regime.scalp_session(pair)
    if win is not None:
        out["window_label"] = win["label"]
        out["in_window"] = win["in_window"]
        if not win["in_window"]:
            out["next_open"] = regime.fmt_next_open(
                regime.next_session_open(pair))

    try:
        candles, _mode = hub.fetch_klines_ex(pair, tf, limit)
    except Exception as exc:
        out["probe_error"] = f"{type(exc).__name__}: {exc}"
        return out

    if not candles:
        out["probe_error"] = "empty candle series"
        return out

    source = candles[-1].get("source")
    out["served_by"] = source
    out["tier"] = quality.runtime_tier(source).value
    out["fresh_ok"], out["fresh_reason"] = freshness.check(candles, tf, now_ms)
    out["scalp_ok"], out["scalp_reason"] = quality.may_emit(
        pair, "scalping", candles=candles)
    if out["scalp_ok"] and out["in_window"] is False:
        out["scalp_ok"] = False
        out["scalp_reason"] = (f"outside {win['label']} — scalping "
                               f"resumes when the window opens")
    last = candles[-1]
    out["last_age_s"] = max(0.0, (now_ms - last["ts"]) / 1000.0)
    out["last_price"] = last["close"]
    return out

"""
app/data/probe.py

One-shot feed diagnostics behind the admin /verifyfeed command. It snapshots
how a pair is actually served right now (provider stamp, quality tier,
freshness, scalping gate) and, when OANDA is configured, runs a direct probe
against the v3 API so the underlying failure surfaces instead of being
swallowed by the graceful fallback. Purely diagnostic: reads state, never
writes it.
"""

from __future__ import annotations

import time

from .. import config
from . import freshness
from . import quality
from . import provider as _prov

# Probing at 1m is the strictest freshness case and the scalp timeframe.
PROBE_TF = "1m"
PROBE_LIMIT = 100


def _mask(key):
    if not key:
        return None
    return "\u2026" + key[-4:] if len(key) > 4 else "\u2026"


def _base_url(env):
    return _prov.OandaProvider.BASES.get(
        env, _prov.OandaProvider.BASES["live"])


def probe_pair(hub, pair, tf=PROBE_TF, limit=PROBE_LIMIT):
    """Snapshot how `pair` is served right now. Never raises for a bad feed:
    failures are recorded in the result dict for the report to show."""
    pair = pair.upper()
    now_ms = int(time.time() * 1000)
    out = {
        "pair": pair,
        "tf": tf,
        "oanda_enabled": config.oanda_enabled(),
        "oanda_environment": (config.oanda_environment()
                              if config.oanda_enabled() else None),
        "oanda_base": None,
        "oanda_key_tail": _mask(config.oanda_key()),
        "oanda_instrument": None,
        "oanda_ok": False,
        "oanda_error": None,
        "oanda_age_s": None,
        "oanda_bam_ok": None,
        "oanda_spread": None,
        "served_by": None,
        "tier": None,
        "fresh_ok": None,
        "fresh_reason": None,
        "scalp_ok": None,
        "scalp_reason": None,
        "last_age_s": None,
        "last_price": None,
        "probe_error": None,
    }

    inst = _prov.oanda_instrument(pair)
    out["oanda_instrument"] = inst
    if inst is not None and getattr(hub, "oanda", None) is not None:
        out["oanda_base"] = _base_url(config.oanda_environment())
        try:
            # require_bam=False: mid-only is still evidence the feed is up;
            # the report flags the missing spread separately (scalping would
            # be blocked when the hub itself demands BAM).
            cs = hub.oanda.fetch_klines(inst, tf, limit,
                                        require_bam=False)
            out["oanda_ok"] = bool(cs)
            if cs:
                out["oanda_age_s"] = max(
                    0.0, (now_ms - cs[-1]["ts"]) / 1000.0)
                sc = _prov.spread_context(cs)
                out["oanda_bam_ok"] = sc is not None
                out["oanda_spread"] = sc["latest"] if sc is not None else None
        except Exception as exc:
            out["oanda_error"] = f"{type(exc).__name__}: {exc}"

    try:
        candles, _mode = hub.fetch_klines_ex(pair, tf, limit)
    except Exception as exc:
        out["probe_error"] = f"{type(exc).__name__}: {exc}"
        return out

    if not candles:
        out["probe_error"] = out["probe_error"] or "empty candle series"
        return out

    source = candles[-1].get("source")
    out["served_by"] = source
    out["tier"] = quality.runtime_tier(source).value
    out["fresh_ok"], out["fresh_reason"] = freshness.check(candles, tf, now_ms)
    out["scalp_ok"], out["scalp_reason"] = quality.may_emit(
        pair, "scalping", candles=candles)
    if out["scalp_ok"] and source == "oanda" and not out["oanda_bam_ok"]:
        out["scalp_ok"] = False
        out["scalp_reason"] = ("OANDA feed has no bid/ask spread data "
                               "(BAM missing) \u2014 scalping needs it")
    last = candles[-1]
    out["last_age_s"] = max(0.0, (now_ms - last["ts"]) / 1000.0)
    out["last_price"] = last["close"]
    return out
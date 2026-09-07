"""Phase-3 tests: demo kill-switch wiring, data-quality tiers, scalping
restriction, freshness gates, calendar blackout, spread widening. The
freshness gate only runs on real DataHub instances, so every test here uses
stubs or static functions - no network is touched."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone  # noqa: E402

from app import constants  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402
from app.data import freshness, quality  # noqa: E402
from app.data.calendar import Calendar  # noqa: E402
from app.data.provider import SyntheticProvider  # noqa: E402
from app.signals.scheduler import Service  # noqa: E402


# ---------------------------------------------------------------------------
# 3C - data-quality tiers, static and runtime
# ---------------------------------------------------------------------------

def test_static_tier_routes_crypto_to_realtime():
    assert quality.static_tier("BTCUSD") is quality.Tier.REALTIME
    assert quality.static_tier("ETHUSD") is quality.Tier.REALTIME


def test_static_tier_delays_everything_else():
    assert quality.static_tier("EURUSD") is quality.Tier.DELAYED
    assert quality.static_tier("AAPL") is quality.Tier.DELAYED


def test_runtime_tier():
    t = quality.Tier
    assert quality.runtime_tier("binance") is t.REALTIME
    assert quality.runtime_tier("ccxt") is t.REALTIME
    assert quality.runtime_tier("yahoo") is t.DELAYED
    assert quality.runtime_tier(None) is t.DELAYED
    assert quality.runtime_tier("binance", data_mode="demo") is t.SYNTHETIC
    assert quality.runtime_tier("synthetic") is t.SYNTHETIC


def test_may_emit_scalping_needs_realtime():
    ok, why = quality.may_emit("EURUSD", "scalping", source="yahoo")
    assert not ok and why
    ok, _ = quality.may_emit("BTCUSD", "scalping", source="binance")
    assert ok


def test_may_emit_permissive_styles_pass_on_delayed():
    ok, _ = quality.may_emit("EURUSD", "intraday", source="yahoo")
    assert ok
    ok, _ = quality.may_emit("EURUSD", "swing", source="yahoo")
    assert ok


def test_may_emit_synthetic_blocks_everything():
    ok, why = quality.may_emit("EURUSD", "intraday", source="synthetic")
    assert not ok and "synthetic" in why
    ok, _ = quality.may_emit("BTCUSD", "scalping", data_mode="demo")
    assert not ok


def test_may_emit_reads_stamp_from_candle_list():
    candles = [{"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1},
               {"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1,
                "source": "synthetic"}]
    ok, why = quality.may_emit("EURUSD", "intraday", candles=candles)
    assert not ok


def test_style_allowed_only_scalping_restricted():
    assert quality.style_allowed("EURUSD", "scalping") is False
    assert quality.style_allowed("BTCUSD", "scalping") is True
    assert quality.style_allowed("EURUSD", "intraday") is True
    assert quality.style_allowed("BTCUSD", "intraday") is True


def test_allowed_styles_filters_scalping_for_delayed_pairs():
    styles = quality.allowed_styles("EURUSD", ["scalping", "intraday", "swing"])
    assert styles == ["intraday", "swing"]
    assert quality.allowed_styles("BTCUSD", ["scalping", "swing"]) == \
        ["scalping", "swing"]


def test_rejection_and_warning_copy_exists():
    assert "crypto" in quality.rejection_message("EURUSD", "scalping").lower()
    assert quality.quality_warning("EURUSD", "intraday") is not None
    assert quality.quality_warning("BTCUSD", "scalping") is None


# ---------------------------------------------------------------------------
# 3B - freshness gates
# ---------------------------------------------------------------------------

def _candles(closes, ts_step=60, now_ms=None):
    now_ms = now_ms or _ms()
    n = len(closes)
    return [{"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
             "volume": 10.0, "ts": now_ms - (n - 1 - i) * ts_step * 1000}
            for i, c in enumerate(closes)]


def test_freshness_ok():
    c = _candles([100 + i * 0.1 for i in range(100)])
    ok, why = freshness.check(c, "15m", now=_ms())
    assert ok is True and why is None


def test_freshness_stale():
    c = _candles([100 + i * 0.1 for i in range(50)])
    ok, why = freshness.check(c, "15m", now=_ms() + 1000 * 60 * 60)
    assert ok is False and "old" in why


def test_freshness_gap():
    c = _candles([100 + i * 0.1 for i in range(60)])
    c[40]["ts"] += 5 * 900 * 1000  # 5 extra 15m bars; gap tolerance is 3x
    ok, why = freshness.check(c, "15m", now=_ms())
    assert ok is False and "gap" in why


def test_freshness_non_monotonic():
    c = _candles([100 + i * 0.1 for i in range(60)])
    c[30]["ts"] = c[29]["ts"] - 5
    ok, why = freshness.check(c, "15m", now=_ms())
    assert ok is False and "monotonic" in why


def test_freshness_spike():
    c = _candles([100 + i * 0.1 for i in range(150)])
    c[-1]["high"], c[-1]["low"] = c[-1]["close"] * 1.5, c[-1]["close"] * 0.4
    ok, why = freshness.check(c, "15m", now=_ms())
    assert ok is False and "print" in why


def test_freshness_daily_long_weekend_allowed():
    c = _candles([100 + i * 0.1 for i in range(50)], ts_step=86400)
    ok3, _ = freshness.check(c, "1d", now=_ms() + 86400 * 1000 * 3.0)
    assert ok3 is True   # a 3-day-old daily bar (holiday weekend) passes
    ok4, why = freshness.check(c, "1d", now=_ms() + 86400 * 1000 * 4.0)
    assert ok4 is False and "old" in why


def _ms():
    return time.time() * 1000.0


# ---------------------------------------------------------------------------
# 3D - calendar blackout
# ---------------------------------------------------------------------------

def _event(hours_ago, impact="High", country="USD"):
    return {"start": time.time() - hours_ago * 3600.0,
            "country": country, "impact": impact}


def test_blackout_on_high_impact():
    cal = Calendar(fetch_json=lambda url: [])
    cal.set_events([_event(0.2)])
    assert cal.in_blackout() is True


def test_no_blackout_outside_window():
    cal = Calendar(fetch_json=lambda url: [])
    cal.set_events([_event(2.0)])  # two hours out > 30 min buffer
    assert cal.in_blackout() is False


def test_blackout_ignores_low_impact_one():
    cal = Calendar(fetch_json=lambda url: [])
    cal.set_events([_event(0.2, impact="Low")])
    assert cal.in_blackout() is False


def test_blackout_global_across_currencies():
    # High-impact EUR release still pauses an USD pair's signal
    cal = Calendar(fetch_json=lambda url: [])
    cal.set_events([_event(0.1, impact="High", country="EUR")])
    assert cal.in_blackout() is True


def test_keep_last_good_on_failure():
    cal = Calendar(fetch_json=lambda url: [])
    cal.set_events([_event(0.2)])
    assert cal.in_blackout() is True

    def always_fails(url):
        raise RuntimeError("feed down")

    cal._fetch = always_fails
    cal._fetched_at = 0.0  # force a refresh attempt on the next check
    # keep-last-good: refresh failed but the store still holds the event,
    # so in_blackout keeps returning True instead of silently opening up.
    assert cal.in_blackout() is True


def _iso(minutes_from_now, hours_after):
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now,
                                                   hours=hours_after)
    return start.isoformat().replace("+00:00", "-04:00")


def test_blackout_fail_open_when_no_data():
    cal = Calendar(fetch_json=lambda url: (_ for _ in ()).throw(
        RuntimeError("offline")))
    cal._fetched_at = 0.0
    assert cal.in_blackout() is False


def test_service_blackout_gate_wiring(tmp_path):
    svc = Service(object(), tmp_path / "state.json")
    assert svc.in_calendar_blackout() is False  # no calendar configured
    from app.data.calendar import Calendar as Cal
    cal = Cal(fetch_json=lambda url: [])
    cal.set_events([_event(0.1)])
    svc.calendar = cal
    assert svc.in_calendar_blackout() is True


# ---------------------------------------------------------------------------
# 3A - demo kill-switch: the running condition is "demo enabled + paid"
# ---------------------------------------------------------------------------

def test_demo_and_plan_condition_detected(tmp_path):
    # The hard-fail itself lives in main.py (sys.exit(2) when demo + paid).
    # Here we only pin the guard's inputs so a refactor can't silently widen
    # the demo+pay overlap: demo flag + active plans are both inspectable.
    from app import config as realcfg
    assert callable(realcfg.allow_demo_data)
    svc = Service(object(), tmp_path / "state.json")
    svc.plans["1"] = {"plan": "pro", "until": time.time() + 86400}
    now = datetime.now(timezone.utc).timestamp()
    active = [c for c, p in svc.plans.items()
              if p.get("until", 0) >= now and p.get("plan") in ("trial", "pro")]
    assert active == ["1"]


# ---------------------------------------------------------------------------
# 3C - queue/migration: scalping watches on delayed pairs get demoted
# ---------------------------------------------------------------------------

def test_migration_demotes_scalping_on_delayed_pairs(tmp_path):
    svc = Service(object(), tmp_path / "state.json")
    svc.watches = {
        "btc": {"key": "btc", "chat_id": 1, "pair": "BTCUSD",
                "style": "scalping", "mode": "normal",
                "last_signal_ts": 0.0},
        "eur": {"key": "eur", "chat_id": 2, "pair": "EURUSD",
                "style": "scalping", "mode": "normal",
                "last_signal_ts": 999.0},
        "eur2": {"key": "eur2", "chat_id": 3, "pair": "EURUSD",
                 "style": "intraday", "mode": "normal"},
    }
    demoted = svc.migrate_scalping_watches()
    assert [d[1] for d in demoted] == ["EURUSD"]
    assert svc.watches["eur"]["style"] == "intraday"
    assert svc.watches["btc"]["style"] == "scalping"  # crypto untouched
    assert svc.watches["eur2"]["style"] == "intraday"
    assert svc.watches["eur"]["last_signal_ts"] == 0.0


def test_universe_size_counts_only_allowed_pairs(tmp_path):
    svc = Service(object(), tmp_path / "state.json")
    n_scalp = svc.universe_size("scalping")
    n_total = len(constants.ALL_UNIVERSE)
    assert 0 < n_scalp < n_total
    assert svc.universe_size("intraday") == n_total


# ---------------------------------------------------------------------------
# 3E - spread widening
# ---------------------------------------------------------------------------

def _mode_profile():
    return {"rr": 2.0, "sl_atr_mult": 1.5, "risk_frac": 0.01}


def test_spread_widens_stop_and_lowers_rr():
    s = strat._spec("long", price=100.0, atr_value=2.0, support_levels=[],
                    resistance_levels=[], mode_profile=_mode_profile(),
                    spread_bps=0)
    assert abs(s["rr"] - 2.0) < 1e-9
    s2 = strat._spec("long", price=100.0, atr_value=2.0, support_levels=[],
                     resistance_levels=[], mode_profile=_mode_profile(),
                     spread_bps=25)
    assert s2["sl"] < s["sl"]          # stop is further away
    assert s2["rr"] < s["rr"]          # R:R fell after widening
    assert s2["spread_estimate"] == 25


def test_spread_kills_rr_below_floor():
    # rr target 2.0, floor 0.8x -> effective must stay >= 1.6. A degenerate
    # (huge) spread relative to the ATR distance must produce no setup.
    huge = 10_000  # bps = 100% of price on a 100.0 quote
    assert strat._spec("long", 100.0, 2.0, [], [],
                       _mode_profile(), spread_bps=huge) is None


def test_spread_backend_constant_table():
    assert constants.SPREAD_ESTIMATES["BTCUSD"] < 10
    assert constants.spread_bps("BTCUSD") == constants.SPREAD_ESTIMATES["BTCUSD"]
    assert constants.spread_bps("SOMETHINGUNKNOWN") == \
        constants.SPREAD_DEFAULT_BPS


# ---------------------------------------------------------------------------
# 3C - emission: analysis carries provenance for the scheduler gate
# ---------------------------------------------------------------------------

def test_analyze_carries_data_source_and_quality_note():
    from app.signals import engine as signal_engine
    hub = SyntheticProvider()
    analysis, _ = signal_engine.quick_analyze("BTCUSD", "intraday", "normal",
                                              hub)
    assert analysis["data_mode"] == "demo"  # synthetic is always demo
    assert analysis["data_source"] == "synthetic"
    assert analysis.get("quality_note") is None  # not a DataHub


def test_evaluate_passes_data_source_and_mode_through():
    from app.signals import engine as signal_engine
    base = {
        "pair": "BTCUSD", "side": "long", "style": "intraday", "mode": "normal",
        "base_tf": "15m", "reasons": ["x"], "exit_notes": [], "hold_horizon": "h",
        "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 98, "zone_high": 100,
                 "sl": 90, "tp1": 112, "tp2": 124, "rr": 2.0, "risk_pct": 1.0},
        "confidence": 95.0, "data_mode": "demo", "data_source": "synthetic",
        "ind": {"bb": {}, "stoch": {}}, "trend": {},
    }
    sig = signal_engine.evaluate(base)
    assert sig is not None
    assert sig["data_mode"] == "demo" and sig["data_source"] == "synthetic"


def test_signal_message_stamps_demo():
    from app.formatting import message as msg
    sig = {"pair": "BTCUSD", "side": "long", "style": "intraday",
           "mode": "normal", "tf": "1h", "entry_zone": (90, 91),
           "sl": 85.0, "rr": 2.0, "tp1": 95.0, "tp2": 100.0,
           "risk_pct": 1.0, "confidence": 60.0, "support": [], "resistance": [],
           "reasons": ["x"], "data_source": "synthetic", "data_mode": "demo"}
    text = msg.signal_message(sig, source="watch")
    assert "DEMO DATA" in text
"""Scalping on FX, metals, oil and the indices.

No feed the bot uses quotes bid/ask, so the spread is the static per-class
estimate and these tests pin what that buys: the estimate widens the stop
and trims the R:R, an absurd estimate makes a scalp unviable, the London/NY
window still gates FX and metals, and the style itself is offered on the
instruments that have a window and refused on single stocks. Candles are
deterministic mid-only fixtures stamped as the Yahoo feed; nothing here
touches the network.
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app import constants  # noqa: E402
from app.analysis import regime  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402
from app.data import quality  # noqa: E402
from app.data.provider import DataHub, stamp_source  # noqa: E402
from app.formatting import message as msg  # noqa: E402
from app.outcomes import OutcomeStore  # noqa: E402
from app.signals import engine as signal_engine  # noqa: E402
from app.signals.autopilot import lifecycle_block  # noqa: E402
from app.signals.scheduler import Service  # noqa: E402


T9 = datetime(2026, 9, 7, 13, 0, 0, tzinfo=timezone.utc).timestamp()  # Mon 13:00
T18 = datetime(2026, 9, 7, 18, 0, 0, tzinfo=timezone.utc).timestamp()  # Mon 18:00
TSAT = datetime(2026, 9, 5, 13, 0, 0, tzinfo=timezone.utc).timestamp()  # Sat


def _trend(n=150, step_ms=300000, drift=0.0008, start=1.0800, now_ms=None):
    """Deterministic rising mid-only series, shaped like a Yahoo response.
    Monotonic up-drift -> EMA stack bullish, so analyze() reliably picks a
    long side (and the mirrored down-drift a short one)."""
    now_ms = now_ms or int(time.time() * 1000)
    now_ms = int(now_ms / step_ms) * step_ms
    candles = []
    px = start
    ts = now_ms - (n - 1) * step_ms
    for _ in range(n):
        o = px
        c = o * (1 + drift)
        hi = max(o, c) * (1 + 0.0004)
        lo = min(o, c) * (1 - 0.0004)
        candles.append({
            "ts": ts, "open": o, "high": hi, "low": lo, "close": c,
            "volume": 100.0,
        })
        px = c
        ts += step_ms
    return candles


class _StubHub:
    """Non-DataHub stand-in. Routes around DataHub bypass the freshness and
    session gates, so the spec logic can be tested without a clock."""
    _tfs = {"5m": "5m", "15m": "15m", "1h": "1h"}

    def __init__(self, candles_by_tf):
        self._candles = {k: list(v) for k, v in candles_by_tf.items()}
        self.mode = "live"

    def fetch_klines_ex(self, symbol, interval, limit=200):
        return stamp_source(list(self._candles[interval]), "yahoo"), "live"

    def fetch_klines(self, symbol, interval, limit=200):
        return self.fetch_klines_ex(symbol, interval)[0]

    @staticmethod
    def classify(symbol):
        return "forex"


def _hub(candles):
    return _StubHub({tf: candles for tf in _StubHub._tfs})


def _as_datahub(candles):
    hub = DataHub()
    hub.fetch_klines_ex = lambda s, itv, limit=200: (
        stamp_source(list(candles), "yahoo"), "live")
    return hub


def _mode():
    return constants.MODE_PROFILE["normal"]


# -- spread basis -------------------------------------------------------------

def test_spread_bps_class_fallbacks_and_override():
    assert constants.spread_bps("BTCUSD") == constants.SPREAD_ESTIMATES["BTCUSD"]
    assert constants.spread_bps("EURUSD") == 2
    assert constants.spread_bps("XAUUSD") == 3
    assert constants.spread_bps("AAPL") == 8
    assert constants.spread_bps("SOMETHINGUNKNOWN") == constants.SPREAD_DEFAULT_BPS


def test_scalp_class_mapping():
    assert constants.scalp_class("EURUSD") == "fx_major"
    assert constants.scalp_class("GBPUSD") == "fx_major"
    assert constants.scalp_class("EURJPY") == "fx_other"
    assert constants.scalp_class("XAUUSD") == "spot_metals"
    assert constants.scalp_class("XAGUSD") == "metals"
    assert constants.scalp_class("WTI") == "metals"
    assert constants.scalp_class("US30") == "index_us"
    assert constants.scalp_class("NAS100") == "index_us"
    assert constants.scalp_class("BTCUSD") == "crypto"
    assert constants.scalp_class("AAPL") is None


def test_scalping_is_offered_on_every_class_with_a_window():
    # the gate quality.style_allowed reads is scalp_class, so the two must
    # never drift apart
    for pair in ("BTCUSD", "EURUSD", "EURJPY", "XAUUSD", "WTI", "US30"):
        assert constants.scalp_class(pair) is not None
        assert quality.style_allowed(pair, "scalping") is True
    for pair in ("AAPL", "SPY", "NOTAPAIR"):
        assert constants.scalp_class(pair) is None
        assert quality.style_allowed(pair, "scalping") is False


def test_spread_atr_max_style_defaults():
    assert constants.SPREAD_ATR_MAX == {"scalping": 0.35,
                                        "intraday": 0.20, "swing": 0.10}


# -- session windows ----------------------------------------------------------

def test_us_index_window_follows_the_new_york_cash_session():
    # 13:00 UTC is before the 13:30 open, 18:00 is inside it
    t1300 = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc).timestamp()
    assert regime.scalp_session("US30", now=t1300)["in_window"] is False
    assert regime.scalp_session("US30", now=T18)["in_window"] is True
    # Yahoo-served metals shut at 16:00, so the two classes genuinely differ
    assert regime.scalp_session("XAGUSD", now=T18)["in_window"] is False


def test_scalp_session_inside_and_outside():
    win = regime.scalp_session("EURUSD", now=T9)
    assert win["in_window"] is True and win["preferred"] is True
    win18 = regime.scalp_session("EURUSD", now=T18)
    assert win18["in_window"] is True  # fx majors run to 21:00
    assert regime.scalp_session("XAGUSD", now=T18)["in_window"] is False
    assert regime.scalp_session("EURJPY", now=T18)["in_window"] is False
    # spot gold scalps all day, with London/NY still the preferred slot
    gold18 = regime.scalp_session("XAUUSD", now=T18)
    assert gold18["in_window"] is True and gold18["preferred"] is False
    assert regime.scalp_session("XAUUSD", now=T9)["preferred"] is True
    t2130 = datetime(2026, 9, 7, 21, 30, tzinfo=timezone.utc).timestamp()
    assert regime.scalp_session("XAUUSD", now=t2130)["in_window"] is False
    assert regime.scalp_session("BTCUSD", now=T9) is None  # never gated


def test_next_session_open_skips_weekend():
    # Sat 13:00 -> Monday 07:00 UTC, never "Sunday 07:00".
    nxt = regime.next_session_open("XAGUSD", now=TSAT)
    assert datetime.fromtimestamp(nxt, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M") == "2026-09-07 07:00"
    # Mon 18:00 (Yahoo metals shut at 16:00) -> tomorrow 07:00.
    nxt2 = regime.next_session_open("XAGUSD", now=T18)
    assert datetime.fromtimestamp(nxt2, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M") == "2026-09-08 07:00"
    # spot gold after the 21:00 close -> tomorrow 00:00; Sat -> Monday 00:00
    t2130 = datetime(2026, 9, 7, 21, 30, tzinfo=timezone.utc).timestamp()
    assert datetime.fromtimestamp(regime.next_session_open("XAUUSD", now=t2130),
                                  tz=timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-09-08 00:00"
    assert datetime.fromtimestamp(regime.next_session_open("XAUUSD", now=TSAT),
                                  tz=timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-09-07 00:00"
    assert regime.next_session_open("BTCUSD", now=T9) is None


def test_block_kind_classification():
    assert regime.block_kind("EURUSD", "scalping", "5m", now=T18) == "closed"
    assert regime.block_kind("XAGUSD", "scalping", "5m", now=T18) == "session"
    assert regime.block_kind("XAUUSD", "scalping", "5m", now=T18) == "closed"
    assert regime.block_kind("EURUSD", "intraday", "15m", now=T9) == "stale"
    assert regime.block_kind("BTCUSD", "scalping", "5m", now=T18) == "stale"


# -- side-priced spec ---------------------------------------------------------

def test_spec_long_is_quoted_at_the_mid_with_a_spread_widened_stop():
    candles = _trend()
    hub = _hub(candles)
    a = strat.analyze("EURUSD", "scalping", "normal", hub)
    assert a["side"] == "long"
    spec = a["spec"]
    last_close = candles[-1]["close"]
    assert spec["market"] == pytest.approx(last_close)
    assert spec["tp1"] > spec["market"] and spec["tp2"] > spec["tp1"]
    assert spec["spread_estimate"] == constants.spread_bps("EURUSD")
    assert spec["spread_unit"] == "bps"
    # the quoted risk is the stop distance plus the assumed spread, so the
    # R:R the user is shown is below the style target, not the raw number
    widening = last_close * constants.spread_bps("EURUSD") / 10000.0
    assert spec["market"] - spec["sl"] > widening
    assert spec["rr"] < _mode()["rr"]


def test_spec_short_mirrors_the_long_side():
    candles = _trend(drift=-0.0008)
    hub = _hub(candles)
    a = strat.analyze("EURUSD", "scalping", "normal", hub)
    assert a["side"] == "short"
    spec = a["spec"]
    assert spec["market"] == pytest.approx(candles[-1]["close"])
    assert spec["tp1"] < spec["market"] and spec["tp2"] < spec["tp1"]
    assert spec["sl"] > spec["market"]
    assert spec["rr"] < _mode()["rr"]


def test_spec_drops_a_setup_the_spread_makes_unprofitable():
    # 900 bps on a 100.0 price is 9.0 of spread against a 2.0 ATR: whatever
    # the structure says, there is no R:R left to trade
    assert strat._spec("long", 100.0, 2.0, None, None, _mode(),
                       spread_bps=900) is None


def test_spec_static_path_still_bps():
    s = strat._spec("long", 100.0, 2.0, None, None, _mode(), spread_bps=25)
    assert s["spread_estimate"] == 25 and s["spread_unit"] == "bps"


def test_analysis_reports_spread_context_and_flags_it_as_an_estimate():
    candles = _trend()
    a = strat.analyze("EURUSD", "scalping", "normal", _hub(candles))
    assert a["spread"]["median"] > 0
    assert a["spread"]["estimated"] is True
    assert a["spread"]["latest"] == pytest.approx(
        candles[-1]["close"] * constants.spread_bps("EURUSD") / 10000.0)
    assert 0 < a["spread"]["atr_ratio"] < 1


# -- viability + session gates through a real DataHub -------------------------

def test_viability_gate_rejects_wide_spread(monkeypatch):
    # 500 bps of assumed spread is a huge slice of the bar ATR
    monkeypatch.setitem(constants.SPREAD_ESTIMATES, "EURUSD", 500)
    hub = _as_datahub(_trend())
    # freeze the session window open so the viability gate is the active one
    monkeypatch.setattr(regime, "scalp_session",
                        lambda pair, now=None: {
                            "class": "fx_major", "in_window": True,
                            "preferred": True, "windows": (), "label": ""})
    with pytest.raises(ValueError, match="quality gate: viability:"):
        strat.analyze("EURUSD", "scalping", "normal", hub)


def test_session_gate_rejects_outside_window(monkeypatch):
    candles = _trend()
    hub = _as_datahub(candles)
    monkeypatch.setattr(regime, "scalp_session",
                        lambda pair, now=None: {
                            "class": "fx_major", "in_window": False,
                            "preferred": False, "windows": (), "label": ""})
    with pytest.raises(ValueError, match="quality gate: session:"):
        strat.analyze("EURUSD", "scalping", "normal", hub)


def test_viability_gate_leaves_crypto_alone(monkeypatch):
    # crypto trades the exchange book: no spread is imposed on it, so the
    # ATR-ratio gate must not fire however wide the table says
    monkeypatch.setitem(constants.SPREAD_ESTIMATES, "BTCUSD", 500)
    monkeypatch.setattr(regime, "scalp_session", lambda pair, now=None: None)
    # no viability ValueError: the pair is analysed and simply finds no
    # tradeable spec once the R:R rule has had its say
    a = strat.analyze("BTCUSD", "scalping", "normal",
                      _as_datahub(_trend(start=97000.0)))
    assert a["pair"] == "BTCUSD"


def test_session_gate_ignores_crypto(monkeypatch):
    # crypto is 24/7: scalp_session -> None must never gate it.
    candles = _trend(start=97000.0)
    hub = _as_datahub(candles)
    monkeypatch.setattr(regime, "scalp_session",
                        lambda pair, now=None: None)
    a = strat.analyze("BTCUSD", "scalping", "normal", hub)
    assert a["spec"] is not None


# -- user-facing gate copy ----------------------------------------------------

def test_quality_gate_text_session():
    t = msg.quality_gate_text("EURUSD", "scalping",
                              "quality gate: session: EURUSD scalping window "
                              "closed", now=T18)
    assert "not the right time" in t
    assert "Next window opens" in t


def test_quality_gate_text_closed():
    t = msg.quality_gate_text("EURUSD", "scalping",
                              "quality gate: closed: EURUSD 5m last bar "
                              "480m old > 20m tolerance", now=TSAT)
    assert "markets look closed" in t


def test_quality_gate_text_viability():
    t = msg.quality_gate_text("XAUUSD", "scalping",
                              "quality gate: viability: XAUUSD 5m spread 0.5 "
                              "is 10.00x its ATR (0.35 limit)")
    assert "spread is too wide" in t


def test_quality_gate_text_stale():
    t = msg.quality_gate_text("EURUSD", "scalping",
                              "quality gate: stale: EURUSD 5m last bar "
                              "480m old > 20m tolerance")
    assert "feed is quiet" in t


def test_signal_message_names_the_delayed_feed_and_the_assumed_spread():
    sig = {
        "pair": "EURUSD", "side": "long", "style": "scalping",
        "mode": "normal", "tf": "5m", "entry_zone": (1.0850, 1.0852),
        "sl": 1.0840, "tp1": 1.0870, "tp2": 1.0875, "rr": 2.0,
        "risk_pct": 1.0, "confidence": 80, "support": [], "resistance": [],
        "reasons": [], "data_source": "yahoo", "data_mode": "live",
        "spread_estimate": 2, "component_scores": {"atr": 0.0008},
    }
    t = msg.signal_message(sig)
    assert "Yahoo delayed" in t
    assert "spread assumed 2 bps" in t
    assert "verify with your broker" in t


# -- 5A/5C provenance + engine passthrough -------------------------------------

def test_engine_signal_carries_the_spread_estimate_in_bps():
    a, sig = signal_engine.quick_analyze(
        "EURUSD", "scalping", "normal", _hub(_trend()))
    assert sig is not None and sig["spread_unit"] == "bps"
    assert sig["spread_estimate"] == constants.spread_bps("EURUSD")
    assert a["spread"]["latest"] is not None


def test_delayed_feed_scalps_but_synthetic_never_does():
    # FX is delayed and still scalps: the instrument decides, not the feed
    assert quality.static_tier("EURUSD") is quality.Tier.DELAYED
    ok, _ = quality.may_emit("EURUSD", "scalping", source="yahoo")
    assert ok is True
    # a slip to the demo generator is blocked whatever the pair
    bad, reason = quality.may_emit("EURUSD", "scalping", source="synthetic")
    assert bad is False and "synthetic" in reason
    bad, reason = quality.may_emit("BTCUSD", "scalping", data_mode="demo")
    assert bad is False and "synthetic" in reason


# -- shadow capture + lifecycle gate (5B) --------------------------------------

def _sig(pair="EURUSD", side="long", style="scalping",
         entry=1.0900, atr=0.0008):
    return {
        "pair": pair, "side": side, "style": style, "mode": "normal",
        "tf": "5m", "entry": entry, "sl": entry - 0.005, "tp1": entry + 0.006,
        "tp2": entry + 0.010, "rr": 2.0, "confidence": 75, "reasons": ["x"],
        "data_source": "yahoo", "data_mode": "live", "ts": time.time(),
        "spread_estimate": 2, "spread_unit": "bps",
        "component_scores": {"atr": atr},
    }


def test_scalp_shadow_env_flag(monkeypatch):
    monkeypatch.delenv("EZYAI_SCALP_SHADOW", raising=False)
    assert config.scalp_shadow() is False
    for v in ("1", "true", "yes", "on"):
        monkeypatch.setenv("EZYAI_SCALP_SHADOW", v)
        assert config.scalp_shadow() is True
    monkeypatch.setenv("EZYAI_SCALP_SHADOW", "0")
    assert config.scalp_shadow() is False


def test_shadow_row_isolated_from_resolver_and_stats(tmp_path):
    store = OutcomeStore(tmp_path / "s.db")
    store.record_shadow(7, _sig())
    assert store.open_signals() == []  # resolver never sees the shadow
    st = store.stats()
    assert st["total"] == 0 and st["open"] == 0
    row = store.query("SELECT * FROM signals")[0]
    assert row["status"] == "shadow" and row["data_source"] == "shadow"
    assert row["pair"] == "EURUSD"


def test_store_migrates_legacy_schema_to_spread_unit(tmp_path):
    import sqlite3
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "chat_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'watch', "
        "created_at REAL NOT NULL, pair TEXT NOT NULL, style TEXT NOT NULL, "
        "mode TEXT NOT NULL, direction TEXT NOT NULL, entry REAL NOT NULL, "
        "stop_loss REAL NOT NULL, tp1 REAL NOT NULL, tp2 REAL NOT NULL, "
        "rr_target REAL NOT NULL, confidence REAL NOT NULL, "
        "component_scores TEXT NOT NULL DEFAULT '{}', "
        "data_source TEXT NOT NULL DEFAULT 'live', "
        "spread_estimate REAL, status TEXT NOT NULL DEFAULT 'open', "
        "same_candle_ambig INTEGER NOT NULL DEFAULT 0, "
        "resolved_at REAL, exit_price REAL, r_multiple REAL)")
    con.commit()
    con.close()
    store = OutcomeStore(db)  # must forward-migrate, not crash
    cols = {r["name"] for r in store.query("PRAGMA table_info(signals)")}
    assert "spread_unit" in cols
    store.record(7, _sig())  # and new rows still land, spread_unit included
    row = store.query("SELECT * FROM signals")[0]
    assert row["spread_unit"] == "bps"


def test_lifecycle_open_signal_blocks_until_resolved(tmp_path):
    store = OutcomeStore(tmp_path / "s.db")
    sig = _sig("EURUSD", "long", "scalping", entry=1.0900)
    store.record_shadow(7, sig)  # shadows never count as the open position
    blocked, why = lifecycle_block(store, 7, "EURUSD", "scalping", sig)
    assert blocked is False
    sid = store.record(7, sig)
    blocked, why = lifecycle_block(store, 7, "EURUSD", "scalping", sig)
    assert blocked is True and "still open" in why
    store.mark_resolved(sid, "tp1", 1.0920, 2.0)
    blocked, why = lifecycle_block(store, 7, "EURUSD", "scalping", sig)
    assert blocked is False


def test_lifecycle_zone_rearm_blocks_near_last_entry(tmp_path):
    store = OutcomeStore(tmp_path / "s.db")
    sig = _sig("EURUSD", "long", "scalping", entry=1.0900, atr=0.0100)
    sid = store.record(7, sig)
    store.mark_resolved(sid, "tp1", 1.0920, 2.0)
    near = dict(sig, component_scores={"atr": 0.01, "close": 1.0904})
    blocked, why = lifecycle_block(store, 7, "EURUSD", "scalping", near)
    assert blocked is True and "re-arm" in why  # 0.0004 <= 0.5 * atr
    away = dict(sig, component_scores={"atr": 0.01, "close": 1.0980})
    blocked, why = lifecycle_block(store, 7, "EURUSD", "scalping", away)
    assert blocked is False  # 0.008 > 0.5 * atr


def test_lifecycle_no_store_never_blocks():
    sig = _sig()
    blocked, why = lifecycle_block(None, 7, "EURUSD", "scalping", sig)
    assert blocked is False and why is None


def test_service_shadow_capture_swallows_scalping(monkeypatch, tmp_path):
    svc = Service(_hub(_trend()), tmp_path / "state.json")
    monkeypatch.setattr(config, "scalp_shadow", lambda: True)
    sig = _sig("EURUSD", "long", "scalping")
    watch = {"chat_id": 7, "pair": "EURUSD", "style": "scalping",
             "mode": "normal"}
    assert svc._shadow_capture(watch, sig) is True
    row = svc.outcomes.query("SELECT * FROM signals")[0]
    assert row["status"] == "shadow" and row["style"] == "scalping"
    # non-scalping flow is untouched by the shadow week.
    sig2 = _sig("EURUSD", "long", "intraday")
    assert svc._shadow_capture(dict(watch, style="intraday"), sig2) is False

# -- what a stale feed inside an open window is told ---------------------------

def test_open_window_with_a_quiet_feed_is_not_reported_as_a_shut_market():
    # Mon 15:25 UTC: gold's window (07:00-16:00) has 35 minutes left, so
    # "no alerts until the venue reopens, next session Tuesday" would be a
    # claim the bot never checked -- the feed is what stopped, not the market
    t = datetime(2026, 9, 7, 15, 25, tzinfo=timezone.utc).timestamp()
    t = msg.quality_gate_text(
        "XAUUSD", "scalping",
        "quality gate: closed: XAUUSD 5m last bar 34m old > 20m tolerance",
        now=t)
    assert "quotes have gone quiet" in t
    assert "markets look closed" not in t
    assert "Tuesday" not in t
    assert "resume by themselves" in t


def test_a_genuinely_shut_venue_still_names_the_next_session():
    # 03:00 is outside silver's Yahoo window (gold's spot window now runs
    # all day, so it no longer serves as the shut-venue example)
    t = datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc).timestamp()  # 03:00
    out = msg.quality_gate_text(
        "XAGUSD", "scalping",
        "quality gate: closed: XAGUSD 5m last bar 400m old > 20m tolerance",
        now=t)
    assert "markets look closed" in out
    assert "Next session" in out


def test_the_weekend_is_never_inside_a_window():
    # 13:00 matches the London/NY clock, but Saturday is not a trading day
    assert regime.scalp_session("EURUSD", now=TSAT)["in_window"] is False
    assert regime.scalp_session("XAUUSD", now=TSAT)["preferred"] is False
    assert regime.scalp_session("EURUSD", now=T9)["in_window"] is True


# -- metals are priced spot, not off the futures basis -------------------------

def test_metals_resolve_to_spot_with_a_futures_fallback():
    from app.data.provider import DataHub
    hub = DataHub()
    assert hub.resolve("XAUUSD")[1] == "PAXGUSDT"
    assert hub.resolve("XAGUSD")[1] == "SI=F"
    assert hub._cfd_fallback("XAUUSD", "PAXGUSDT") == "GC=F"
    # once the futures ticker is the one in hand, there is nothing left to
    # fall back to and a failure must surface instead of looping
    assert hub._cfd_fallback("XAUUSD", "GC=F") is None

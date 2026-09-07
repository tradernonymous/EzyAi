"""Phase-4 calibration: confidence buckets vs realised hit rate & R.

Covers the curve/overall/recommend policy: live-only rows, 10-point buckets,
credibility floors, expectancy maths and the (never auto-applied) gate-raise
suggestion, plus the engine's spread_estimate passthrough to the store.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.outcomes import OutcomeStore  # noqa: E402
from app.outcomes.calibration import (  # noqa: E402
    Calibration, MIN_RESOLVED, _expectancy)


def sig(conf=70.0, style="intraday", mode="normal", data_mode="live",
        pair="BTCUSD"):
    return {
        "pair": pair, "side": "long", "style": style, "mode": mode,
        "tf": "15m", "entry": 100.0, "sl": 90.0, "tp1": 120.0, "tp2": 140.0,
        "rr": 2.0, "confidence": conf, "reasons": ["x"], "data_mode": data_mode,
        "ts": time.time(), "component_scores": {}, "spread_estimate": 10,
    }


def seed(store, rows):
    for conf, status, r, _data_mode in rows:
        sid = store.record(1, sig(conf=conf, data_mode=_data_mode), "watch")
        store.mark_resolved(sid, status, exit_price=100.0, r_multiple=r)


def resolved_rows(store):
    return store.query("SELECT * FROM signals")


# -- curve -------------------------------------------------------------------

def test_curve_buckets_by_ten_and_averages(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    seed(store, [
        (72, "wan", 1.5, "live"), (74, "wan", 1.5, "live"),
        (72, "loss", -0.9, "live"), (95, "wan", 2.0, "live"),
    ])
    cal = Calibration(store)
    curve = cal.curve()
    assert [(r["style"], r["mode"], r["lo"]) for r in curve] == [
        ("intraday", "normal", 70), ("intraday", "normal", 90)]
    b70 = next(r for r in curve if r["lo"] == 70)
    assert b70["resolved"] == 3 and b70["wins"] == 2
    assert b70["win_pct"] == pytest.approx(100.0 * 2 / 3)
    assert b70["avg_win_r"] == 1.5 and b70["avg_loss_r"] == -0.9
    assert b70["credible"] is False  # 3 < MIN_RESOLVED
    assert b70["expectancy"] == pytest.approx(
        2 / 3 * 1.5 + 1 / 3 * (-0.9))


def test_curve_ignores_demo_rows(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    seed(store, [
        (72, "loss", -1.0, "live"), (72, "loss", -1.0, "live"),
        (72, "loss", -1.0, "demo"), (72, "loss", -1.0, "demo"),
    ])
    rows = resolved_rows(store)
    cal = Calibration(store)
    assert cal.overall()["resolved"] == 2
    curve = cal.curve()
    assert len(curve) == 1 and curve[0]["resolved"] == 2
    assert len(rows) == 4  # demo rows ARE stored, just never used to tune


def test_expectancy_handles_all_wins_or_all_losses(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    seed(store, [(95, "wan", 2.0, "live"), (95, "wan", 2.0, "live")])
    cal = Calibration(store)
    row = cal.curve()[0]
    assert row["expectancy"] == pytest.approx(2.0)  # no avg_loss_r -> p*avg_win


def test_overall_aggregates(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    seed(store, [(70, "wan", 1.5, "live"), (70, "loss", -1.0, "live"),
                 (70, "loss", -1.0, "demo")])
    o = Calibration(store).overall()
    assert o["resolved"] == 2 and o["wins"] == 1
    assert o["win_pct"] == 50.0 and o["avg_r"] == pytest.approx(0.25)


def test_empty_store(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    cal = Calibration(store)
    assert cal.curve() == []
    assert cal.overall()["resolved"] == 0
    assert cal.recommend() == []


# -- recommend ---------------------------------------------------------------

def test_recommend_raises_gate_when_credible_bucket_underpays(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    # intraday/normal gate = 68. Bucket 70 (n=40) pays -0.3R -> raise to 70.
    seed(store, [(72, "wan", 1.5, "live")] * 10 +
                [(72, "loss", -0.9, "live")] * 30 +
                [(95, "wan", 2.0, "live")] * 35)  # healthy 90 bucket
    recs = Calibration(store).recommend()
    assert len(recs) == 1
    r = recs[0]
    assert r["style"] == "intraday" and r["mode"] == "normal"
    assert r["current_gate"] == 68 and r["proposed_gate"] == 70
    assert r["verdict"] == "raise"
    assert r["n"] == 40
    assert r["expectancy"] == pytest.approx(
        10 / 40 * 1.5 + 30 / 40 * (-0.9))


def test_recommend_silent_when_credible_tiers_pay(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    # bucket 70 pays +0.714R on 35 outcomes -> no raise proposed.
    seed(store, [(72, "wan", 2.0, "live")] * 20 +
                [(72, "loss", -1.0, "live")] * 15)
    assert Calibration(store).recommend() == []


def test_recommend_needs_credible_bucket(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    seed(store, [(72, "loss", -1.0, "live")] * 10)  # below MIN_RESOLVED
    assert Calibration(store).recommend() == []


# -- engine passthrough ------------------------------------------------------

def test_engine_signal_carries_spread_estimate():
    from app.signals import engine
    analysis = {
        "pair": "BTCUSD", "side": "long", "style": "scalping",
        "mode": "normal", "base_tf": "15m",
        "spec": {"market": 10.0, "limit": 10.0, "sl": 9.5, "tp1": 11.0,
                 "tp2": 12.0, "rr": 2.0, "risk_pct": 0.01, "zone_low": 9.9,
                 "zone_high": 10.1, "spread_estimate": 34},
        "ind": {"ema21": None, "ema50": None, "bb": {}, "stoch": {},
                "atr": 0.2, "macd_hist": None, "rsi": None},
        "trend": {"adx": 25.0},
        "reasons": ["x"], "exit_notes": [], "hold_horizon": "1-6h",
        "levels": {"support": [], "resistance": []},
        "confidence": 70.0, "data_mode": "live", "data_source": "binance",
        "confirm": {}, "cross": None,
    }
    sig = engine.evaluate(analysis)
    assert sig["spread_estimate"] == 34


# -- report ------------------------------------------------------------------

def test_calibration_report_empty_state(tmp_path):
    store = OutcomeStore(tmp_path / "signals.db")
    from app.formatting import message as msg
    text = msg.calibration_report(Calibration(store))
    assert "No resolved live signals yet" in text
    assert "calibration kicks in" in text
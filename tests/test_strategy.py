from app.analysis import strategy as strat
from app.data.provider import DataHub
from app.signals import engine as signal_engine
from app.signals.autopilot import AutoPilot


def make_hub():
    hub = DataHub(allow_demo=True)
    hub.allow_demo = True
    hub.mode = "demo"
    return hub


def random_pair(hub):
    return hub.random_symbol()


def test_analyze_output_shape():
    hub = make_hub()
    for style in ("scalping", "intraday", "swing"):
        for mode in ("safe", "normal", "aggressive"):
            a = strat.analyze(random_pair(hub), style, mode, hub)
            assert a["price"] > 0
            assert a["pair"]
            assert a["side"] in ("long", "short", "neutral")
            assert 0 <= a["confidence"] <= 100
            if a["spec"]:
                spec = a["spec"]
                if a["side"] == "long":
                    assert spec["sl"] < spec["tp1"] < spec["tp2"]
                    assert spec["market"] > spec["sl"]
                else:
                    assert spec["sl"] > spec["tp1"] > spec["tp2"]
                    assert spec["market"] < spec["sl"]
                assert spec["rr"] >= 0.5
            assert isinstance(a["reasons"], list)


def test_spec_risk_positive_distance():
    hub = make_hub()
    a = strat.analyze("BTCUSD", "intraday", "normal", hub)
    if a["spec"]:
        assert abs(a["spec"]["tp1"] - a["spec"]["market"]) > 0


def test_engine_accepts_high_confidence_long():
    analysis = {
        "pair": "BTCUSD", "side": "long", "style": "intraday", "mode": "safe",
        "base_tf": "15m", "confidence": 90.0,
        "reasons": ["test"], "exit_notes": ["x"], "hold_horizon": "hours",
        "levels": {"support": [1], "resistance": [2]},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 1.0},
        "ind": {"macd_hist": 5.0}, "data_mode": "demo",
    }
    sig = signal_engine.evaluate(analysis)
    assert sig is not None
    assert sig["side"] == "long"
    assert sig["tp1"] > sig["entry"] > sig["sl"]


def test_engine_rejects_neutral():
    analysis = {
        "pair": "EURUSD", "side": "neutral", "mode": "normal",
        "style": "intraday", "base_tf": "15m", "confidence": 5.0,
        "reasons": [], "exit_notes": [], "hold_horizon": "hours",
        "levels": {"support": [], "resistance": []},
        "spec": None, "ind": {}, "data_mode": "demo",
    }
    assert signal_engine.evaluate(analysis) is None


def test_engine_gate_respects_confidence():
    base = {
        "pair": "BTCUSD", "side": "long", "style": "intraday", "mode": "normal",
        "base_tf": "15m", "confidence": 1.0, "reasons": [], "exit_notes": [],
        "hold_horizon": "hours", "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 1.0},
        "ind": {"macd_hist": 5.0}, "data_mode": "demo",
    }
    assert signal_engine.evaluate(base) is None
    base["confidence"] = 80.0
    assert signal_engine.evaluate(base) is not None


def test_autopilot_respects_daily_limit():
    hub = make_hub()
    pilot = AutoPilot(hub, 1, "intraday", "normal")
    counters = {}
    hits = 0
    for _ in range(50):
        sig, err = pilot.run(counters)
        if sig is not None:
            hits += 1
        if err:
            break
    assert hits <= 6


def test_engine_safe_requires_macd_confirmation():
    base = {
        "pair": "BTCUSD", "side": "long", "mode": "safe",
        "style": "intraday", "base_tf": "15m", "confidence": 90.0,
        "reasons": [], "exit_notes": [], "hold_horizon": "hours",
        "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 0.5},
        "ind": {"macd_hist": -3.0}, "data_mode": "demo",
    }
    assert signal_engine.evaluate(base) is None
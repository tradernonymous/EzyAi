import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402


def test_gates_cover_all_styles():
    for style in constants.STYLES:
        g = constants.SIGNAL_GATES[style]
        for key in ("rsi_long", "rsi_short", "adx_min", "stoch_cut",
                    "macd_atr_min", "conf_gate"):
            assert key in g, (style, key)


def test_gate_defaults_match_legacy_thresholds():
    g = constants.SIGNAL_GATES["scalping"]
    assert tuple(g["rsi_long"]) == (45.0, 68.0)
    assert tuple(g["rsi_short"]) == (30.0, 55.0)
    assert g["adx_min"] == 25.0
    assert g["stoch_cut"] == 50.0
    assert g["macd_atr_min"] == 0.0
    assert g["conf_gate"] == float(constants.CONFIDENCE_GATE)


def test_approved_tuned_gates():
    # Approved 2026-09-05 from scripts/tune.py evidence (see tune_results).
    intra = constants.SIGNAL_GATES["intraday"]
    assert tuple(intra["rsi_long"]) == (40.0, 65.0)
    assert tuple(intra["rsi_short"]) == (28.0, 52.0)
    assert intra["adx_min"] == 32.0
    assert intra["conf_gate"] == 66.0
    swing = constants.SIGNAL_GATES["swing"]
    assert swing["adx_min"] == 28.0
    assert swing["stoch_cut"] == 45.0
    assert swing["conf_gate"] == 70.0


def test_scalar_direction_matches_legacy_logic():
    cases = [
        (11.0, 10.0, 9.0, 0.5, "up"),
        (9.0, 10.0, 11.0, -0.5, "down"),
        (10.0, 10.0, 10.0, 0.0, "neutral"),
        (11.0, 10.0, 10.5, 0.2, "neutral"),  # mixed align, ema21<ema50 but hist>0
        (9.0, 10.0, 9.5, -0.2, "neutral"),  # mixed align, ema21>ema50 but hist<0
        (None, 10.0, 9.0, 0.5, "up"),  # macd confirmation carries
    ]
    for ema9, ema21, ema50, hist, want in cases:
        direction, bull, bear = strat._direction_from(ema9, ema21, ema50, hist)
        assert direction == want, (ema9, ema21, ema50, hist)
    assert strat._direction_from(11.0, 10.0, 9.0, 0.5)[1] is True
    assert strat._direction_from(9.0, 10.0, 11.0, -0.5)[2] is True


def test_scalar_confidence_matches_wrapper():
    closes = [100.0 + (i % 7) + i * 0.05 for i in range(120)]
    candles = [{"open": c, "high": c * 1.002, "low": c * 0.998, "close": c,
                "volume": 10.0, "ts": i} for i, c in enumerate(closes)]
    gates = constants.SIGNAL_GATES["intraday"]
    r1, r2 = [], []
    s1 = strat._confidence("long", candles, closes, "up", 0.05, 55.0,
                           closes[-1] * 0.999, 60.0, 30.0, r1, gates)
    feats = {"ema21": 1.0, "ema50": 0.9, "adx": 30.0, "macd_hist": 0.05,
             "rsi": 55.0, "bb_mid": closes[-1] * 0.999, "stoch_k": 60.0,
             "close": closes[-1], "atr": 0.5}
    s2 = strat._confidence_from("long", feats, gates, r2)
    assert r1 and r2 and r1[0] == r2[0]

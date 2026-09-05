import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import patterns as pat  # noqa: E402
from app.analysis import regime as rg  # noqa: E402
from app.analysis import sentiment as sent  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402


def _c(o, h, l, c, ts=0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 1.0, "ts": ts}


def test_bullish_engulfing():
    prev = _c(10, 10.5, 9.5, 9.6)
    cur = _c(9.5, 10.8, 9.4, 10.6)
    assert pat.bullish_engulfing(prev, cur) is True
    assert pat.detect([prev, cur]) == 1


def test_bearish_engulfing():
    prev = _c(9.6, 10.5, 9.5, 10.4)
    cur = _c(10.5, 10.6, 9.2, 9.4)
    assert pat.bearish_engulfing(prev, cur) is True
    assert pat.detect([prev, cur]) == -1


def test_hammer_and_shooting_star():
    assert pat.hammer(_c(10.0, 10.1, 9.0, 10.05)) is True
    assert pat.shooting_star(_c(10.0, 11.0, 9.95, 10.05)) is True
    assert pat.shooting_star(_c(10.0, 10.1, 9.0, 10.05)) is False


def test_doji_is_neutral():
    assert pat.detect([_c(10, 10.5, 9.5, 10.0), _c(10.0, 10.05, 9.95, 10.0)]) == 0


def test_bias_series_has_no_lookahead():
    base = [_c(10 + i * 0.1, 10.5 + i * 0.1, 9.5 + i * 0.1, 10 + i * 0.1)
            for i in range(10)]
    b1 = pat.pattern_bias(base)
    mutated = list(base)
    mutated[-1] = _c(20, 25, 5, 6)  # violent last bar
    b2 = pat.pattern_bias(mutated)
    assert b1[:-1] == b2[:-1]  # earlier bars unaffected by the future


def test_sentiment_directions():
    assert sent.score_text("Bitcoin surges to record high on ETF boom") > 0.2
    assert sent.score_text("Stocks plunge on recession fears, panic selling") < -0.2
    assert sent.score_text("Company reports earnings in line with estimates") == 0.0
    assert sent.score_text("") == 0.0


def test_sentiment_negation_flips():
    pos = sent.score_text("shares rally strongly")
    neg = sent.score_text("shares do not rally strongly")
    assert pos > 0 and neg < 0


def test_sentiment_headlines():
    hs = [{"title": "Gold soars to record high"}, {"title": "Oil rebounds strongly"}]
    s = sent.score_headlines(hs)
    assert s is not None and s > 0.2
    assert sent.score_headlines([]) is None
    assert sent.score_headlines([{"title": "Meeting scheduled for Tuesday"}]) is None


def test_vol_ratio_calm_is_near_one():
    closes = [100.0 + (i % 5) * 0.01 for i in range(200)]
    import app.analysis.indicators as ind
    roll = ind.realized_vol(closes)
    r = rg.vol_ratio(roll, len(closes) - 1)
    assert r is not None and 0.5 < r < 2.0


def test_vol_chaos_trims_confidence():
    reasons = []
    out = rg.apply_vol_regime(80.0, 3.0, reasons)
    assert out == 72.0
    assert reasons and "High-volatility" in reasons[0]


def test_vol_dead_trims_slightly():
    assert rg.apply_vol_regime(80.0, 0.2, []) == 76.0
    assert rg.apply_vol_regime(80.0, 1.0, []) == 80.0
    assert rg.apply_vol_regime(80.0, None, []) == 80.0


def test_session_states():
    # 2026-09-07 is a Monday; 2026-09-05 a Saturday (UTC).
    mon = 1788768000000   # Mon 2026-09-07 08:00 UTC
    sat = 1788595200000   # Sat 2026-09-05 08:00 UTC
    assert rg.session_state("crypto", sat) == "open"
    assert rg.session_state("forex", sat) == "closed"
    assert rg.session_state("cfd", sat) == "closed"
    assert rg.session_state("forex", mon - 6 * 3600000) == "thin"  # 02:00 Asian
    assert rg.session_state("forex", mon) == "open"
    assert rg.session_state("stock", mon) == "thin"  # pre-market
    assert rg.session_state("stock", mon + 8 * 3600000) == "open"  # 16:00


def test_session_adjustment_bounds():
    assert rg.apply_session(80.0, "crypto", 1788595200000, []) == 80.0
    reasons = []
    out = rg.apply_session(80.0, "forex", 1788595200000, reasons)
    assert out == 72.0 and reasons


class _Hub:
    mode = "live"

    def __init__(self, candles, dirc):
        self._c = candles
        self._d = dirc

    def fetch_klines(self, pair, interval, limit=200):
        return self._c if interval == "15m" else self._d

    def classify(self, pair):
        return "crypto"


def test_analyze_carries_confluence_block():
    closes = [100.0 + i * 0.05 + (i % 5) for i in range(150)]
    candles = [_c(c, c * 1.001, c * 0.999, c, ts=i * 900000) for i, c in enumerate(closes)]
    a = strat.analyze("BTCUSD", "intraday", "normal", _Hub(candles, candles))
    assert "confluence" in a
    assert set(a["confluence"]) == {"pattern", "sentiment", "vol_ratio", "session"}
    assert a["confluence"]["sentiment"] is None


def test_sentiment_moves_confidence_bounded():
    closes = [100.0 + i * 0.05 + (i % 5) for i in range(150)]
    candles = [_c(c, c * 1.001, c * 0.999, c, ts=i * 900000) for i, c in enumerate(closes)]
    hub = _Hub(candles, candles)
    base = strat.analyze("BTCUSD", "intraday", "normal", hub)
    if base["side"] == "neutral":
        return
    up = strat.analyze("BTCUSD", "intraday", "normal", hub, sentiment=0.9)
    dn = strat.analyze("BTCUSD", "intraday", "normal", hub, sentiment=-0.9)
    assert abs(up["confidence"] - base["confidence"]) <= 8.0
    assert abs(dn["confidence"] - base["confidence"]) <= 8.0
    if base["side"] == "long":
        assert up["confidence"] >= base["confidence"] >= dn["confidence"]
    else:
        assert dn["confidence"] >= base["confidence"] >= up["confidence"]


def _fixture_hub():
    closes = [100.0 + i * 0.05 + (i % 5) for i in range(150)]
    candles = [_c(c, c * 1.001, c * 0.999, c, ts=i * 900000) for i, c in enumerate(closes)]
    return _Hub(candles, candles)


def test_display_only_scoring_off_by_default():
    import app.constants as constants
    assert constants.CONFLUENCE_SCORING is False
    hub = _fixture_hub()
    base = strat.analyze("BTCUSD", "intraday", "normal", hub)
    if base["side"] == "neutral":
        return
    for s in (0.9, -0.9):
        other = strat.analyze("BTCUSD", "intraday", "normal", hub, sentiment=s)
        assert other["confidence"] == base["confidence"]  # zero signal impact
        assert any("Headline sentiment" in r for r in other["reasons"])
        assert not any("Headline sentiment" in r for r in base["reasons"])


def test_scoring_path_still_works_when_enabled(monkeypatch):
    import app.constants as constants
    monkeypatch.setattr(constants, "CONFLUENCE_SCORING", True)
    hub = _fixture_hub()
    base = strat.analyze("BTCUSD", "intraday", "normal", hub)
    if base["side"] == "neutral":
        return
    up = strat.analyze("BTCUSD", "intraday", "normal", hub, sentiment=0.9)
    assert up["confidence"] != base["confidence"]
    assert abs(up["confidence"] - base["confidence"]) <= 8.0

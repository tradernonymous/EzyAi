"""Phase-2 tests: per-mode thresholds, weighted confidence model, HTF confirm
gate, cross-asset tilt, and the ranked scanner. Synthetic data only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402
from app.data.provider import SyntheticProvider, validate_candles  # noqa: E402
from app.signals import engine as signal_engine  # noqa: E402
from app.signals.autopilot import AutoPilot  # noqa: E402


def _candles(closes):
    return [{"open": c, "high": c * 1.002, "low": c * 0.998, "close": c,
             "volume": 10.0, "ts": i * 60} for i, c in enumerate(closes)]


def _rising(n=150, start=100.0, slope=0.5):
    return _candles([start + i * slope for i in range(n)])


def _falling(n=150, start=200.0, slope=-0.3):
    return _candles([start + i * slope for i in range(n)])


class StubHub:
    """Hand-built series per (symbol, interval): full control of direction."""

    def __init__(self, series):
        self.series = series  # {(symbol, tf): [candles]}
        self.mode = "live"

    def fetch_klines_ex(self, symbol, interval, limit=200):
        candles = self.series[(symbol, interval)]
        return candles[-limit:], "live"

    def fetch_klines(self, symbol, interval, limit=200):
        return self.fetch_klines_ex(symbol, interval, limit)[0]

    def classify(self, symbol):
        return "crypto"


class OfflineHub:
    def __init__(self):
        self.demo = SyntheticProvider(constants.ALL_UNIVERSE)
        self.mode = "demo"
        self.allow_demo = True
        self.random_calls = 0

    def fetch_klines_ex(self, symbol, interval, limit=200):
        return validate_candles(
            self.demo.fetch_klines(symbol, interval, limit)), "demo"

    def fetch_klines(self, symbol, interval, limit=200):
        return self.fetch_klines_ex(symbol, interval, limit)[0]

    def random_symbol(self, kind=None, exclude=()):
        self.random_calls += 1
        pool = [s for s in constants.ALL_UNIVERSE if s not in exclude]
        return pool[hash(tuple(exclude)) % len(pool)]

    def classify(self, symbol):
        return "crypto"


def _base_hub_up():
    return StubHub({
        ("ETHUSD", "5m"): _rising(),
        ("ETHUSD", "15m"): _rising(),
        ("ETHUSD", "1h"): _rising(),
        ("BTCUSD", "1h"): _rising(),
    })


def test_thresholds_table_covered_and_ordered():
    for style in constants.STYLES:
        t = constants.SIGNAL_THRESHOLDS[style]
        assert set(t) == {"safe", "normal", "aggressive"}, style
        assert t["safe"] > t["normal"] > t["aggressive"], style


def test_engine_uses_per_mode_threshold_boundary():
    base = {
        "pair": "BTCUSD", "side": "long", "style": "intraday", "mode": "normal",
        "base_tf": "15m", "reasons": ["x"], "exit_notes": [], "hold_horizon": "h",
        "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 1.0},
        "ind": {"macd_hist": 3.0}, "data_mode": "demo",
    }
    gate = constants.SIGNAL_THRESHOLDS["intraday"]["normal"]
    assert signal_engine.evaluate(dict(base, confidence=gate - 0.1)) is None
    assert signal_engine.evaluate(dict(base, confidence=gate)) is not None
    # aggressive mode drops the gate, so the same confidence now passes
    aggr = dict(base, mode="aggressive",
                confidence=constants.SIGNAL_THRESHOLDS["intraday"]["aggressive"])
    assert signal_engine.evaluate(aggr) is not None


def _ctx(side="long", cdx="up", cadex=25.0, conf_over=None):
    b = {"ema21": 100.0, "ema50": 90.0, "adx": 30.0, "macd_hist": 0.4,
         "rsi": 55.0, "bb_mid": 99.0, "stoch_k": 60.0, "close": 100.0,
         "atr": 0.5}
    ctx = {
        "base": b, "confirm": (cdx, cadex), "confirm_tf": "1h",
        "levels": ([95.0], []), "vol_ratio": 1.1, "session": "open",
        "base_tf": "15m", "cross": 2.5,
    }
    if conf_over:
        ctx.update(conf_over)
    return ctx


def test_score_upper_bound_sums_to_100():
    reasons = []
    s = strat._score("long", _ctx(), constants.SIGNAL_GATES["intraday"], reasons)
    assert 0 <= s <= 100
    # best-case inputs reach the 100 cap (weights sum exactly to 100); use
    # scalping gates so adx=30 clears adx_min=25 for the full momentum block
    best = _ctx(conf_over={"cross": 5.0, "levels": ([99.9], [])})
    best["confirm"] = ("up", 25.0)
    assert strat._score("long", best, constants.SIGNAL_GATES["scalping"], []) == 100


def test_score_flat_confirm_partial():
    base = strat._score("long", _ctx(cdx="neutral"),
                        constants.SIGNAL_GATES["intraday"], [])
    agree = strat._score("long", _ctx(cdx="up"),
                         constants.SIGNAL_GATES["intraday"], [])
    assert base < agree  # flat HTF scores a partial vs a confirmed read


def test_score_dead_volatility_penalty():
    dead = strat._score("long", _ctx(conf_over={"vol_ratio": 0.1}),
                        constants.SIGNAL_GATES["intraday"], [])
    healthy = strat._score("long", _ctx(conf_over={"vol_ratio": 1.1}),
                           constants.SIGNAL_GATES["intraday"], [])
    assert healthy > dead


def test_analyze_htf_conflict_turns_signal_neutral():
    hub = StubHub({
        ("ETHUSD", "5m"): _rising(),
        ("ETHUSD", "15m"): _rising(),
        ("ETHUSD", "1h"): _falling(),  # confirm opposes the base trend
        ("BTCUSD", "1h"): _rising(),
    })
    a = strat.analyze("ETHUSD", "scalping", "normal", hub)
    assert a["side"] == "neutral"
    assert a["spec"] is None
    assert a["confirm"]["direction"] in ("up", "down")
    assert any("opposes" in r for r in a["reasons"])


def test_analyze_htf_agreement_scores_signal():
    hub = _base_hub_up()
    a = strat.analyze("ETHUSD", "scalping", "normal", hub)
    assert a["side"] == "long"
    assert a["spec"] is not None
    assert a["confirm"]["agree"] is True


def test_cross_score_btc_agreement_and_conflict():
    hub = _base_hub_up()
    agree = strat._cross_score("long", ("up", 25.0), "BTCUSD", "1h", hub)
    assert agree == 5.0
    conflict = strat._cross_score("long", ("down", 25.0), "BTCUSD", "1h", hub)
    assert conflict == 0.0
    flat = strat._cross_score("long", ("neutral", None), "BTCUSD", "1h", hub)
    assert flat == 2.5


def test_cross_score_non_crypto_neutral():
    class FXHub(StubHub):
        def classify(self, symbol):
            return "forex"

    hub = FXHub({})
    assert strat._cross_score("long", ("up", 25.0), "EURUSD", "1h", hub) == 2.5


def test_cross_score_crypto_other_major_uses_btc():
    hub = _base_hub_up()
    assert strat._cross_score("long", ("up", 25.0), "ETHUSD", "1h", hub) == 5.0


def test_scanner_best_candidate_and_daily_cap():
    hub = OfflineHub()
    pilot = AutoPilot(hub, 42, "intraday", "normal", batch=5)
    counters = {}
    hits = 0
    best_conf = 0.0
    for _ in range(30):
        sig, err = pilot.run(counters)
        if err:
            break
        if sig is not None:
            hits += 1
            best_conf = max(best_conf, sig["confidence"])
            gate = constants.SIGNAL_THRESHOLDS["intraday"]["normal"]
            assert sig["confidence"] >= gate
    assert hits <= constants.MODE_PROFILE["normal"]["daily_limit"]
    assert best_conf > 0


def test_scanner_never_uses_random_symbol():
    hub = OfflineHub()
    pilot = AutoPilot(hub, 7, "swing", "safe", batch=3)
    counters = {}
    for _ in range(10):
        pilot.run(counters)
    assert hub.random_calls == 0


def test_scanner_deterministic_per_chat():
    hub = OfflineHub()
    a = AutoPilot(hub, 99, "intraday", "normal")
    b = AutoPilot(hub, 99, "intraday", "normal")
    c = AutoPilot(hub, 100, "intraday", "normal")
    assert a._order == b._order
    assert a._order != c._order


def test_scanner_rotates_without_repeating_recent():
    hub = OfflineHub()
    pilot = AutoPilot(hub, 5, "intraday", "normal", batch=3)
    seen = set()
    for _ in range(3):
        window = pilot._slice(3)
        for p in window:
            assert p not in seen or p in set(pilot.recent)
        seen.update(window)
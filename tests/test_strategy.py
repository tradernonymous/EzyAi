"""Strategy and engine tests. The hub below serves synthetic candles only:
these tests never touch Binance or Yahoo, so they are deterministic and
safe to run in CI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402
from app.data.provider import SyntheticProvider, validate_candles  # noqa: E402
from app.signals import engine as signal_engine  # noqa: E402
from app.signals.autopilot import AutoPilot  # noqa: E402


class OfflineHub:
    """Synthetic-only stand-in for DataHub with the same read surface."""

    def __init__(self):
        self.demo = SyntheticProvider(constants.ALL_UNIVERSE)
        self.mode = "demo"
        self.allow_demo = True

    def fetch_klines_ex(self, symbol, interval, limit=200):
        return validate_candles(self.demo.fetch_klines(symbol, interval, limit)), "demo"

    def fetch_klines(self, symbol, interval, limit=200):
        return self.fetch_klines_ex(symbol, interval, limit)[0]

    def random_symbol(self, kind=None, exclude=()):
        pool = [s for s in constants.ALL_UNIVERSE if s not in exclude]
        return pool[hash(tuple(exclude)) % len(pool)]

    @staticmethod
    def classify(symbol):
        return "crypto"


def make_hub():
    return OfflineHub()


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
            assert a["data_mode"] == "demo"
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


def test_analyze_is_deterministic_offline():
    hub = make_hub()
    a = strat.analyze("ETHUSD", "swing", "safe", hub)
    b = strat.analyze("ETHUSD", "swing", "safe", hub)
    assert a["confidence"] == b["confidence"] and a["side"] == b["side"]


def _analysis(**over):
    base = {
        "pair": "BTCUSD", "side": "long", "style": "intraday", "mode": "normal",
        "base_tf": "15m", "confidence": 90.0,
        "reasons": ["test"], "exit_notes": ["x"], "hold_horizon": "hours",
        "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 1.0},
        "ind": {"macd_hist": 5.0}, "data_mode": "demo",
    }
    base.update(over)
    return base


def test_engine_accepts_high_confidence_long():
    sig = signal_engine.evaluate(_analysis(mode="safe", confidence=90.0))
    assert sig is not None and sig["side"] == "long"
    assert sig["sl"] == 95 and sig["tp1"] == 110


def test_engine_rejects_neutral():
    assert signal_engine.evaluate(_analysis(side="neutral", spec=None, confidence=5.0)) is None


def test_engine_gate_respects_confidence():
    assert signal_engine.evaluate(_analysis(confidence=1.0)) is None
    assert signal_engine.evaluate(_analysis(confidence=80.0)) is not None


def test_engine_rejects_degenerate_spec():
    # ATR 0 on a halted instrument: SL == entry == TP must never alert.
    flat = {"market": 100, "limit": 100, "zone_low": 100, "zone_high": 100,
            "sl": 100, "tp1": 100, "tp2": 100, "rr": 2.0, "risk_pct": 1.0}
    assert signal_engine.evaluate(_analysis(spec=flat)) is None


def test_engine_safe_requires_macd_confirmation():
    a = _analysis(mode="safe", ind={"macd_hist": -3.0}, confidence=90.0)
    assert signal_engine.evaluate(a) is None
    a["ind"]["macd_hist"] = 3.0
    assert signal_engine.evaluate(a) is not None


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
    assert hits <= constants.MODE_PROFILE["normal"]["daily_limit"]


def test_autopilot_marks_run_before_fetch():
    hub = make_hub()

    class Boom(OfflineHub):
        def random_symbol(self, kind=None, exclude=()):
            raise IOError("feed down")

    pilot = AutoPilot(Boom(), 1, "intraday", "normal")
    try:
        pilot.run({})
    except IOError:
        pass
    # a failing feed backs off to the normal cadence instead of retrying
    # on every 30 s tick
    assert pilot.last_run > 0

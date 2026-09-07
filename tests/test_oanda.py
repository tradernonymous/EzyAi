"""OANDA real-time feed: FX & metals tier REALTIME and can scalp.

The whole feature keys off OANDA_API_KEY being present: without it the
behaviour is byte-identical to before (delayed tier, no scalping on FX/XAU).
Proxy stamping on candles is exercised end-to-end through DataHub so the
quality gate sees exactly what production will see.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import quality  # noqa: E402
from app.data.provider import (  # noqa: E402
    DataHub, OandaProvider, make_candle, oanda_instrument, spread_context)


# -- mapping & parsing --------------------------------------------------------

def test_instrument_mapping():
    assert OandaProvider.instrument("EURUSD") == "EUR_USD"
    assert OandaProvider.instrument("xauusd") == "XAU_USD"
    assert OandaProvider.instrument("WTI") is None
    assert OandaProvider.instrument("BTCUSD") is None  # crypto never routed


def test_granularity_map():
    g = OandaProvider.granularity
    assert g("1m") == "M1" and g("5m") == "M5" and g("15m") == "M15"
    assert g("30m") == "M30" and g("1h") == "H1" and g("4h") == "H4"
    assert g("1d") == "D"


def test_parse_candles_drops_incomplete_keeps_bam():
    epoch = int(datetime(2026, 9, 7, 12, 0, 0,
                        tzinfo=timezone.utc).timestamp() * 1000)
    payload = {
        "instrument": "EUR_USD", "granularity": "M5",
        "candles": [
            {"time": "2026-09-07T12:00:00.000000000Z", "complete": True,
             "volume": 231,
             "mid": {"o": "1.08530", "h": "1.08544", "l": "1.08510",
                     "c": "1.08531"},
             "bid": {"o": "1.08525", "h": "1.08539", "l": "1.08505",
                     "c": "1.08526"},
             "ask": {"o": "1.08535", "h": "1.08549", "l": "1.08515",
                     "c": "1.08536"}},
            {"time": "2026-09-07T12:05:00.123000000Z", "complete": False,
             "volume": 7,
             "mid": {"o": "1.08531", "h": "1.08540", "l": "1.08526",
                     "c": "1.08533"},
             "bid": {"o": "1.08526", "h": "1.08535", "l": "1.08521",
                     "c": "1.08528"},
             "ask": {"o": "1.08536", "h": "1.08545", "l": "1.08531",
                     "c": "1.08538"}},
            {"time": "2026-09-07T12:10:00.000000000Z", "complete": True,
             "volume": 0, "mid": None},  # no mid -> dropped
        ],
    }
    # the in-progress bar is dropped by default: a half-formed 5m ATR and a
    # still-moving entry level are not tradeable input (1B).
    cs = OandaProvider.parse_candles(payload)
    assert len(cs) == 1
    assert cs[0]["ts"] == epoch
    assert cs[0]["open"] == 1.08530 and cs[0]["complete"] is True
    assert cs[0]["mid"] == {"o": 1.08530, "h": 1.08544, "l": 1.08510,
                            "c": 1.08531}
    assert cs[0]["bid"]["c"] == 1.08526 and cs[0]["ask"]["c"] == 1.08536
    # the caller that wants them can keep in-progress bars
    cs2 = OandaProvider.parse_candles(payload, include_incomplete=True)
    assert len(cs2) == 2
    assert cs2[1]["ts"] == epoch + 5 * 60 * 1000 + 123
    assert cs2[1]["complete"] is False


def test_parse_candles_require_bam_raises_when_bid_ask_missing():
    payload = {"candles": [
        {"time": "2026-09-07T12:00:00.000000000Z", "complete": True,
         "volume": 100,
         "mid": {"o": "1.08530", "h": "1.08544", "l": "1.08510",
                 "c": "1.08531"}}]}
    with pytest.raises(ValueError, match="bid/ask"):
        OandaProvider.parse_candles(payload, require_bam=True)
    # diagnostic/probe callers may degrade (mid-only) to report *why*
    cs = OandaProvider.parse_candles(payload)
    assert len(cs) == 1 and cs[0]["bid"] is None and cs[0]["ask"] is None


def test_spread_context_from_bam_series():
    epoch = int(datetime(2026, 9, 7, 12, 0, 0,
                        tzinfo=timezone.utc).timestamp() * 1000)
    candles = []
    for i, pips in enumerate((2, 3, 2, 3)):  # 2,3,2,3 pips
        bid = 1.0850 + i / 10000
        ask = bid + pips / 100000
        candles.append({
            "ts": epoch + i * 300000, "open": bid, "high": ask,
            "low": bid, "close": bid, "volume": 1.0, "complete": True,
            "mid": {"o": bid, "h": ask, "l": bid, "c": bid},
            "bid": {"o": bid, "h": bid + 0.5 / 100000, "l": bid - 0.5 / 100000,
                    "c": bid},
            "ask": {"o": ask, "h": ask + 0.5 / 100000, "l": ask - 0.5 / 100000,
                    "c": ask},
        })
    sc = spread_context(candles)
    assert sc["latest"] == pytest.approx(3.0 / 100000, abs=1e-8)
    assert sc["median"] == pytest.approx(2.5 / 100000, abs=1e-8)  # sorted 2,2,3,3
    assert sc["last_bid"] == pytest.approx(1.0853, abs=1e-8)
    assert sc["last_ask"] == pytest.approx(1.0853 + 3.0 / 100000, abs=1e-8)
    assert spread_context([]) is None
    assert spread_context([make_candle(1, 1.0, 1.1, 0.9, 1.05, 3.0)]) is None


# -- tiering ------------------------------------------------------------------

def test_oanda_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    assert oanda_instrument("EURUSD") is None
    assert quality.static_tier("EURUSD") is quality.Tier.DELAYED
    assert quality.style_allowed("EURUSD", "scalping") is False


def test_oanda_enables_realtime_and_scalping(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    assert oanda_instrument("EURUSD") == "EUR_USD"
    assert quality.static_tier("EURUSD") is quality.Tier.REALTIME
    assert quality.static_tier("XAUUSD") is quality.Tier.REALTIME
    assert quality.static_tier("WTI") is quality.Tier.DELAYED  # no OANDA
    assert quality.static_tier("AAPL") is quality.Tier.DELAYED
    assert quality.style_allowed("EURUSD", "scalping") is True
    assert quality.style_allowed("XAUUSD", "scalping") is True
    from app import ui
    kb = ui.style_keyboard("analyze", "EURUSD")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("scalping" in x.lower() for x in labels)


def test_runtime_tier_oanda_source(monkeypatch):
    assert quality.runtime_tier("oanda", "live") is quality.Tier.REALTIME
    assert quality.runtime_tier("oanda", "demo") is quality.Tier.SYNTHETIC
    assert quality.may_emit("EURUSD", "scalping", source="oanda")[0] is True
    assert quality.may_emit("EURUSD", "scalping", source="yahoo")[0] is False


# -- DataHub integration ------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _payload(ts):
    stamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f000Z")
    return {"candles": [
        {"time": stamp, "complete": True, "volume": 100,
         "mid": {"o": "1.0000", "h": "1.0020", "l": "0.9990", "c": "1.0010"},
         "bid": {"o": "0.9999", "h": "1.0019", "l": "0.9989", "c": "1.0009"},
         "ask": {"o": "1.0001", "h": "1.0021", "l": "0.9991", "c": "1.0011"}}]}


def test_datahub_fetches_via_oanda_and_stamps(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    hub = DataHub()
    ts = int(time.time() * 1000)
    monkeypatch.setattr(hub.oanda.session, "get",
                        lambda *a, **k: _FakeResp(_payload(ts)))
    candles, mode = hub.fetch_klines_ex("EURUSD", "5m", 20)
    assert mode == "live"
    assert candles[-1]["source"] == "oanda"
    assert candles[0]["ts"] == ts
    assert hub.partner("EURUSD") is hub.oanda


def test_datahub_fetches_metals_via_oanda_display_symbol(monkeypatch):
    """Regression: resolve() maps XAUUSD to the Yahoo ticker GC=F, which
    OANDA does not know. The hub must still request the OANDA instrument."""
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    hub = DataHub()
    ts = int(time.time() * 1000)

    seen = {}
    def fake_get(path, params=None, **kw):
        seen["path"] = path
        seen["price"] = params.get("price")
        return _FakeResp(_payload(ts))

    monkeypatch.setattr(hub.oanda.session, "get", fake_get)
    candles, mode = hub.fetch_klines_ex("XAUUSD", "5m", 20)
    assert mode == "live"
    assert seen["path"].endswith("/instruments/XAU_USD/candles")
    assert seen["price"] == "BAM"
    assert candles[-1]["source"] == "oanda"
    assert candles[-1]["ask"]["c"] == 1.0011


def test_datahub_oanda_midonly_raises_and_falls_back(monkeypatch):
    """A BAM response missing bid/ask must not silently scalp with mid data:
    it raises inside the oanda path and the hub falls back to the delayed
    Yahoo feed, which the emit gate then blocks for scalping."""
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    hub = DataHub()
    ts = int(time.time() * 1000)

    payload = {"candles": [
        {"time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%S.%f000Z"),
         "complete": True, "volume": 100,
         "mid": {"o": "1.00", "h": "1.01", "l": "0.99", "c": "1.005"}}]}
    monkeypatch.setattr(hub.oanda.session, "get",
                        lambda *a, **k: _FakeResp(payload))
    fake = [make_candle(ts - 5 * 60 * 1000, 1.0, 1.2, 0.9, 1.1, 5.0)]
    monkeypatch.setattr(hub.forex, "fetch_klines",
                        lambda *a, **k: list(fake))
    candles, mode = hub.fetch_klines_ex("EURUSD", "15m", 100)
    assert mode == "live"
    assert candles[-1]["source"] == "yahoo"
    ok, why = quality.may_emit("EURUSD", "scalping",
                               source=candles[-1]["source"])
    assert ok is False and "real-time" in why


def test_datahub_falls_back_to_yahoo_stamp(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    hub = DataHub()

    def boom(*a, **k):
        raise RuntimeError("oanda down")

    monkeypatch.setattr(hub.oanda, "_get", boom)
    fake = [make_candle(int(time.time() * 1000) - 5 * 60 * 1000,
                        1.0, 1.2, 0.9, 1.1, 5.0)]
    monkeypatch.setattr(hub.forex, "fetch_klines",
                        lambda *a, **k: list(fake))
    candles, mode = hub.fetch_klines_ex("EURUSD", "15m", 100)
    assert mode == "live"
    assert candles[-1]["source"] == "yahoo"
    # the degrade must be visible to the emit gate: scalping suppressed.
    ok, why = quality.may_emit("EURUSD", "scalping",
                               source=candles[-1]["source"])
    assert ok is False and "real-time" in why
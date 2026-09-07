"""Admin /verifyfeed: probe_pair + report for the OANDA feed diagnostic."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import probe as probe_mod  # noqa: E402
from app.data.provider import make_candle, stamp_source  # noqa: E402
from app.formatting import message as msg  # noqa: E402


class _FakeOanda:
    def __init__(self, ok=True):
        self.ok = ok
        self.rows = None

    def fetch_klines(self, inst, tf, limit=200):
        if not self.ok:
            raise RuntimeError("401 Unauthorized")
        now = int(time.time() * 1000)
        self.rows = [make_candle(now - 30_000, 1.08, 1.09, 1.07, 1.085, 10.0),
                     make_candle(now - 10_000, 1.085, 1.09, 1.08, 1.088,
                                 10.0)]
        return list(self.rows)


class _FakeHub:
    def __init__(self, oanda=None, served="yahoo", rows=None, fail=False):
        self.oanda = oanda
        self.served = served
        self.rows = rows
        self.fail = fail

    def fetch_klines_ex(self, pair, tf, limit=200):
        if self.fail:
            raise RuntimeError("all providers down")
        if self.oanda is not None and self.oanda.rows is not None:
            return stamp_source(list(self.oanda.rows), "oanda"), "live"
        rows = self.rows
        if rows is None:
            rows = [make_candle(int(time.time() * 1000) - 60_000,
                                1.0, 1.2, 0.9, 1.1, 5.0)]
        return stamp_source(list(rows), self.served), "live"


def _probe(hub, monkeypatch, key="test-key"):
    if key:
        monkeypatch.setenv("OANDA_API_KEY", key)
    else:
        monkeypatch.delenv("OANDA_API_KEY", raising=False)
    return probe_mod.probe_pair(hub, "EURUSD")


# -- probe ---------------------------------------------------------------------

def test_probe_healthy_oanda(monkeypatch):
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    p = _probe(_FakeHub(oanda=_FakeOanda(ok=True)), monkeypatch)
    assert p["oanda_enabled"] is True
    assert p["oanda_instrument"] == "EUR_USD"
    assert p["oanda_ok"] is True
    assert p["oanda_error"] is None
    assert p["served_by"] == "oanda"
    assert p["tier"] == "realtime"
    assert p["fresh_ok"] is True
    assert p["scalp_ok"] is True
    assert p["last_price"] == 1.088


def test_probe_oanda_down_falls_back_to_yahoo(monkeypatch):
    monkeypatch.setenv("OANDA_ENVIRONMENT", "live")
    p = _probe(_FakeHub(oanda=_FakeOanda(ok=False)), monkeypatch)
    assert p["oanda_ok"] is False
    assert "401" in p["oanda_error"]
    assert p["oanda_instrument"] == "EUR_USD"
    assert p["served_by"] == "yahoo"
    assert p["tier"] == "delayed"
    assert p["scalp_ok"] is False
    assert "real-time" in p["scalp_reason"]


def test_probe_oanda_disabled(monkeypatch):
    p = _probe(_FakeHub(), monkeypatch, key=None)
    assert p["oanda_enabled"] is False
    assert p["oanda_instrument"] is None
    assert p["oanda_ok"] is False
    assert p["served_by"] == "yahoo"
    assert p["tier"] == "delayed"


def test_probe_crypto_realtime(monkeypatch):
    p = probe_mod.probe_pair(_FakeHub(served="binance"), "BTCUSD")
    assert p["served_by"] == "binance"
    assert p["tier"] == "realtime"
    assert p["scalp_ok"] is True


def test_probe_all_down_records_error(monkeypatch):
    p = probe_mod.probe_pair(_FakeHub(oanda=_FakeOanda(ok=True), fail=True),
                             "EURUSD")
    assert p["probe_error"] and "all providers down" in p["probe_error"]
    assert p["served_by"] is None


# -- report --------------------------------------------------------------------

def test_report_healthy(monkeypatch):
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    p = _probe(_FakeHub(oanda=_FakeOanda(ok=True)), monkeypatch)
    t = msg.verify_feed_report(p)
    assert "FEED CHECK" in t
    assert "practice" in t
    assert "api-fxpractice.oanda.com/v3" in t
    assert "EUR_USD" in t
    assert "OANDA v3" in t
    assert "realtime tier" in t
    assert "scalping: allowed" in t


def test_report_fallback_honesty(monkeypatch):
    monkeypatch.setenv("OANDA_ENVIRONMENT", "live")
    p = _probe(_FakeHub(oanda=_FakeOanda(ok=False)), monkeypatch)
    t = msg.verify_feed_report(p)
    assert "direct probe" in t and "\u274c" in t
    assert "Yahoo (delayed)" in t
    assert "scalping: blocked" in t
    assert "showing the Yahoo fallback" in t


def test_report_disabled_hint(monkeypatch):
    p = _probe(_FakeHub(), monkeypatch, key=None)
    t = msg.verify_feed_report(p)
    assert "OANDA</b> off" in t
    assert "OANDA_API_KEY" in t
    assert "scalping: blocked" in t


def test_report_probe_error_shorts(monkeypatch):
    p = probe_mod.probe_pair(_FakeHub(oanda=_FakeOanda(ok=True), fail=True),
                             "EURUSD")
    t = msg.verify_feed_report(p)
    assert "feed probe failed: RuntimeError: all providers down" in t
    assert "scalping" not in t.lower()
"""Admin /verifyfeed: probe_pair + report.

The probe answers one question an admin actually has when a pair goes
quiet: is the feed broken, or is the market simply shut? So it reports the
serving provider and freshness alongside the pair's scalping window, and
the report has to keep those two apart. Nothing here touches the network.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import regime  # noqa: E402
from app.data import probe as probe_mod  # noqa: E402
from app.data.provider import make_candle, stamp_source  # noqa: E402
from app.formatting import message as msg  # noqa: E402


class _FakeHub:
    def __init__(self, served="yahoo", rows=None, fail=False):
        self.served = served
        self.rows = rows
        self.fail = fail

    def fetch_klines_ex(self, pair, tf, limit=200):
        if self.fail:
            raise RuntimeError("all providers down")
        rows = self.rows
        if rows is None:
            rows = [make_candle(int(time.time() * 1000) - 60_000,
                                1.0, 1.2, 0.9, 1.1, 5.0)]
        return stamp_source(list(rows), self.served), "live"


def _open_window(monkeypatch, in_window=True):
    monkeypatch.setattr(regime, "scalp_session", lambda pair, now=None: {
        "class": "fx_major", "in_window": in_window, "preferred": in_window,
        "windows": ((420, 1260),), "label": "the London/NY window"})
    monkeypatch.setattr(regime, "next_session_open",
                        lambda pair, now=None: 1_800_000_000.0)


# -- probe ---------------------------------------------------------------------

def test_probe_reports_the_serving_provider_and_tier(monkeypatch):
    _open_window(monkeypatch)
    p = probe_mod.probe_pair(_FakeHub(), "EURUSD")
    assert p["served_by"] == "yahoo"
    assert p["tier"] == "delayed"
    assert p["static_tier"] == "delayed"
    assert p["spread_bps"] == 2
    assert p["fresh_ok"] is True
    assert p["scalp_ok"] is True
    assert p["last_price"] == 1.1


def test_probe_separates_a_shut_window_from_a_broken_feed(monkeypatch):
    _open_window(monkeypatch, in_window=False)
    p = probe_mod.probe_pair(_FakeHub(), "EURUSD")
    # the feed itself is healthy ...
    assert p["served_by"] == "yahoo" and p["fresh_ok"] is True
    # ... it is the clock that blocks scalping, and the probe says when
    assert p["in_window"] is False
    assert p["scalp_ok"] is False
    assert "outside" in p["scalp_reason"]
    assert p["next_open"]


def test_probe_marks_a_stale_series(monkeypatch):
    _open_window(monkeypatch)
    old = [make_candle(int(time.time() * 1000) - 9 * 3600 * 1000,
                       1.0, 1.2, 0.9, 1.1, 5.0)]
    p = probe_mod.probe_pair(_FakeHub(rows=old), "EURUSD")
    assert p["fresh_ok"] is False
    assert p["fresh_reason"]


def test_probe_crypto_is_realtime_and_never_windowed():
    p = probe_mod.probe_pair(_FakeHub(served="binance"), "BTCUSD")
    assert p["served_by"] == "binance"
    assert p["tier"] == "realtime"
    assert p["static_tier"] == "realtime"
    assert p["scalp_class"] == "crypto"
    assert p["window_label"] is None
    assert p["scalp_ok"] is True


def test_probe_blocks_scalping_on_a_stock():
    p = probe_mod.probe_pair(_FakeHub(), "AAPL")
    assert p["scalp_class"] is None
    assert p["scalp_ok"] is False


def test_probe_flags_synthetic_data():
    p = probe_mod.probe_pair(_FakeHub(served="synthetic"), "BTCUSD")
    assert p["tier"] == "synthetic"
    assert p["scalp_ok"] is False
    assert "synthetic" in p["scalp_reason"]


def test_probe_all_down_records_error():
    p = probe_mod.probe_pair(_FakeHub(fail=True), "EURUSD")
    assert p["probe_error"] and "all providers down" in p["probe_error"]
    assert p["served_by"] is None


# -- report --------------------------------------------------------------------

def test_report_healthy(monkeypatch):
    _open_window(monkeypatch)
    t = msg.verify_feed_report(probe_mod.probe_pair(_FakeHub(), "EURUSD"))
    assert "FEED CHECK" in t
    assert "delayed</b> tier" in t
    assert "spread estimate 2 bps" in t
    assert "inside the London/NY window" in t
    assert "Yahoo (delayed)" in t
    assert "scalping: allowed" in t


def test_report_names_the_next_window_when_shut(monkeypatch):
    _open_window(monkeypatch, in_window=False)
    t = msg.verify_feed_report(probe_mod.probe_pair(_FakeHub(), "EURUSD"))
    assert "outside the London/NY window" in t
    assert "next open" in t
    assert "scalping: blocked" in t


def test_report_says_crypto_never_closes():
    t = msg.verify_feed_report(
        probe_mod.probe_pair(_FakeHub(served="binance"), "BTCUSD"))
    assert "24/7" in t
    assert "realtime tier" in t


def test_report_says_a_stock_is_not_scalped():
    t = msg.verify_feed_report(probe_mod.probe_pair(_FakeHub(), "AAPL"))
    assert "not scalped" in t
    assert "scalping: blocked" in t


def test_report_probe_error_shorts():
    p = probe_mod.probe_pair(_FakeHub(fail=True), "EURUSD")
    t = msg.verify_feed_report(p)
    assert "feed probe failed: RuntimeError: all providers down" in t
    assert "scalping:" not in t

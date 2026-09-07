"""Phase-1 signal outcome tracking: store, first-touch resolver, /stats.

Covers the brief's honesty rules: SL/TP resolution order, same-bar
ambiguity conservatism, TP1-then-TP2 laddering, time-based expiry only
when data actually covers the window, and degraded behaviour (feed
errors / short history) never fabricating an outcome.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.outcomes import OutcomeStore, Resolver  # noqa: E402

TF_15M = 900


def sig(pair="BTCUSD", side="long", style="intraday", mode="normal",
        entry=100.0, sl=90.0, tp1=120.0, tp2=140.0, rr=2.0, conf=70.0,
        ts=None, data_mode="live", spread_estimate=None):
    return {
        "pair": pair, "side": side, "style": style, "mode": mode, "tf": "15m",
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "confidence": conf, "reasons": ["EMA bull"], "data_mode": data_mode,
        "ts": ts if ts is not None else time.time(),
        "component_scores": {"rsi": 55.0, "ema21": 101.0, "none_key": None},
        "spread_estimate": spread_estimate,
    }


def test_record_persists_spread_estimate(tmp_path):
    store = _store(tmp_path)
    store.record(1, sig(spread_estimate=22), "watch")
    rows = store.query("SELECT spread_estimate FROM signals")
    assert rows[0]["spread_estimate"] == 22


def test_query_returns_rows_and_binds_params(tmp_path):
    store = _store(tmp_path)
    store.record(1, sig(), "watch")
    store.record(1, sig(pair="ETHUSD"), "watch")
    rows = store.query("SELECT pair FROM signals WHERE pair=?", ("ETHUSD",))
    assert [r["pair"] for r in rows] == ["ETHUSD"]


def candles(ts_ms, n, step_s=TF_15M, base=100.0, span=2.0):
    """Ascending bars drifting between base-span and base+span."""
    out = []
    for i in range(n):
        t = ts_ms + i * step_s * 1000
        o = base + span * (0.5 if i % 2 else -0.5)
        out.append({"ts": t, "open": o, "high": o + 0.5, "low": o - 0.5,
                    "close": o + 0.1, "volume": 1.0})
    return out


class FakeHub:
    def __init__(self, data=None, exc=None):
        self._data = data or []
        self._exc = exc

    def fetch_klines_ex(self, *a, **k):
        if self._exc:
            raise self._exc
        return self._data, "live"


def _store(tmp_path):
    return OutcomeStore(tmp_path / "signals.db")


def _run(resolver):
    asyncio.run(resolver.run())


# -- store -------------------------------------------------------------------

def test_record_and_open_roundtrip(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    id1 = store.record(111, sig(entry=100.0, ts=now - 3600), "watch")
    id2 = store.record(222, sig(side="short", pair="EURUSD", ts=now), "autopilot")
    open_rows = store.open_signals()
    assert [id1, id2] != [None, None]
    assert len(open_rows) == 2
    a, b = open_rows
    assert a["source"] == "watch" and a["chat_id"] == 111
    assert a["direction"] == "long" and a["style"] == "intraday"
    assert b["source"] == "autopilot" and b["pair"] == "EURUSD"
    assert b["direction"] == "short" and abs(b["entry"] - 100.0) < 1e-9
    assert b["rr_target"] == 2.0
    assert b["data_source"] == "live"
    # component_scores stored as JSON, None values dropped
    import json
    scores = json.loads(a["component_scores"])
    assert scores["rsi"] == 55.0 and "none_key" not in scores


def test_stats_empty_and_after_count(tmp_path):
    store = _store(tmp_path)
    s = store.stats()
    assert s["total"] == 0 and s["win_rate_pct"] is None
    for i in range(3):
        store.record(111, sig(pair=f"P{i}USD"))
    s = store.stats()
    assert s["total"] == 3 and s["open"] == 3 and s["resolved"] == 0


# -- resolver: first touch ----------------------------------------------------

def _long_candles(ts_ms, n=10):
    bars = []
    for i in range(n):
        t = ts_ms + (i + 1) * TF_15M * 1000
        o = 88.0 + i * 1.5  # lows start under SL(90), rise away from it
        bars.append({"ts": t, "open": o, "high": o + 1.0, "low": o - 1.0,
                     "close": o, "volume": 1.0})
    return bars


def test_sl_touch_resolves_first(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    sid = store.record(1, sig(entry=100.0, sl=90.0, tp1=120.0, tp2=140.0,
                              ts=now, style="intraday"))
    # First bar lows at 87 -> SL before any TP print.
    bars = _long_candles(int(now * 1000))
    hub = FakeHub(bars)
    _run(Resolver(store, hub))
    row = store.open_signals()
    assert row == []
    assert store.count("status='sl'") == 1
    assert store.count("r_multiple=-1.0") == 1


def test_tp1_then_tp2_ladder(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(entry=100.0, sl=90.0, tp1=120.0, tp2=140.0,
                        ts=now, style="intraday"))
    bars = []
    for i, hi in enumerate((121.0, 150.0)):  # TP1 first, then ladder to TP2
        t = int(now * 1000) + (i + 1) * TF_15M * 1000
        bars.append({"ts": t, "open": 100.0, "high": hi, "low": 99.0,
                     "close": 100.0, "volume": 1.0})
    _run(Resolver(store, FakeHub(bars)))
    row = store.open_signals()
    assert row == []
    assert store.count("status='tp2'") == 1
    assert store.count("r_multiple=4.0") == 1  # 2 * rr


def test_tp1_locked_not_eroded_by_later_sl(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(entry=100.0, sl=90.0, tp1=120.0, tp2=140.0,
                        ts=now, style="intraday"))
    bars = [
        {"ts": int(now * 1000) + TF_15M * 1000, "open": 100.0, "high": 121.0,
         "low": 99.0, "close": 100.0, "volume": 1.0},  # TP1 booked
        {"ts": int(now * 1000) + 2 * TF_15M * 1000, "open": 100.0, "high": 95.0,
         "low": 85.0, "close": 90.0, "volume": 1.0},  # later SL attempt
    ]
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='tp1'") == 1
    assert store.count("r_multiple=2.0") == 1  # +rr kept


def test_same_bar_ambiguity_resolves_to_sl_and_flags(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(entry=100.0, sl=90.0, tp1=120.0, tp2=140.0,
                        ts=now, style="intraday"))
    bars = [{"ts": int(now * 1000) + TF_15M * 1000, "open": 100.0,
             "high": 125.0, "low": 88.0, "close": 100.0, "volume": 1.0}]
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='sl'") == 1
    assert store.count("same_candle_ambig=1") == 1


def test_short_mirror(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(side="short", entry=100.0, sl=110.0, tp1=80.0,
                        tp2=60.0, rr=2.0, ts=now, style="intraday"))
    bars = [{"ts": int(now * 1000) + TF_15M * 1000, "open": 101.0,
             "high": 112.0, "low": 99.0, "close": 100.0, "volume": 1.0}]
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='sl'") == 1 and store.count("r_multiple=-1.0") == 1


# -- resolver: expiry ----------------------------------------------------------

def test_expiry_on_style_window_uses_last_close(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    created = now - 37 * 3600  # intraday window is 36h
    store.record(1, sig(entry=100.0, sl=90.0, tp1=120.0, tp2=140.0,
                        rr=2.0, ts=created, style="intraday"))
    # Flat tape at 103.1: never prints 90 or 120; expires at last close.
    bars = candles(int(created * 1000), 150, span=0.0, base=103.0)
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='expired'") == 1
    with _store(tmp_path)._conn() as db:
        r = db.execute("SELECT r_multiple FROM signals "
                       "WHERE status='expired'").fetchone()[0]
    assert r == pytest.approx((103.1 - 100.0) / 10.0)


def test_young_signal_stays_open(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(ts=now - 60, style="swing"))
    bars = candles(int((now - 60) * 1000), 5, base=101.0)
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='open'") == 1


def test_truncated_history_cannot_expire(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    created = now - 37 * 3600
    store.record(1, sig(ts=created, style="intraday"))
    # Only 30 minutes of history after the window ran out.
    bars = candles(int(created * 1000), 2, base=103.0)
    _run(Resolver(store, FakeHub(bars)))
    assert store.count("status='open'") == 1


def test_feed_error_leaves_open(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(ts=now - 37 * 3600, style="intraday"))
    _run(Resolver(store, FakeHub(exc=RuntimeError("feed down"))))
    assert store.count("status='open'") == 1


def test_idempotent_resolution(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(1, sig(ts=now - 37 * 3600, style="intraday"))
    store.mark_resolved(1, "sl", 90.0, -1.0)
    store.mark_resolved(1, "tp2", 140.0, 4.0)  # second write must be ignored
    assert store.count("status='sl'") == 1 and store.count("r_multiple=-1.0") == 1


# -- stats -------------------------------------------------------------------

def test_stats_dashboard(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    # 3 longs on XAUUSD, 1 short on BTCUSD; resolved sl,sl,tp1
    ids = [store.record(1, sig(pair="XAUUSD", side="long", ts=now - 1000, conf=82.0)),
           store.record(1, sig(pair="XAUUSD", side="long", ts=now - 800, conf=78.0)),
           store.record(1, sig(pair="XAUUSD", side="long", ts=now - 600, conf=74.0)),
           store.record(1, sig(pair="BTCUSD", side="short", ts=now - 400, conf=40.0))]
    store.mark_resolved(ids[0], "sl", 90.0, -1.0)
    store.mark_resolved(ids[1], "sl", 90.0, -1.0)
    store.mark_resolved(ids[2], "tp1", 120.0, 2.0)
    # ids[3] stays open
    s = store.stats()
    assert s["total"] == 4 and s["open"] == 1 and s["resolved"] == 3
    assert s["avg_r"] == pytest.approx(0.0)  # (-1 -1 +2)/3
    assert s["win_rate_pct"] == pytest.approx(100.0 / 3)
    assert s["worst_streak"] == 2  # sl, sl
    by_pair = {r["k"]: r for r in s["by_pair"]}
    assert by_pair["XAUUSD"]["n"] == 3
    assert by_pair["BTCUSD"]["n"] == 1
    # confidence buckets: 74,78 -> 60-79 (n=2, both resolved); 82 -> 80-99
    # (n=1, resolved); 40 -> 20-39 (n=1, still open)
    b = {x["lo"]: x for x in s["conf_buckets"]}
    assert b[60]["n"] == 2 and b[60]["resolved"] == 2
    assert b[80]["n"] == 1 and b[80]["resolved"] == 1
    assert b[40]["n"] == 1 and b[40]["resolved"] == 0
    by_style = {r["k"]: r for r in s["by_style"]}
    assert by_style["intraday"]["n"] == 4


def test_signal_message_carries_data_mode_component(tmp_path):
    """The store persists the data_mode stamped on the delivered signal."""
    store = _store(tmp_path)
    # demo fallback can reach PRO users today (brief 1.4); it is recorded
    # verbatim so the anomaly is visible in /stats.
    store.record(1, sig(data_mode="demo"))
    assert store.count("data_source='demo'") == 1


def test_service_constructs_store_alongside_state(tmp_path):
    from app.signals.scheduler import Service
    svc = Service(object(), tmp_path / "state.json")
    assert svc.outcomes is not None and svc.resolver is not None
    assert (tmp_path / "signals.db").exists()
    svc.outcomes.record(1, sig())
    assert svc.outcomes.count() == 1
"""Freshness and trading-session gating.

Scalping on forex, stocks and CFDs used to analyse whatever bars Yahoo last
returned, so a watch could fire a signal at 03:00 on a Sunday off Friday's
closing candles. These tests pin the rules that stop that: a bar older than
its timeframe allows is stale, a shut venue is closed, and neither reaches
the signal path. All timestamps are synthetic, so nothing here touches the
network."""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.analysis import regime as rg  # noqa: E402
from app.formatting import message as msg  # noqa: E402
from app.signals import engine as signal_engine  # noqa: E402
from app.signals.scheduler import Service  # noqa: E402


def _utc(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _ms(iso):
    return int(_utc(iso).timestamp() * 1000)


# -- bar age -------------------------------------------------------------------

def test_bar_age_and_staleness_scale_with_timeframe():
    now = time.time()
    fresh = (now - 120) * 1000            # two minutes back
    two_hours = (now - 7200) * 1000
    assert rg.bar_age_s(fresh, now) == 120
    assert not rg.is_stale(fresh, "5m", now)
    # two hours is far too old to scalp, but ordinary for a daily bar
    assert rg.is_stale(two_hours, "5m", now)
    assert rg.is_stale(two_hours, "15m", now)
    assert not rg.is_stale(two_hours, "1d", now)


def test_daily_bars_clear_a_full_day():
    # daily bars print at 00:00 UTC, so late in the day they are ~24h old
    now = time.time()
    assert not rg.is_stale((now - 23 * 3600) * 1000, "1d", now)
    assert rg.is_stale((now - 60 * 3600) * 1000, "1d", now)


def test_unknown_interval_falls_back_to_a_limit():
    now = time.time()
    limit = constants.DEFAULT_MAX_BAR_AGE_S
    assert not rg.is_stale((now - limit + 60) * 1000, "7m", now)
    assert rg.is_stale((now - limit - 60) * 1000, "7m", now)


# -- sessions ------------------------------------------------------------------

def test_crypto_is_always_open():
    for iso in ("2026-09-05T03:00", "2026-09-06T12:00", "2026-09-07T09:00"):
        assert rg.session_state("crypto", _ms(iso)) == "open"
        assert rg.venue_open_now("crypto", _utc(iso)) is True


def test_saturday_closes_forex_and_cfd():
    sat = _ms("2026-09-05T12:00")
    assert rg.session_state("forex", sat) == "closed"
    assert rg.session_state("cfd", sat) == "closed"
    assert rg.session_state("stock", sat) == "closed"
    assert rg.venue_open_now("forex", _utc("2026-09-05T12:00")) is False


def test_forex_reopens_sunday_evening_and_shuts_friday_evening():
    # the FX week runs Sunday 17:00 to Friday 17:00 New York
    assert rg.session_state("forex", _ms("2026-09-06T20:00")) == "closed"  # 16:00 NY
    assert rg.session_state("forex", _ms("2026-09-06T22:00")) == "open"    # 18:00 NY
    assert rg.session_state("forex", _ms("2026-09-04T20:00")) == "open"    # Fri 16:00 NY
    assert rg.session_state("forex", _ms("2026-09-04T22:00")) == "closed"  # Fri 18:00 NY


def test_stock_session_is_dst_aware():
    # 14:00 UTC is 09:00 New York in January and 10:00 in July: shut, then open
    assert rg.session_state("stock", _ms("2026-01-14T14:00")) == "thin"
    assert rg.session_state("stock", _ms("2026-07-15T14:00")) == "open"
    assert rg.session_state("stock", _ms("2026-01-14T15:00")) == "open"


def test_venue_open_now_treats_thin_stocks_as_shut_but_not_thin_forex():
    # no extended-hours bars are fetched, so a thin stock cannot print one
    assert rg.venue_open_now("stock", _utc("2026-07-15T02:00")) is False
    assert rg.venue_open_now("stock", _utc("2026-07-15T14:00")) is True
    # thin FX hours still trade
    assert rg.session_state("forex", _ms("2026-09-07T03:00")) == "thin"
    assert rg.venue_open_now("forex", _utc("2026-09-07T03:00")) is True


# -- the signal path -----------------------------------------------------------

def _analysis(**over):
    base = {
        "pair": "EURUSD", "side": "long", "style": "scalping", "mode": "normal",
        "base_tf": "5m", "confidence": 95.0, "reasons": [], "exit_notes": [],
        "hold_horizon": "minutes", "levels": {"support": [], "resistance": []},
        "spec": {"market": 100, "limit": 99, "zone_low": 99, "zone_high": 100,
                 "sl": 95, "tp1": 110, "tp2": 120, "rr": 2.0, "risk_pct": 1.0},
        "ind": {"macd_hist": 5.0}, "data_mode": "live",
        "stale": False, "session": "open", "bar_age_s": 60.0, "kind": "forex",
    }
    base.update(over)
    return base


def test_fresh_open_market_still_signals():
    assert signal_engine.evaluate(_analysis()) is not None


def test_stale_or_closed_never_signals():
    assert signal_engine.evaluate(_analysis(stale=True)) is None
    assert signal_engine.evaluate(_analysis(session="closed")) is None
    # thin is tradeable: FX in Asian hours still fires
    assert signal_engine.evaluate(_analysis(session="thin")) is not None


def test_analyses_without_freshness_keys_are_unaffected():
    a = _analysis()
    del a["stale"], a["session"]
    assert signal_engine.evaluate(a) is not None


# -- scheduler skips shut venues before fetching -------------------------------

class _Hub:
    def __init__(self, kind):
        self.kind = kind

    def classify(self, pair):
        return self.kind


def _tick_calls(hub, tmp_path, open_now):
    svc = Service(hub, Path(tmp_path) / "state.json")
    svc.activate_pro(1, 1)
    svc.add_watch(1, "EURUSD", "scalping", "normal")
    calls = []

    def fake_qa(pair, style, mode, hub_):
        calls.append(pair)
        return {"pair": pair}, None

    async def send(*a, **k):
        raise AssertionError("must not send")

    import app.signals.engine as eng
    orig_qa, orig_open = eng.quick_analyze, rg.venue_open_now
    eng.quick_analyze = fake_qa
    rg.venue_open_now = lambda kind, now=None: open_now
    try:
        asyncio.run(svc.tick(send))
    finally:
        eng.quick_analyze, rg.venue_open_now = orig_qa, orig_open
    return calls


def test_closed_venue_costs_no_upstream_fetch(tmp_path):
    assert _tick_calls(_Hub("forex"), tmp_path, open_now=False) == []


def test_open_venue_is_analysed_normally(tmp_path):
    assert _tick_calls(_Hub("forex"), tmp_path, open_now=True) == ["EURUSD"]


def test_stub_hub_without_classify_is_not_skipped(tmp_path):
    # older tests pass a bare object() as the hub; the fetch must still run
    svc = Service(object(), tmp_path / "state.json")
    assert svc._venue_open("EURUSD") is True


# -- what the user is told -----------------------------------------------------

def test_report_names_a_shut_venue_and_a_stale_feed():
    closed = msg.data_state_line(_analysis(session="closed", bar_age_s=220000))
    assert "Market closed" in closed and "2d old" in closed
    stale = msg.data_state_line(_analysis(stale=True, bar_age_s=5400))
    assert "Stale feed" in stale and "1h old" in stale
    thin = msg.data_state_line(_analysis(session="thin", bar_age_s=120, kind="stock"))
    assert "cash session" in thin
    assert msg.data_state_line(_analysis()) == ""


def test_age_words_read_naturally():
    assert msg._age_words(30) == "just now"
    assert msg._age_words(600) == "10 min old"
    assert msg._age_words(7200) == "2h old"
    assert msg._age_words(200000) == "2d old"

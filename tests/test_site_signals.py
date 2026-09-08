"""Website live-signal board bridge.

The board is a shop window: it must mirror every autopilot signal, keep the
card moving until it closes, and never be able to delay or break the bot.
"""
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import site_signals as ss  # noqa: E402

KEY = "test-board-key"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("EZYAI_SIGNAL_KEY", KEY)
    monkeypatch.setenv("EZYMAP_SITE_URL", "https://example.test")
    monkeypatch.setattr(ss, "_no_key_logged", False)


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(monkeypatch, handler):
    """Record every request and answer it with `handler(request, n)`."""
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return handler(req, len(calls))

    monkeypatch.setattr(ss.urllib.request, "urlopen", fake)
    monkeypatch.setattr(ss.time, "sleep", lambda _s: None)
    return calls


def _http_error(code, body=b"nope"):
    import io
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


def signal(pair="XAUUSD", side="long", data_mode="live"):
    return {"pair": pair, "side": side, "style": "intraday", "mode": "normal",
            "tf": "15m", "entry": 100.0, "entry_zone": (99.5, 100.5),
            "sl": 95.0, "tp1": 110.0, "tp2": 120.0, "rr": 2.4,
            "risk_pct": 1.0, "confidence": 82.4, "reasons": ["EMA21 above EMA50"],
            "ts": 1_700_000_000.0, "data_mode": data_mode}


def row(**kw):
    base = {"id": 7, "source": "autopilot", "data_source": "live"}
    base.update(kw)
    return base


def _body(req):
    return json.loads(req.data)


def test_push_sends_bearer_key_and_json(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({"accepted": 1}))
    out = ss.push(external_id="ezyai-1", symbol="XAUUSD", direction="buy")
    assert out["ok"] and out["status"] == 200 and out["accepted"] == 1
    req = calls[0]
    assert req.full_url == "https://example.test/api/public/ezyai/signals"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert _body(req) == {"external_id": "ezyai-1", "symbol": "XAUUSD",
                          "direction": "buy"}


def test_push_drops_none_fields_so_a_partial_update_stays_partial(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    ss.tick("ezyai-1", 101.5)
    assert _body(calls[0]) == {"external_id": "ezyai-1", "last_price": 101.5}


def test_push_without_key_is_a_noop(monkeypatch):
    monkeypatch.delenv("EZYAI_SIGNAL_KEY")
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    out = ss.push(external_id="ezyai-1")
    assert not out["ok"] and not calls


def test_bad_status_and_direction_never_reach_the_site(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    assert not ss.push(external_id="x", status="closed")["ok"]
    assert not ss.push(external_id="x", direction="LONG")["ok"]
    assert not ss.push(symbol="XAUUSD")["ok"]
    assert not calls


def test_4xx_fails_fast(monkeypatch):
    def handler(req, n):
        raise _http_error(400, b"bad payload")

    calls = _urlopen(monkeypatch, handler)
    out = ss.push(external_id="ezyai-1")
    assert not out["ok"] and out["status"] == 400
    assert len(calls) == 1


def test_401_reports_what_the_site_is_checking(monkeypatch, caplog):
    def handler(req, n):
        raise _http_error(401, b"unauthorized")

    _urlopen(monkeypatch, handler)
    monkeypatch.setattr(ss, "diagnose", lambda: {"ok": True, "variable": "EZYAI_SIGNAL_KEY"})
    with caplog.at_level("WARNING"):
        assert not ss.push(external_id="ezyai-1")["ok"]
    assert "EZYAI_SIGNAL_KEY" in caplog.text
    assert KEY not in caplog.text


def test_5xx_and_429_retry_then_give_up(monkeypatch):
    def handler(req, n):
        raise _http_error(503)

    calls = _urlopen(monkeypatch, handler)
    assert not ss.push(external_id="ezyai-1")["ok"]
    assert len(calls) == ss.RETRIES

    def flaky(req, n):
        if n == 1:
            raise _http_error(429)
        return FakeResponse({})

    calls = _urlopen(monkeypatch, flaky)
    assert ss.push(external_id="ezyai-1")["ok"]
    assert len(calls) == 2


def test_a_dead_site_never_raises(monkeypatch):
    def handler(req, n):
        raise urllib.error.URLError("connection refused")

    _urlopen(monkeypatch, handler)
    assert ss.push(external_id="ezyai-1") == {
        "ok": False, "error": "URLError: <urlopen error connection refused>"}


def test_push_many_batches_and_caps_at_fifty(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({"accepted": 2}))
    assert ss.push_many([])["accepted"] == 0
    assert ss.push_many([{"external_id": f"e{i}"} for i in range(51)])["ok"] is False
    assert ss.push_many([{"external_id": "e1"}, {"external_id": "e2"}])["ok"]
    assert len(_body(calls[0])["signals"]) == 2
    # a malformed entry stops the whole batch before it is sent
    assert not ss.push_many([{"external_id": "e1"}, {"symbol": "X"}])["ok"]
    assert len(calls) == 1


def test_diagnose_needs_no_auth(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({"variable": "EZYAI_SIGNAL_KEY"}))
    out = ss.diagnose()
    assert out["ok"] and out["variable"] == "EZYAI_SIGNAL_KEY"
    assert calls[0].full_url.endswith("/signals?diagnose=1")
    assert "Authorization" not in calls[0].headers


def test_diagnose_survives_an_unreachable_site(monkeypatch):
    def handler(req, n):
        raise urllib.error.URLError("nope")

    _urlopen(monkeypatch, handler)
    assert ss.diagnose()["ok"] is False


def test_publish_signal_maps_every_card_field(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    ss.publish_signal("ezyai-7", signal(side="short")).join()
    sent = _body(calls[0])
    assert sent == {
        "external_id": "ezyai-7", "symbol": "XAUUSD", "direction": "sell",
        "status": "running", "entry_low": 99.5, "entry_high": 100.5,
        "stop_price": 95.0, "tp1": 110.0, "tp2": 120.0, "rr": 2.4,
        "setup_score": 82, "setup": "Intraday · Normal risk · 15m",
        "timeframe": "15m", "note": "EMA21 above EMA50",
        "opened_at": "2023-11-14T22:13:20+00:00"}


def test_demo_prices_never_reach_the_public_board(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    assert ss.publish_signal("ezyai-7", signal(data_mode="demo")) is None
    assert ss.publish_tick(row(data_source="demo"), 101.0) is None
    assert not calls


def test_only_autopilot_rows_are_ticked_and_closed(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    assert ss.publish_tick(row(source="watch"), 101.0) is None
    assert ss.publish_close(row(source="watch"), "tp1", 110.0, 2.4) is None
    assert not calls


def test_publish_tick_moves_the_rail(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    ss.publish_tick(row(), 101.25).join()
    assert _body(calls[0]) == {"external_id": "ezyai-7", "last_price": 101.25}


@pytest.mark.parametrize("status,expected,r", [
    ("tp1", "tp", 2.4), ("tp2", "tp", 4.8), ("sl", "sl", -1.0),
    # a time-based exit is neither a target nor a stop, and keeps its real R
    # so the site's performance figures still count it
    ("expired", "be", -0.4)])
def test_publish_close_maps_outcomes(monkeypatch, status, expected, r):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    ss.publish_close(row(), status, 110.0, r).join()
    assert _body(calls[0]) == {"external_id": "ezyai-7", "status": expected,
                               "result_r": r, "last_price": 110.0}


def test_publish_close_ignores_a_status_the_board_has_no_card_for(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    assert ss.publish_close(row(), "open", 110.0, 0.0) is None
    assert not calls


def test_a_slow_site_does_not_hold_up_the_caller(monkeypatch):
    def handler(req, n):
        time.sleep(0.4)
        return FakeResponse({})

    monkeypatch.setattr(ss.urllib.request, "urlopen", handler)
    started = time.monotonic()
    thread = ss.publish_tick(row(), 101.0)
    assert time.monotonic() - started < 0.2
    thread.join()


def test_a_push_that_explodes_never_reaches_the_bot(monkeypatch):
    def handler(req, n):
        raise RuntimeError("boom")

    _urlopen(monkeypatch, handler)
    assert not ss.push(external_id="ezyai-1")["ok"]
    ss.publish_tick(row(), 101.0).join()


def test_a_malformed_signal_never_raises_into_the_delivery_loop(monkeypatch):
    calls = _urlopen(monkeypatch, lambda req, n: FakeResponse({}))
    broken = signal()
    del broken["entry_zone"]
    assert ss.publish_signal("ezyai-7", broken) is None
    assert ss.publish_signal("ezyai-7", None) is None
    assert ss.publish_tick(object(), 101.0) is None
    assert ss.publish_close(row(id="nope"), "sl", 95.0, -1.0) is None
    assert not calls


# -- the resolver keeps the card moving ---------------------------------------

class FakeHub:
    def __init__(self, bars):
        self._bars = bars

    def fetch_klines_ex(self, *a, **k):
        return self._bars, "live"


def _bar(ts_ms, low, high, close):
    return {"ts": ts_ms, "open": close, "high": high, "low": low,
            "close": close, "volume": 1.0}


def _recorded(tmp_path, source="autopilot"):
    from app.outcomes import OutcomeStore
    store = OutcomeStore(tmp_path / "signals.db")
    sig = signal()
    sig["ts"] = time.time()
    store.record(1, sig, source)
    return store, sig


def _resolve(store, bars):
    import asyncio
    from app.outcomes import Resolver
    asyncio.run(Resolver(store, FakeHub(bars)).run())


def _pushes(monkeypatch):
    """Swap the two resolver hooks for recorders (the push itself is
    covered above; this pins down that the resolver calls them)."""
    seen = []
    monkeypatch.setattr(ss, "publish_tick",
                        lambda row, price: seen.append(("tick", row["id"], price)))
    monkeypatch.setattr(ss, "publish_close",
                        lambda row, status, px, r: seen.append(("close", status, r)))
    return seen


def test_resolver_ticks_an_open_card(tmp_path, monkeypatch):
    seen = _pushes(monkeypatch)
    store, sig = _recorded(tmp_path)
    ms = int(sig["ts"] * 1000) + 900_000
    _resolve(store, [_bar(ms, 99.0, 101.0, 100.5)])
    assert seen == [("tick", 1, 100.5)]
    assert store.count("status='open'") == 1


def test_resolver_closes_the_card_with_the_outcome_it_wrote(tmp_path, monkeypatch):
    seen = _pushes(monkeypatch)
    store, sig = _recorded(tmp_path)
    ms = int(sig["ts"] * 1000) + 900_000
    _resolve(store, [_bar(ms, 89.0, 91.0, 90.0)])
    assert seen == [("close", "sl", -1.0)]
    assert store.count("status='sl'") == 1


# -- the delivery loop hands autopilot signals to the board -------------------

def test_only_autopilot_alerts_are_mirrored_with_the_store_row_id(tmp_path,
                                                                  monkeypatch):
    import asyncio
    from app.bot import Bot
    from app.outcomes import OutcomeStore
    from app.signals.scheduler import Service

    svc = Service(object(), tmp_path / "state.json")
    svc.outcomes = OutcomeStore(tmp_path / "signals.db")
    bot = Bot("0" * 46, object(), svc)
    bot._sweep_tick = 1  # skip the periodic Telegram API check

    async def delivered(chat_id, text, **kw):
        return True

    published = []
    monkeypatch.setattr(bot, "_send_safe", delivered)
    monkeypatch.setattr(ss, "publish_signal",
                        lambda ext, sig: published.append((ext, sig["pair"])))

    async def tick(send):
        await send(1, signal(), source="watch")
        await send(2, signal(pair="COPPER"), source="autopilot")

    monkeypatch.setattr(svc, "tick", tick)
    asyncio.run(bot._job(None))

    # both are recorded locally; only the autopilot one reaches the board,
    # under the id of the row the bot just wrote for it
    assert svc.outcomes.count() == 2
    assert published == [("ezyai-2", "COPPER")]


def test_a_board_push_never_stops_a_delivery(tmp_path, monkeypatch):
    import asyncio
    from app.bot import Bot
    from app.outcomes import OutcomeStore
    from app.signals.scheduler import Service

    svc = Service(object(), tmp_path / "state.json")
    svc.outcomes = OutcomeStore(tmp_path / "signals.db")
    bot = Bot("0" * 46, object(), svc)
    bot._sweep_tick = 1
    sent = []

    async def delivered(chat_id, text, **kw):
        sent.append(chat_id)
        return True

    def boom(ext, sig):
        raise RuntimeError("website is down")

    monkeypatch.setattr(bot, "_send_safe", delivered)
    monkeypatch.setattr(ss, "publish_signal", boom)

    async def tick(send):
        await send(2, signal(), source="autopilot")
        await send(3, signal(), source="autopilot")

    monkeypatch.setattr(svc, "tick", tick)
    asyncio.run(bot._job(None))
    assert sent == [2, 3] and svc.outcomes.count() == 2

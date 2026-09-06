"""DataHub behaviour that used to be untested: symbol resolution, candle
validation, retry policy and the short-lived klines cache."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.data import provider as prov  # noqa: E402
from app.data.provider import DataHub, make_candle, validate_candles, with_retry  # noqa: E402


def _offline_hub(allow_demo=False):
    hub = DataHub(allow_demo=allow_demo)
    # never reach the network in tests
    hub.binance.validate = lambda s: False
    hub.stock.fetch_klines = lambda *a, **k: (_ for _ in ()).throw(IOError("offline"))
    return hub


def test_resolve_loose_returns_none_for_unknown():
    hub = _offline_hub()
    assert hub.resolve_loose("GARBAGE") is None
    # the old (None, None) tuple was truthy, so `if not resolve_loose(...)`
    # never fired and users saw "feed hiccup" instead of "unknown symbol"
    assert not hub.resolve_loose("GARBAGE")


def test_resolve_loose_rejects_junk_input_fast():
    hub = _offline_hub()
    assert hub.resolve_loose("") is None
    assert hub.resolve_loose("<b>x</b>") is None
    assert hub.resolve_loose("A" * 40) is None


def test_resolve_loose_known_symbols():
    hub = _offline_hub()
    assert hub.resolve_loose("btcusd") == (constants.KIND_CRYPTO, "BTCUSDT")
    assert hub.resolve_loose("EURUSD") == (constants.KIND_FOREX, "EURUSD")
    assert hub.resolve_loose("AAPL") == (constants.KIND_STOCK, "AAPL")


def test_validate_candles_sorts_dedupes_and_drops_bad_rows():
    rows = [make_candle(3000, 1, 2, 0.5, 1.5, 1), make_candle(1000, 1, 2, 0.5, 1.5, 1),
            make_candle(1000, 1, 2, 0.5, 1.6, 1), {"ts": 2000, "open": None,
                                                    "high": 1, "low": 1, "close": 1,
                                                    "volume": 0}]
    out = validate_candles(rows)
    assert [c["ts"] for c in out] == [1000, 3000]
    assert out[0]["close"] == 1.6  # last row for a timestamp wins


def test_validate_candles_refuses_empty():
    with pytest.raises(ValueError):
        validate_candles([])
    with pytest.raises(ValueError):
        validate_candles([{"ts": 1, "open": None, "high": None, "low": None,
                           "close": None, "volume": 0}])


class _Resp:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {"Retry-After": str(retry_after)} if retry_after else {}


class _HTTPError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"http {status}")
        self.response = _Resp(status, retry_after)


def test_with_retry_retries_transient_and_stops_on_client_error(monkeypatch):
    monkeypatch.setattr(prov.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _HTTPError(503)
        return "ok"

    assert with_retry(flaky, attempts=3) == "ok"
    assert len(calls) == 3

    def not_found():
        calls.append(2)
        raise _HTTPError(404)

    calls.clear()
    with pytest.raises(_HTTPError):
        with_retry(not_found, attempts=3)
    assert len(calls) == 1  # a 404 never heals by waiting


def test_with_retry_honours_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(prov.time, "sleep", waits.append)
    n = {"i": 0}

    def limited():
        n["i"] += 1
        if n["i"] == 1:
            raise _HTTPError(429, retry_after=2)
        return "ok"

    assert with_retry(limited, attempts=2) == "ok"
    assert waits and 2.0 <= waits[0] < 2.5


def test_klines_cache_dedupes_identical_fetches():
    hub = DataHub(allow_demo=True)
    calls = []
    real = hub.demo.fetch_klines

    def counting(symbol, interval, limit=200):
        calls.append(symbol)
        return real(symbol, interval, limit)

    hub.demo.fetch_klines = counting
    hub.binance.validate = lambda s: (_ for _ in ()).throw(IOError("offline"))
    # unknown-to-Binance crypto with demo enabled resolves to the demo feed
    a, mode = hub.fetch_klines_ex("BTCUSD", "15m", 50)
    assert mode == "demo" and len(a) == 50
    # demo results are not cached (they are synthetic anyway) but live ones are
    hub._cache_put(("k", "BTCUSD", "15m", 50), (a, "live"))
    b, mode_b = hub.fetch_klines_ex("BTCUSD", "15m", 50)
    assert mode_b == "live" and b is a


def test_ticker_carries_its_own_mode():
    hub = DataHub(allow_demo=True)
    hub.binance.validate = lambda s: (_ for _ in ()).throw(IOError("offline"))
    tick = hub.fetch_ticker("BTCUSD")
    assert tick["mode"] == "demo" and tick["symbol"] == "BTCUSD"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.data import provider as prov  # noqa: E402
from app.data.provider import CcxtProvider, DataHub  # noqa: E402


class _FakeExchange:
    name = "fake"
    ohlcv = [[1700000000000, 60000.0, 60500.0, 59500.0, 60200.0, 12.5],
             [1700000090000, 60200.0, 60700.0, 60000.0, 60600.0, 9.0]]
    ticker = {"last": 60600.0, "percentage": 1.5, "high": 60700.0,
              "low": 59500.0, "quoteVolume": 12345.0}

    def __init__(self, config=None):
        self.config = config or {}

    def load_markets(self):
        return {"BTC/USDT": {}, "ETH/USDT": {}}

    def fetch_ohlcv(self, symbol, timeframe=None, limit=None):
        assert symbol in self.load_markets()
        return list(self.ohlcv)

    def fetch_ticker(self, symbol):
        assert symbol in self.load_markets()
        return dict(self.ticker)


class _FakeCcxt:
    kraken = _FakeExchange
    coinbase = _FakeExchange


@pytest.fixture()
def fake_ccxt(monkeypatch):
    monkeypatch.setitem(sys.modules, "ccxt", _FakeCcxt)
    return _FakeCcxt


def test_symbol_mapping():
    assert CcxtProvider.to_ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert CcxtProvider.to_ccxt_symbol("ethusdt") == "ETH/USDT"
    assert CcxtProvider.to_ccxt_symbol("BTCUSDC") == "BTC/USDC"


def test_ccxt_import_is_lazy():
    sys.modules.pop("ccxt", None)
    import importlib
    importlib.reload(prov)
    assert "ccxt" not in sys.modules


def test_klines_fallback_to_ccxt(fake_ccxt, monkeypatch):
    hub = DataHub()
    monkeypatch.setattr(hub.binance, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    monkeypatch.setattr(hub.binance, "validate", lambda s: True)
    candles = hub.fetch_klines("BTCUSDT", "15m", 2)
    assert len(candles) == 2
    assert candles[-1]["close"] == 60600.0
    assert hub.mode == "live"


def test_ticker_fallback_to_ccxt(fake_ccxt, monkeypatch):
    hub = DataHub()
    monkeypatch.setattr(hub.binance, "fetch_ticker",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    monkeypatch.setattr(hub.binance, "validate", lambda s: True)
    tick = hub.fetch_ticker("BTCUSDT")
    assert tick["price"] == 60600.0
    assert tick["symbol"] == "BTCUSDT"
    assert hub.mode == "live"


def test_ccxt_failure_falls_to_demo(fake_ccxt, monkeypatch):
    hub = DataHub(allow_demo=True)
    monkeypatch.setattr(hub.binance, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    monkeypatch.setattr(hub.binance, "validate", lambda s: True)
    monkeypatch.setattr(hub.ccxt, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    candles = hub.fetch_klines("BTCUSDT", "15m", 5)
    assert len(candles) == 5
    assert hub.mode == "demo"


def test_ccxt_failure_raises_without_demo(fake_ccxt, monkeypatch):
    hub = DataHub()
    monkeypatch.setattr(hub.binance, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    monkeypatch.setattr(hub.binance, "validate", lambda s: True)
    monkeypatch.setattr(hub.ccxt, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    with pytest.raises(IOError):
        hub.fetch_klines("BTCUSDT", "15m", 5)


def test_non_crypto_skips_ccxt(monkeypatch):
    hub = DataHub()
    called = []
    monkeypatch.setattr(hub.ccxt, "fetch_klines",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(hub.stock, "fetch_klines",
                        lambda *a, **k: (_ for _ in ()).throw(IOError("down")))
    with pytest.raises(IOError):
        hub.fetch_klines("AAPL", "1d", 5)
    assert called == []

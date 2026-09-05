import random
import time

from .. import constants


def make_candle(ts, o, h, l, c, v):
    return {"ts": ts, "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v)}


class BinanceProvider:
    BASES = [
        "https://data-api.binance.vision",
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api.binance.us",
    ]

    def __init__(self, timeout=8):
        self.bases = self.BASES
        self.timeout = timeout
        self._http = None
        self._info = None
        self._info_ts = 0.0

    def _get(self, path, params=None):
        import requests
        if self._http is None:
            self._http = requests.Session()
            self._http.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EzyAiBot/1.0",
                "Accept": "application/json",
            })
        last_error = None
        for base in self.bases:
            try:
                r = self._http.get(base + path, params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_error = exc
                continue
        raise last_error

    def exchange_info(self):
        if self._info is None or time.time() - self._info_ts > 600:
            info = self._get("/api/v3/exchangeInfo")
            self._info = {s["symbol"] for s in info.get("symbols", [])}
            self._info_ts = time.time()
        return self._info

    def validate(self, symbol):
        return symbol in self.exchange_info()

    def fetch_klines(self, symbol, interval, limit=200):
        raw = self._get("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "limit": limit,
        })
        return [make_candle(int(k[0]), k[1], k[2], k[3], k[4], k[5]) for k in raw]

    def fetch_ticker(self, symbol):
        t = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        return {
            "price": float(t["lastPrice"]),
            "change_pct": float(t["priceChangePercent"]),
            "high": float(t["highPrice"]),
            "low": float(t["lowPrice"]),
            "volume": float(t["quoteVolume"]),
            "kind": constants.KIND_CRYPTO,
            "asset": symbol.replace("USDT", ""),
            "quote": "USDT",
        }


class YahooProvider:
    BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
    QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
    }
    RANGE_MAP = {
        "1m": ("1m", "1d"),
        "5m": ("5m", "5d"),
        "15m": ("15m", "1mo"),
        "30m": ("30m", "1mo"),
        "1h": ("1h", "3mo"),
        "4h": ("1h", "3mo"),
        "1d": ("1d", "1y"),
    }

    def __init__(self, timeout=10, kind=constants.KIND_STOCK):
        import requests
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.timeout = timeout
        self.kind = kind

    def _get(self, url, params=None):
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def resolve_symbol(self, symbol):
        if self.kind == constants.KIND_FOREX:
            return symbol if symbol.endswith("=X") else symbol + "=X"
        return symbol

    def fetch_klines(self, symbol, interval, limit=200):
        sym = self.resolve_symbol(symbol)
        y_interval, y_range = self.RANGE_MAP.get(
            interval, ("1d", "1y"))
        data = self._get(f"{self.BASE}/{sym}", {
            "interval": y_interval, "range": y_range,
            "includePrePost": "false",
            "events": "capitalGain%2Cdiv",
        })
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjclose = result["indicators"].get("adjclose", [{}])[0]
        candles = []
        for i, t in enumerate(ts):
            o = quote["open"][i]
            h = quote["high"][i]
            l = quote["low"][i]
            c = quote["close"][i]
            if c is None:
                continue
            if adjclose and adjclose.get("adjclose") and adjclose["adjclose"][i]:
                c = adjclose["adjclose"][i]
            candles.append(make_candle(
                int(t) * 1000,
                o if o is not None else c,
                h if h is not None else c,
                l if l is not None else c,
                c,
                quote["volume"][i] or 0.0,
            ))
        return candles[-limit:]

    def fetch_ticker(self, symbol, candles=None):
        sym = self.resolve_symbol(symbol)
        candles = candles or self.fetch_klines(sym, "1d", limit=5)
        last = candles[-1]
        prev = candles[0]["close"] if len(candles) > 1 else last["close"]
        chg = 0.0
        if prev:
            chg = (last["close"] - prev) / prev * 100
        return {
            "price": last["close"],
            "change_pct": chg,
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "volume": float(sum(c["volume"] for c in candles)),
            "kind": self.kind,
            "asset": symbol.replace("=X", ""),
            "quote": "USD" if self.kind == constants.KIND_FOREX else "USD",
        }

    def fetch_quote(self, symbols):
        import urllib.parse
        out = {}
        try:
            data = self._get(self.QUOTE, {"symbols": ",".join(symbols)})
            for q in data.get("quoteResponse", {}).get("result", []):
                if q.get("regularMarketPrice") is not None:
                    out[q["symbol"]] = q
        except Exception:
            pass
        return out


class SyntheticProvider:
    def __init__(self, seed_pairs=None):
        self.pairs = set(seed_pairs or [])

    @staticmethod
    def _seed(symbol, interval):
        import hashlib
        return int(hashlib.sha256((symbol + interval).encode()).hexdigest(), 16) & 0xFFFFFFFF

    def _base_price(self, symbol):
        mapping = {
            "BTC": 97000.0, "ETH": 3500.0, "SOL": 190.0, "XRP": 0.6,
            "ADA": 0.45, "DOGE": 0.16, "BNB": 620.0, "LINK": 18.0,
            "EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 149.0, "AUDUSD": 0.65,
            "AAPL": 210.0, "MSFT": 430.0, "GOOGL": 175.0, "AMZN": 185.0,
            "NVDA": 130.0, "TSLA": 250.0, "SPY": 550.0, "QQQ": 470.0,
        }
        key = symbol.replace("USDT", "").replace("=X", "")
        return mapping.get(key, 50.0 + self._seed(symbol, "x") % 450)

    def validate(self, symbol):
        return True

    def fetch_klines(self, symbol, interval, limit=200):
        rng = random.Random(self._seed(symbol, interval))
        step = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 300)
        price = self._base_price(symbol)
        now = int(time.time() * 1000)
        candles = []
        phase = rng.uniform(0, 6.28)
        for i in range(limit):
            ts = now - (limit - 1 - i) * step * 1000
            drift = rng.gauss(0, 0.0006)
            wave = 0.0009 * (1 + rng.random() * 2) * (
                1 if (phase + i * 0.02) % 6.28 < 3.14 else -1
            ) * min(1.0, i / max(limit * 0.2, 1))
            drift = drift + wave
            o = price
            c = price * (1 + drift) * (1 + rng.gauss(0, 0.0004))
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0003)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0003)))
            vol = rng.uniform(10, 100) * price
            candles.append(make_candle(ts, o, hi, lo, c, vol))
            price = c
        return candles

    def fetch_ticker(self, symbol, candles=None):
        candles = candles or self.fetch_klines(symbol, "1h", limit=24)
        last = candles[-1]["close"]
        prev = candles[0]["close"]
        chg = (last - prev) / prev * 100 if prev else 0.0
        asset = symbol.replace("USDT", "").replace("=X", "")
        return {
            "price": last,
            "change_pct": chg,
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "volume": float(sum(c["volume"] for c in candles)),
            "kind": None,
            "asset": asset,
            "quote": "USDT",
        }


class DataHub:
    def __init__(self, allow_demo=False):
        self.binance = BinanceProvider()
        self.forex = YahooProvider(kind=constants.KIND_FOREX)
        self.stock = YahooProvider(kind=constants.KIND_STOCK)
        self.demo = SyntheticProvider(constants.ALL_UNIVERSE)
        self.allow_demo = allow_demo
        self.mode = "live"

    @staticmethod
    def classify(symbol):
        s = symbol.upper()
        if s in constants.CRYPTO_UNIVERSE or s.endswith(("USDT", "USDC")):
            return constants.KIND_CRYPTO
        if s in constants.FX_UNIVERSE:
            return constants.KIND_FOREX
        if s in constants.STOCK_UNIVERSE:
            return constants.KIND_STOCK
        return None

    def resolve(self, symbol):
        kind = self.classify(symbol.upper())
        if kind == constants.KIND_CRYPTO:
            return kind, symbol.upper()
        if kind == constants.KIND_FOREX:
            return kind, symbol.upper()
        if kind == constants.KIND_STOCK:
            return kind, symbol.upper()
        return None, None

    def resolve_loose(self, symbol):
        kind, sym = self.resolve(symbol)
        if kind:
            return kind, sym
        try:
            if self.binance.validate(symbol.upper()):
                return constants.KIND_CRYPTO, symbol.upper()
        except Exception:
            pass
        try:
            self.stock.fetch_klines(symbol.upper(), "1d", 5)
            return constants.KIND_STOCK, symbol.upper()
        except Exception:
            pass
        if self.allow_demo:
            return constants.KIND_CRYPTO, symbol.upper()
        return None, None

    def partner(self, symbol):
        kind, sym = self.resolve(symbol)
        if kind == constants.KIND_CRYPTO:
            try:
                if self.binance.validate(sym):
                    return self.binance
            except Exception:
                pass
        elif kind == constants.KIND_FOREX:
            return self.forex
        elif kind == constants.KIND_STOCK:
            return self.stock
        if self.allow_demo:
            self.mode = "demo"
            return self.demo
        raise ValueError(f"Unknown symbol: {symbol}")

    def fetch_klines(self, symbol, interval, limit=200):
        kind, sym = self.resolve(symbol)
        try:
            partner = self.partner(symbol)
        except ValueError:
            if self.allow_demo:
                partner = self.demo
                self.mode = "demo"
            else:
                raise
        try:
            candles = partner.fetch_klines(sym, interval, limit)
            if partner is not self.demo:
                self.mode = "live"
            return candles
        except Exception:
            if self.allow_demo:
                self.mode = "demo"
                return self.demo.fetch_klines(sym, interval, limit)
            raise

    def fetch_ticker(self, symbol):
        kind, sym = self.resolve(symbol)
        try:
            partner = self.partner(symbol)
        except ValueError:
            partner = self.demo if self.allow_demo else None
            if partner is None:
                raise
        try:
            tick = partner.fetch_ticker(sym)
            if partner is not self.demo:
                self.mode = "live"
            else:
                self.mode = "demo"
            tick["symbol"] = symbol.upper()
            return tick
        except Exception:
            if self.allow_demo:
                self.mode = "demo"
                tick = self.demo.fetch_ticker(sym)
                tick["symbol"] = symbol.upper()
                return tick
            raise

    def random_symbol(self, kind=None, exclude=()):
        import random
        pool = constants.ALL_UNIVERSE
        if kind:
            if kind == constants.KIND_CRYPTO:
                pool = constants.CRYPTO_UNIVERSE
            elif kind == constants.KIND_FOREX:
                pool = list(constants.FX_UNIVERSE.keys())
            elif kind == constants.KIND_STOCK:
                pool = constants.STOCK_UNIVERSE
        available = [s for s in pool if s not in exclude]
        return random.choice(available or pool)
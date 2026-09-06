import logging
import random
import threading
import time

from .. import constants

logger = logging.getLogger(__name__)

LIVE = "live"
DEMO = "demo"


def make_candle(ts, o, h, l, c, v):
    return {"ts": ts, "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v)}


def _retry_after(exc):
    """Seconds an HTTP 429/5xx asks us to wait, or None for other errors."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status is None:
        return None
    if status == 429 or status >= 500:
        try:
            return max(0.5, float(resp.headers.get("Retry-After", 1)))
        except (TypeError, ValueError):
            return 1.0
    return None


def with_retry(fn, attempts=3, base_delay=0.6):
    """Call fn() with exponential backoff and jitter. Honours Retry-After on
    429/5xx; gives up immediately on other HTTP errors (404, 400) since
    those never heal by waiting."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            wait = _retry_after(exc)
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None)
            if status is not None and wait is None:
                raise  # 4xx other than 429: not transient
            if i == attempts - 1:
                break
            delay = wait if wait is not None else base_delay * (2 ** i)
            time.sleep(min(delay, 8.0) + random.uniform(0, 0.3))
    raise last


def validate_candles(candles, symbol=""):
    """Normalise a provider's candle list: drop rows with missing prices,
    sort by time, dedupe timestamps (last wins) and refuse empty results so
    the indicators never index into nothing."""
    by_ts = {}
    for c in candles or []:
        try:
            if any(c[k] is None for k in ("open", "high", "low", "close")):
                continue
            if c["close"] <= 0 or c["high"] < c["low"]:
                continue
            by_ts[int(c["ts"])] = c
        except (KeyError, TypeError, ValueError):
            continue
    out = [by_ts[k] for k in sorted(by_ts)]
    if not out:
        raise ValueError(f"no candles returned for {symbol or 'symbol'}")
    return out


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
                wait = _retry_after(exc)
                if wait is not None:
                    # Hammering the next host immediately after a 429 is how
                    # a rate limit turns into an IP ban.
                    time.sleep(min(wait, 3.0))
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
            "asset": constants.base_asset(symbol),
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
        def call():
            r = self.session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        return with_retry(call, attempts=3)

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


class CcxtProvider:
    """Public-only multi-exchange crypto fallback (no API key).

    Used only when every Binance host fails. ccxt is imported lazily
    inside the methods so the dependency never loads (and never costs
    RAM) during normal operation.
    """

    EXCHANGES = ("kraken", "coinbase")

    MARKETS_TTL = 3600

    def __init__(self, timeout=10):
        self.timeout = timeout
        self._exchanges = {}  # name -> (exchange, markets_loaded_at)
        self._lock = threading.Lock()

    @staticmethod
    def to_ccxt_symbol(symbol):
        if symbol.upper().endswith("USDC"):
            return constants.base_asset(symbol) + "/USDC"
        return constants.base_asset(symbol) + "/USDT"

    def _exchange(self, ccxt_mod, name):
        cls = getattr(ccxt_mod, name)
        return cls({"enableRateLimit": True, "timeout": self.timeout * 1000})

    def _try_each(self, op, symbol, interval=None, limit=None):
        import ccxt  # lazy: keeps the normal path light
        last_error = None
        ccxt_symbol = self.to_ccxt_symbol(symbol)
        for name in self.EXCHANGES:
            try:
                ex, markets = self._markets(ccxt, name)
                if ccxt_symbol not in markets:
                    continue
                if op == "ohlcv":
                    return ex.fetch_ohlcv(ccxt_symbol, timeframe=interval,
                                          limit=limit)
                return ex.fetch_ticker(ccxt_symbol)
            except Exception as exc:
                last_error = exc
                continue
        raise last_error if last_error else ValueError(
            f"No ccxt market: {symbol}")

    def _markets(self, ccxt_mod, name):
        """Exchange instance with markets loaded, reused for an hour. Loading
        the full market catalogue on every fallback call was a multi-MB
        download per tick during a Binance outage."""
        with self._lock:
            ex, markets, loaded = self._exchanges.get(name, (None, None, 0.0))
            if ex is None or time.time() - loaded > self.MARKETS_TTL:
                ex = self._exchange(ccxt_mod, name)
                markets = ex.load_markets() or {}
                self._exchanges[name] = (ex, markets, time.time())
            return ex, markets

    def fetch_klines(self, symbol, interval, limit=200):
        raw = self._try_each("ohlcv", symbol, interval, limit)
        return [make_candle(int(k[0]), k[1], k[2], k[3], k[4],
                            k[5] if k[5] is not None else 0.0)
                for k in raw[-limit:]]

    def fetch_ticker(self, symbol):
        t = self._try_each("ticker", symbol)
        last = t.get("last") or t.get("close")
        return {
            "price": float(last),
            "change_pct": float(t.get("percentage") or 0.0),
            "high": float(t.get("high") or last),
            "low": float(t.get("low") or last),
            "volume": float(t.get("quoteVolume") or t.get("baseVolume") or 0.0),
            "kind": constants.KIND_CRYPTO,
            "asset": constants.base_asset(symbol),
            "quote": "USDT",
        }


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
        key = constants.base_asset(symbol).replace("=X", "")
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
        asset = constants.base_asset(symbol).replace("=X", "")
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
        self.ccxt = CcxtProvider()
        self.forex = YahooProvider(kind=constants.KIND_FOREX)
        self.stock = YahooProvider(kind=constants.KIND_STOCK)
        self.cfd = YahooProvider(kind=constants.KIND_CFD)
        self.demo = SyntheticProvider(constants.ALL_UNIVERSE)
        self.allow_demo = allow_demo
        # Mode of the most recent fetch, for the dashboard's feed label only.
        # Per-request callers must use fetch_klines_ex / tick["mode"], since
        # this attribute is shared by every concurrent user.
        self.mode = LIVE
        self._cache = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def classify(symbol):
        s = symbol.upper()
        if s in constants.CRYPTO_UNIVERSE or s.endswith(("USDT", "USDC")):
            return constants.KIND_CRYPTO
        if s in constants.CFD_UNIVERSE:
            return constants.KIND_CFD
        if s in constants.FX_UNIVERSE:
            return constants.KIND_FOREX
        if s in constants.STOCK_UNIVERSE:
            return constants.KIND_STOCK
        return None

    def resolve(self, symbol):
        kind = self.classify(symbol.upper())
        if kind == constants.KIND_CRYPTO:
            return kind, constants.binance_symbol(symbol.upper())
        if kind == constants.KIND_CFD:
            return kind, constants.CFD_UNIVERSE[symbol.upper()]
        if kind == constants.KIND_FOREX:
            return kind, symbol.upper()
        if kind == constants.KIND_STOCK:
            return kind, symbol.upper()
        return None, None

    def resolve_loose(self, symbol):
        """(kind, venue_symbol) for known or discoverable symbols, else None.
        Discovery does live lookups (Binance exchangeInfo, Yahoo) and must
        be called off the event loop."""
        kind, sym = self.resolve(symbol)
        if kind:
            return kind, sym
        s = symbol.upper()
        if not s or len(s) > 24 or not s.replace("=", "").replace("-", "").isalnum():
            return None
        # friendly USD spelling for listings outside the universe
        if s.endswith("USD") and not s.endswith(("USDT", "USDC")):
            venue = constants.binance_symbol(s)
            try:
                if self.binance.validate(venue):
                    return constants.KIND_CRYPTO, venue
            except Exception:
                pass
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
        return None

    def partner(self, symbol):
        kind, sym = self.resolve(symbol)
        if kind == constants.KIND_CRYPTO:
            try:
                if self.binance.validate(sym):
                    return self.binance
            except Exception:
                pass
        elif kind == constants.KIND_CFD:
            return self.cfd
        elif kind == constants.KIND_FOREX:
            return self.forex
        elif kind == constants.KIND_STOCK:
            return self.stock
        if self.allow_demo:
            self.mode = DEMO
            return self.demo
        raise ValueError(f"Unknown symbol: {symbol}")

    @staticmethod
    def _cache_ttl(interval):
        # Ten users watching the same pair must not mean ten identical
        # upstream calls per tick. Short bars stay fresh, daily bars longer.
        secs = constants.INTERVALS.get(interval, 300)
        return max(15.0, min(secs / 4.0, 600.0))

    def _cache_get(self, key, ttl):
        with self._cache_lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    def _cache_put(self, key, value):
        with self._cache_lock:
            self._cache[key] = (time.time(), value)
            if len(self._cache) > 512:
                oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])
                for k, _ in oldest[:128]:
                    del self._cache[k]

    def fetch_klines_ex(self, symbol, interval, limit=200):
        """(candles, mode) where mode is LIVE or DEMO for this very fetch."""
        key = ("k", symbol.upper(), interval, limit)
        hit = self._cache_get(key, self._cache_ttl(interval))
        if hit is not None:
            return hit
        kind, sym = self.resolve(symbol)
        try:
            partner = self.partner(symbol)
        except ValueError:
            partner = None
        last_error = None
        if partner is not None:
            try:
                candles = validate_candles(partner.fetch_klines(sym, interval, limit), sym)
                mode = DEMO if partner is self.demo else LIVE
                self.mode = mode
                self._cache_put(key, (candles, mode))
                return candles, mode
            except Exception as exc:
                last_error = exc
                logger.warning("klines %s %s via %s failed: %s: %s", sym, interval,
                               type(partner).__name__, type(exc).__name__, exc)
        if kind == constants.KIND_CRYPTO:
            try:
                candles = validate_candles(self.ccxt.fetch_klines(sym, interval, limit), sym)
                self.mode = LIVE
                self._cache_put(key, (candles, LIVE))
                return candles, LIVE
            except Exception as exc:
                last_error = last_error or exc
                logger.warning("klines %s %s via ccxt failed: %s: %s", sym, interval,
                               type(exc).__name__, exc)
        if self.allow_demo:
            self.mode = DEMO
            return self.demo.fetch_klines(sym, interval, limit), DEMO
        if partner is None:
            raise ValueError(f"Unknown symbol: {symbol}")
        raise last_error

    def fetch_klines(self, symbol, interval, limit=200):
        return self.fetch_klines_ex(symbol, interval, limit)[0]

    def fetch_ticker(self, symbol):
        kind, sym = self.resolve(symbol)
        try:
            partner = self.partner(symbol)
        except ValueError:
            partner = None
        last_error = None
        if partner is not None:
            try:
                tick = partner.fetch_ticker(sym)
                tick["mode"] = DEMO if partner is self.demo else LIVE
                self.mode = tick["mode"]
                tick["symbol"] = symbol.upper()
                return tick
            except Exception as exc:
                last_error = exc
                logger.warning("ticker %s via %s failed: %s: %s", sym,
                               type(partner).__name__, type(exc).__name__, exc)
        if kind == constants.KIND_CRYPTO:
            try:
                tick = self.ccxt.fetch_ticker(sym)
                tick["mode"] = LIVE
                self.mode = LIVE
                tick["symbol"] = symbol.upper()
                return tick
            except Exception as exc:
                last_error = last_error or exc
                logger.warning("ticker %s via ccxt failed: %s: %s", sym,
                               type(exc).__name__, exc)
        if self.allow_demo:
            self.mode = DEMO
            tick = self.demo.fetch_ticker(sym)
            tick["mode"] = DEMO
            tick["symbol"] = symbol.upper()
            return tick
        if partner is None:
            raise ValueError(f"Unknown symbol: {symbol}")
        raise last_error

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
            elif kind == constants.KIND_CFD:
                pool = list(constants.CFD_UNIVERSE.keys())
        available = [s for s in pool if s not in exclude]
        return random.choice(available or pool)
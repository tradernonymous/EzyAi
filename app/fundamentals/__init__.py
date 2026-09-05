import html
import re
import requests

from .. import constants


class Fundamentals:
    CG = "https://api.coingecko.com/api/v3"
    NEWS = "https://news.google.com/rss/search"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EzyAiBot/1.0",
        "Accept": "application/json",
    }

    def __init__(self, session=None, timeout=10):
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self.timeout = timeout

    def _get(self, url, params=None):
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _coin_id(self, pair):
        cid = constants.COINGECKO_IDS.get(pair)
        if cid:
            return cid
        try:
            search = self._get(f"{self.CG}/search", {"query": pair.replace("USDT", "")})
            coins = search.get("coins", [])
            for c in coins:
                if c.get("symbol", "").upper() == pair.replace("USDT", ""):
                    return c["id"]
            if coins:
                return coins[0]["id"]
        except Exception:
            pass
        return None

    def crypto(self, pair):
        cid = self._coin_id(pair)
        if not cid:
            return None
        try:
            price = self._get(f"{self.CG}/simple/price", {
                "ids": cid, "vs_currencies": "usd",
                "include_market_cap": "true", "include_24hr_vol": "true",
                "include_24hr_change": "true",
                "include_last_updated_at": "true",
            }).get(cid, {})
            detail = self._get(f"{self.CG}/coins/{cid}")
            meta = detail.get("market_data", {})
            links = detail.get("links", {})
            return {
                "id": cid,
                "name": detail.get("name"),
                "symbol": detail.get("symbol", "").upper(),
                "price_usd": price.get("usd"),
                "mcap": price.get("usd_market_cap"),
                "volume_24h": price.get("usd_24h_vol"),
                "change_24h": price.get("usd_24h_change"),
                "high_24h": meta.get("high_24h", {}).get("usd"),
                "low_24h": meta.get("low_24h", {}).get("usd"),
                "ath": meta.get("ath", {}).get("usd"),
                "atl": meta.get("atl", {}).get("usd"),
                "ath_pct": meta.get("ath_change_percentage", {}).get("usd"),
                "rank": meta.get("market_cap_rank"),
                "desc": html.unescape(re.sub(r"<[^>]+>", "",
                                             detail.get("description", {}).get("en", "")))[:400],
                "website": (links.get("homepage") or [None])[0],
                "explorer": (links.get("blockchain_site") or [None])[0],
                "whitepaper": (links.get("whitepaper") or None),
            }
        except Exception:
            return None

    def stock(self, symbol):
        from ..data.provider import YahooProvider
        yahoo = YahooProvider(kind=constants.KIND_STOCK)
        candles = yahoo.fetch_klines(symbol, "1d", 300)
        if not candles:
            return None
        closes = [c["close"] for c in candles]
        vols = [c["volume"] for c in candles]
        last = closes[-1]
        out = {
            "price": last,
            "name": symbol,
            "high_52w": max(closes[-252:]),
            "low_52w": min(closes[-252:]),
            "avg_volume_20": sum(vols[-20:]) / 20,
            "chg_1w": (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0,
            "chg_1m": (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else 0.0,
            "chg_3m": (closes[-1] / closes[-66] - 1) * 100 if len(closes) >= 66 else 0.0,
            "chg_1y": (closes[-1] / closes[-252] - 1) * 100 if len(closes) >= 252 else 0.0,
            "vol_pct": self._realized_vol(closes),
            "source": "derived from daily candles",
        }
        try:
            quotes = yahoo.fetch_quote([symbol])
            if symbol in quotes:
                q = quotes[symbol]
                for key in ("marketCap", "trailingPE", "forwardPE", "exchange",
                            "longName", "regularMarketPreviousClose"):
                    if q.get(key) is not None:
                        out[key] = q[key]
                out["source"] = "yahoo quote + derived"
        except Exception:
            pass
        return out

    @staticmethod
    def _realized_vol(closes, period=252):
        import math
        if len(closes) < 2:
            return 0.0
        log_rets = [math.log(closes[i] / closes[i - 1])
                    for i in range(1, len(closes)) if closes[i - 1] > 0]
        if not log_rets:
            return 0.0
        mean = sum(log_rets) / len(log_rets)
        var = sum((x - mean) ** 2 for x in log_rets) / max(len(log_rets) - 1, 1)
        return math.sqrt(var * period) * 100.0

    def forex(self, symbol):
        from ..data.provider import YahooProvider
        yahoo = YahooProvider(kind=constants.KIND_FOREX)
        candles = yahoo.fetch_klines(symbol, "1d", 300)
        if not candles:
            return None
        closes = [c["close"] for c in candles]
        return {
            "price": closes[-1],
            "high_1y": max(closes[-252:]),
            "low_1y": min(closes[-252:]),
            "chg_1w": (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0,
            "chg_1m": (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else 0.0,
            "chg_3m": (closes[-1] / closes[-66] - 1) * 100 if len(closes) >= 66 else 0.0,
            "chg_1y": (closes[-1] / closes[-252] - 1) * 100 if len(closes) >= 252 else 0.0,
            "vol_pct": self._realized_vol(closes),
            "source": "derived from daily candles",
        }

    def news(self, query, limit=3):
        items = []
        try:
            text = self.session.get(
                self.NEWS,
                params={"q": f"{query} price", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                timeout=self.timeout,
            ).text
            for m in re.finditer(r"<item>(.*?)</item>", text, re.S):
                block = m.group(1)
                title = re.sub(r"<[^>]+>", "", block)
                title = title.replace("&amp;", "&").replace("&#39;", "'")
                link_m = re.search(r"<link>(.*?)</link>", block)
                if not link_m:
                    continue
                link = link_m.group(1).replace("&amp;", "&")
                items.append({"title": title.strip()[:160], "url": link.strip()})
                if len(items) >= limit:
                    break
        except Exception:
            pass
        return items

    def links(self, kind, symbol):
        s = symbol.upper()
        out = []
        if kind == constants.KIND_CRYPTO:
            asset = s.replace("USDT", "")
            out += [
                ("CoinGecko", f"https://www.coingecko.com/en/coins/{asset.lower()}"),
                ("CoinMarketCap", f"https://coinmarketcap.com/currencies/{asset.lower()}/"),
                ("Binance", f"https://www.binance.com/en/trade/{s}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{s}"),
            ]
            explorer = constants.CRYPTO_REVERSE_URL.get(s)
            if explorer:
                out.append(("Block Explorer", explorer))
        elif kind == constants.KIND_FOREX:
            yahoo = constants.FX_UNIVERSE.get(s, s + "=X")
            out += [
                ("Yahoo Finance", f"https://finance.yahoo.com/quote/{yahoo}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol=FX%3A{s}"),
                ("FXStreet", f"https://www.fxstreet.com/search?q={s}"),
                ("Forex Calendar", "https://www.forexfactory.com/calendar"),
                ("Investing.com", f"https://www.investing.com/currencies/{s.lower()}"),
            ]
        elif kind == constants.KIND_STOCK:
            out += [
                ("Yahoo Finance", f"https://finance.yahoo.com/quote/{s}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol={s}"),
                ("StockAnalysis", f"https://stockanalysis.com/stocks/{s.lower()}/"),
                ("Macrotrends", f"https://www.macrotrends.net/stocks/charts/{s}/stock"),
            ]
        return out
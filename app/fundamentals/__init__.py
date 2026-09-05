import html
import re
import time
import requests

from .. import constants
from . import edgar as edgar_mod
from . import scoring


class Fundamentals:
    CG = "https://api.coingecko.com/api/v3"
    NEWS = "https://news.google.com/rss/search"
    COT = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EzyAiBot/1.0",
        "Accept": "application/json",
    }

    def __init__(self, session=None, timeout=10):
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self.timeout = timeout
        self._cache = {}

    def _cached(self, key, ttl_s, fn, fail_ttl_s=600):
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl_s:
            return hit[1]
        try:
            value = fn()
        except Exception:
            self._cache[key] = (now - ttl_s + fail_ttl_s, None)
            return None
        self._cache[key] = (now, value)
        return value

    def _get(self, url, params=None):
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _coin_id(self, pair):
        cid = constants.COINGECKO_IDS.get(pair)
        if cid:
            return cid
        asset = constants.base_asset(pair)
        try:
            search = self._get(f"{self.CG}/search", {"query": asset})
            coins = search.get("coins", [])
            for c in coins:
                if c.get("symbol", "").upper() == asset:
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
            mcap = price.get("usd_market_cap")
            vol24 = price.get("usd_24h_vol")
            out = {
                "id": cid,
                "name": detail.get("name"),
                "symbol": detail.get("symbol", "").upper(),
                "price_usd": price.get("usd"),
                "mcap": mcap,
                "volume_24h": vol24,
                "change_24h": price.get("usd_24h_change"),
                "high_24h": meta.get("high_24h", {}).get("usd"),
                "low_24h": meta.get("low_24h", {}).get("usd"),
                "ath": meta.get("ath", {}).get("usd"),
                "atl": meta.get("atl", {}).get("usd"),
                "ath_pct": meta.get("ath_change_percentage", {}).get("usd"),
                "chg_7d": (meta.get("price_change_percentage_7d_in_currency") or {}).get("usd",
                    meta.get("price_change_percentage_7d")),
                "chg_30d": (meta.get("price_change_percentage_30d_in_currency") or {}).get("usd",
                    meta.get("price_change_percentage_30d")),
                "chg_1y": (meta.get("price_change_percentage_1y_in_currency") or {}).get("usd",
                    meta.get("price_change_percentage_1y")),
                "rank": meta.get("market_cap_rank"),
                "desc": html.unescape(re.sub(r"<[^>]+>", "",
                                             detail.get("description", {}).get("en", "")))[:400],
                "website": (links.get("homepage") or [None])[0],
                "explorer": (links.get("blockchain_site") or [None])[0],
                "whitepaper": (links.get("whitepaper") or None),
            }
            out["volume_mcap"] = (vol24 / mcap) if (vol24 and mcap) else None
            gauge = scoring.crypto_score(
                out["chg_7d"], out["chg_30d"], out["chg_1y"], out["ath_pct"],
                out["volume_mcap"], out["rank"])
            out["cscore"] = gauge["score"]
            out["cgrade"] = gauge["grade"]
            out["cpillars"] = gauge["pillars"]
            return out
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
        try:
            out.update(self.stock_statements(symbol))
        except Exception:
            pass
        return out

    def edgar_facts(self, ticker):
        """Raw SEC company-facts JSON (cached 24h). None for ETFs/unknown."""
        cik = constants.SEC_CIK.get(ticker.upper())
        if not cik:
            return None

        def fetch():
            r = self.session.get(
                edgar_mod.EDGAR_FACTS.format(cik=cik), timeout=self.timeout)
            r.raise_for_status()
            return r.json()

        return self._cached("edgar:" + ticker.upper(), 86400, fetch)

    def stock_statements(self, symbol):
        """Parsed statement metrics + score + DCF. Never raises."""
        try:
            facts = self.edgar_facts(symbol)
            if not facts:
                return {"stat_note": "ETF/basket: no 10-K statements"}
            m = edgar_mod.statement_metrics(facts)
            derived = self._derived(symbol, constants.KIND_STOCK) or {}
            price = derived.get("price")
            pos = None
            hi, lo = derived.get("high_1y"), derived.get("low_1y")
            if price and hi and lo and hi > lo:
                pos = (price - lo) / (hi - lo)
            score = scoring.stock_score(m, price, derived.get("chg_3m"), pos)
            dcf = scoring.dcf_intrinsic(
                m.get("fcf"), m.get("ocf_cagr_3y"), m.get("shares"),
                m.get("net_debt") or 0.0)
            verdict = scoring.dcf_verdict(dcf, price)
            return {"stat_entity": m.get("entity"), "stat_fy": m.get("fy"),
                    "stat_metrics": {k: m.get(k) for k in (
                        "revenue", "net_income", "equity", "fcf", "shares",
                        "eps", "rev_cagr_3y", "eps_cagr_3y", "ocf_cagr_3y",
                        "net_margin", "roe", "de_ratio", "current_ratio")},
                    "fscore": score["score"], "fgrade": score["grade"],
                    "fpillars": score["pillars"], "fpe": score["pe"],
                    "fnotes": score["notes"], "dcf": dcf, "dcf_verdict": verdict}
        except Exception:
            return {}

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

    def _derived(self, symbol, kind):
        from ..data.provider import YahooProvider
        yahoo = YahooProvider(kind=kind)
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

    def forex(self, symbol):
        d = self._derived(symbol, constants.KIND_FOREX)
        if d:
            try:
                d["verdict"] = scoring.fx_verdict(
                    symbol, d.get("chg_1w"), d.get("chg_1m"),
                    d.get("chg_3m"), d.get("vol_pct"))
            except Exception:
                pass
        return d

    def cot(self, pair):
        """CFTC large-speculator positioning for XAUUSD/WTI/UKOIL (weekly)."""
        target = constants.COT_TARGETS.get((pair or "").upper())
        if not target:
            return None

        def fetch():
            if "name" in target:
                where = "market_and_exchange_names='%s'" % target["name"]
            else:
                where = ("cftc_commodity_code='%s' AND cftc_market_code='%s'"
                         % (target["code"], target["market"]))
            rows = self.session.get(
                self.COT, params={
                    "$limit": 60, "$where": where,
                    "$select": "report_date_as_yyyy_mm_dd,"
                               "market_and_exchange_names,"
                               "noncomm_positions_long_all,"
                               "noncomm_positions_short_all,"
                               "open_interest_all",
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                }, timeout=self.timeout).json()
            by_market = {}
            for r in rows:
                try:
                    by_market.setdefault(r["market_and_exchange_names"], []).append({
                        "date": r["report_date_as_yyyy_mm_dd"][:10],
                        "long": int(r["noncomm_positions_long_all"]),
                        "short": int(r["noncomm_positions_short_all"]),
                        "oi": int(r["open_interest_all"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            if not by_market:
                return None
            # flagship contract = highest latest open interest
            name = max(by_market, key=lambda k: by_market[k][0]["oi"])
            reps = by_market[name]
            if len(reps) < 1:
                return None
            cur = reps[0]
            net = cur["long"] - cur["short"]
            prev = reps[1] if len(reps) > 1 else None
            prev_net = (prev["long"] - prev["short"]) if prev else None
            return {
                "market": name, "date": cur["date"],
                "net_long": net,
                "wow": (net - prev_net) if prev_net is not None else None,
                "long": cur["long"], "short": cur["short"],
                "pct_oi": (net / cur["oi"] * 100) if cur["oi"] else None,
            }

        return self._cached("cot:" + pair.upper(), 86400, fetch)

    def cfd(self, symbol, tag=None):
        d = self._derived(symbol, constants.KIND_CFD)
        if d and tag:
            try:
                d["cot"] = self.cot(tag)
            except Exception:
                pass
        return d

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
            asset = constants.base_asset(s)
            venue = constants.binance_symbol(s)
            out += [
                ("CoinGecko", f"https://www.coingecko.com/en/coins/{asset.lower()}"),
                ("CoinMarketCap", f"https://coinmarketcap.com/currencies/{asset.lower()}/"),
                ("Binance", f"https://www.binance.com/en/trade/{venue}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{venue}"),
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
        elif kind == constants.KIND_CFD:
            yahoo = constants.CFD_UNIVERSE.get(s, s)
            tv = constants.CFD_TRADINGVIEW.get(s, yahoo)
            out += [
                ("Yahoo Finance", f"https://finance.yahoo.com/quote/{yahoo}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol={tv}"),
                ("Investing.com", f"https://www.investing.com/search/?q={s}"),
                ("FXStreet", f"https://www.fxstreet.com/search?q={s}"),
                ("TradingView ideas", f"https://www.tradingview.com/ideas/search/{s}/"),
            ]
        elif kind == constants.KIND_STOCK:
            out += [
                ("Yahoo Finance", f"https://finance.yahoo.com/quote/{s}"),
                ("TradingView", f"https://www.tradingview.com/chart/?symbol={s}"),
                ("StockAnalysis", f"https://stockanalysis.com/stocks/{s.lower()}/"),
                ("Macrotrends", f"https://www.macrotrends.net/stocks/charts/{s}/stock"),
            ]
        return out
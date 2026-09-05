import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.formatting import message as msg  # noqa: E402
from app.fundamentals import Fundamentals  # noqa: E402
from app.fundamentals import edgar  # noqa: E402
from app.fundamentals import scoring  # noqa: E402


def _facts(entries):
    # entries: [(tag, unit, fy, form, filed, val)]
    units = {}
    for tag, unit, fy, form, filed, val in entries:
        units.setdefault(tag, {}).setdefault(unit, []).append(
            {"fy": fy, "form": form, "filed": filed, "val": val})
    return {"entityName": "Test Corp", "facts": {"us-gaap": {
        tag: {"units": u} for tag, u in units.items()}}}


def test_annual_series_latest_filing_wins():
    f = _facts([
        ("Revenues", "USD", 2024, "10-K", "2024-11-01", 100.0),
        ("Revenues", "USD", 2024, "10-K/A", "2024-12-01", 110.0),
        ("Revenues", "USD", 2023, "10-Q", "2023-07-01", 999.0),
        ("Revenues", "USD", 2023, "10-K", "2023-11-01", 90.0),
    ])
    assert edgar.annual_series(f, "Revenues") == {2023: 90.0, 2024: 110.0}


def test_pick_series_falls_back():
    f = _facts([("SalesRevenueNet", "USD", 2024, "10-K", "2024-11-01", 50.0)])
    tag, s = edgar.pick_series(f, ["Revenues", "SalesRevenueNet"])
    assert tag == "SalesRevenueNet" and s == {2024: 50.0}
    assert edgar.pick_series(f, ["Nope"]) == (None, {})


def test_cagr_math_and_guards():
    assert abs(edgar.cagr([100.0, 110.0, 121.0]) - 0.10) < 1e-9
    assert edgar.cagr([100.0]) is None
    assert edgar.cagr([-5.0, 10.0, 20.0]) is None
    assert edgar.cagr([100.0, 0.0]) is None


def _rich_facts():
    rows = []
    rev = [80.0, 90.0, 100.0, 115.0]
    ni = [8.0, 10.0, 12.0, 15.0]
    for i, fy in enumerate((2021, 2022, 2023, 2024)):
        rows += [
            ("Revenues", "USD", fy, "10-K", f"{fy}-11-01", rev[i]),
            ("NetIncomeLoss", "USD", fy, "10-K", f"{fy}-11-01", ni[i]),
            ("StockholdersEquity", "USD", fy, "10-K", f"{fy}-11-01", 50.0),
            ("NetCashProvidedByUsedInOperatingActivities", "USD", fy, "10-K",
             f"{fy}-11-01", 20.0 + i),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", fy, "10-K",
             f"{fy}-11-01", 5.0),
            ("CommonStockSharesOutstanding", "shares", fy, "10-K",
             f"{fy}-11-01", 10.0),
            ("EarningsPerShareDiluted", "USD/shares", fy, "10-K",
             f"{fy}-11-01", ni[i] / 10.0),
            ("LongTermDebt", "USD", fy, "10-K", f"{fy}-11-01", 10.0),
            ("AssetsCurrent", "USD", fy, "10-K", f"{fy}-11-01", 30.0),
            ("LiabilitiesCurrent", "USD", fy, "10-K", f"{fy}-11-01", 15.0),
        ]
    return _facts(rows)


def test_statement_metrics_ratios():
    m = edgar.statement_metrics(_rich_facts())
    assert m["entity"] == "Test Corp"
    assert m["revenue"] == 115.0
    assert m["fcf"] == 23.0 - 5.0
    assert abs(m["net_margin"] - 15.0 / 115.0) < 1e-9
    assert abs(m["roe"] - 15.0 / 50.0) < 1e-9
    assert abs(m["de_ratio"] - 10.0 / 50.0) < 1e-9
    assert abs(m["current_ratio"] - 2.0) < 1e-9
    assert m["rev_cagr_3y"] is not None and m["rev_cagr_3y"] > 0.10


def test_stock_score_bands_and_grade():
    m = edgar.statement_metrics(_rich_facts())
    s = scoring.stock_score(m, price=30.0, chg_3m=20.0, pos_52w=0.9)
    assert s["pe"] == 30.0 / 1.5
    assert 0 <= s["score"] <= 100
    assert set(s["pillars"]) == {"valuation", "profitability", "growth",
                                 "health", "momentum"}
    assert s["grade"] in ("A+", "A", "B", "C", "D", "F")
    # cheap + profitable + growing + healthy + hot = high score
    assert s["score"] >= 70
    # no earnings -> valuation zero
    m2 = dict(m, eps=-1.0)
    assert scoring.stock_score(m2, 30.0)["pillars"]["valuation"] == 0.0


def test_dcf_math_and_guards():
    d = scoring.dcf_intrinsic(100.0, 0.10, 10.0, 0.0)
    assert d and d["intrinsic"] > 0
    assert d["growth_used"] == 0.10
    # growth clamped at 15%
    d2 = scoring.dcf_intrinsic(100.0, 0.99, 10.0, 0.0)
    assert d2["growth_used"] == 0.15
    assert d2["intrinsic"] < scoring.dcf_intrinsic(100.0, 0.99, 10.0, 0.0)["intrinsic"] * 2
    # net debt reduces equity value
    d3 = scoring.dcf_intrinsic(100.0, 0.10, 10.0, 500.0)
    assert d3["intrinsic"] < d["intrinsic"]
    assert scoring.dcf_intrinsic(-5.0, 0.10, 10.0) is None
    assert scoring.dcf_intrinsic(100.0, 0.10, 0) is None
    v = scoring.dcf_verdict(d, d["intrinsic"] * 0.5)
    assert v["label"] == "undervalued" and v["mos_pct"] > 20
    v2 = scoring.dcf_verdict(d, d["intrinsic"] * 2.0)
    assert v2["label"] == "overvalued"
    assert scoring.dcf_verdict(None, 10.0) is None


def test_crypto_score_bands():
    s = scoring.crypto_score(5.0, 12.0, 80.0, -5.0, 0.12, 1)
    assert s["score"] >= 85 and s["grade"] == "A+"
    s2 = scoring.crypto_score(-5.0, -12.0, -40.0, -80.0, 0.001, 500)
    assert s2["score"] < 40
    assert scoring.crypto_score()["score"] == 0.0


def test_fx_verdict_carry_and_risk():
    v = scoring.fx_verdict("EURUSD", 1.0, 2.0, 3.0, 8.0)
    assert v["base"] == "EUR" and v["quote"] == "USD"
    assert abs(v["carry_bp"] - (2.00 - 3.62) * 100) < 1e-9
    assert v["agree"] is True and v["risk"] == "low"
    v2 = scoring.fx_verdict("EURUSD", 1.0, -2.0, -3.0, 30.0)
    assert v2["agree"] is False and v2["risk"] == "high"
    assert v2["direction"] == "mixed"
    assert "2026" in v["rates_asof"]


def test_cache_ttl():
    f = Fundamentals(session=None)
    calls = []

    def fn():
        calls.append(1)
        return "v"
    assert f._cached("k", 3600, fn) == "v"
    assert f._cached("k", 3600, fn) == "v"
    assert len(calls) == 1
    f._cache["k"] = (0.0, "old")
    assert f._cached("k", 3600, fn) == "v"
    assert len(calls) == 2


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Session:
    def __init__(self, payload):
        self._p = payload
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _Resp(self._p)


def test_cot_picks_flagship_and_wow():
    rows = [
        {"report_date_as_yyyy_mm_dd": "2026-09-01T00:00:00.000",
         "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
         "noncomm_positions_long_all": "260485",
         "noncomm_positions_short_all": "32361",
         "open_interest_all": "415196"},
        {"report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000",
         "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
         "noncomm_positions_long_all": "250000",
         "noncomm_positions_short_all": "40000",
         "open_interest_all": "410000"},
        {"report_date_as_yyyy_mm_dd": "2026-09-01T00:00:00.000",
         "market_and_exchange_names": "MICRO GOLD - COMMODITY EXCHANGE INC.",
         "noncomm_positions_long_all": "10",
         "noncomm_positions_short_all": "5",
         "open_interest_all": "100"},
    ]
    f = Fundamentals(session=_Session(rows))
    c = f.cot("XAUUSD")
    assert c["market"] == "GOLD - COMMODITY EXCHANGE INC."
    assert c["net_long"] == 260485 - 32361
    assert c["wow"] == (260485 - 32361) - (250000 - 40000)
    assert c["date"] == "2026-09-01"
    # cached: second call makes no HTTP
    assert f.cot("XAUUSD") == c
    assert f.session.calls == 1
    assert f.cot("EURUSD") is None


def test_report_blocks_present():
    stock = {"price": 200.0, "high_52w": 220.0, "low_52w": 150.0,
             "chg_1w": 1.0, "chg_1m": 2.0, "chg_3m": 5.0, "chg_1y": 20.0,
             "vol_pct": 25.0, "avg_volume_20": 1000, "fscore": 72.0,
             "fgrade": "B",
             "fpillars": {"valuation": 15.0, "profitability": 20.0,
                          "growth": 14.0, "health": 12.0, "momentum": 11.0},
             "fpe": 22.0, "fnotes": [],
             "dcf": {"intrinsic": 250.0, "growth_used": 0.1, "discount": 0.09,
                     "terminal": 0.025, "years": 5,
                     "assumptions": "FCF $10.0B growing 10% for 5y"},
             "dcf_verdict": {"intrinsic": 250.0, "mos_pct": 20.0,
                             "label": "undervalued"}}
    t = msg.fundamentals_report("stock", "AAPL", stock, "live")
    assert "72/100 (B)" in t and "Fair value" in t and "margin" in t
    fx = {"price": 1.08, "low_1y": 1.0, "high_1y": 1.2, "chg_1w": 1.0,
          "chg_1m": 2.0, "chg_3m": 3.0, "chg_1y": 4.0, "vol_pct": 8.0,
          "verdict": {"base": "EUR", "quote": "USD", "carry_bp": -162.0,
                      "rates_asof": "2026-09-05", "agree": True,
                      "direction": "bullish EUR", "risk": "low"}}
    t2 = msg.fundamentals_report("forex", "EURUSD", fx, "live")
    assert "Carry" in t2 and "risk: low" in t2
    assert msg.meter(50) == "\u25b0" * 4 + "\u25b1" * 4
    bare = msg.fundamentals_report("stock", "X", None, "live")
    assert "<b>Links</b>" not in bare  # links now live in related_reading
    tail = msg.related_reading("stock", "X", [], [])
    assert "\u2500" in tail  # divider always present
    assert "not financial advice" in tail


def test_policy_rates_sane():
    assert constants.POLICY_RATES_ASOF.startswith("2026")
    for ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "CHF", "CAD", "NZD"):
        rate, bank = constants.POLICY_RATES[ccy]
        assert -1.0 <= rate <= 10.0 and bank
        assert ccy in constants.POLICY_STANCE
    assert "AAPL" in constants.SEC_CIK and "SPY" not in constants.SEC_CIK


def _p_series():
    def yrs(vals):
        return {2023 + i: v for i, v in enumerate(vals)}
    return {
        "net_income": yrs([8.0, 12.0]),
        "assets": yrs([100.0, 110.0]),
        "ocf": yrs([15.0, 20.0]),
        "revenue": yrs([90.0, 115.0]),
        "debt": yrs([20.0, 18.0]),
        "equity": yrs([50.0, 55.0]),
        "assets_current": yrs([30.0, 35.0]),
        "liab_current": yrs([15.0, 14.0]),
        "shares": yrs([10.0, 10.0]),
        "gross_profit": yrs([30.0, 45.0]),
    }


def test_piotroski_all_pass():
    p = scoring.piotroski(_p_series())
    assert p["score"] == 9 and p["total"] == 9 and not p["failed"]


def test_piotroski_abstains_when_missing():
    p = scoring.piotroski({"net_income": {2024: 5.0}})
    assert p["total"] <= 2
    assert scoring.piotroski({}) == {"score": 0, "total": 0,
                                     "passed": [], "failed": []}


def test_piotroski_aligns_fiscal_years():
    # ocf covering different years than income must not mix FYs
    s = _p_series()
    s["ocf"] = {2022: 15.0, 2023: 20.0}  # lags income (2023, 2024)
    p = scoring.piotroski(s)
    assert p["total"] == 9  # aligned on 2022+2023, nothing abstains wrongly
    assert "Cash-backed earnings" in p["passed"]  # 20 > 12 on aligned FYs


def test_earnings_quality_labels():
    assert scoring.earnings_quality(10.0, 10.0) == "strong cash conversion"
    assert scoring.earnings_quality(4.0, 10.0) == "partial cash conversion"
    assert scoring.earnings_quality(-2.0, 10.0) == "negative cash conversion"
    assert scoring.earnings_quality(-2.0, -5.0) == "loss-making: cash burn"
    assert scoring.earnings_quality(2.0, -5.0) == "loss-making on paper, cash-positive"
    assert scoring.earnings_quality(None, 10.0) is None


def test_related_reading_sentences_and_links():
    links = [("CoinGecko", "https://x/y"), ("Binance", "https://x/z"),
             ("TradingView", "https://x/w"), ("Block Explorer", "https://x/v")]
    news = [{"title": "Bitcoin rallies hard", "url": "https://n/1"}]
    t = msg.related_reading("crypto", "BTCUSD", links, news)
    assert "Track BTCUSD live on" in t or "Track BTC live on" in t
    assert '<a href="https://x/y">CoinGecko</a>' in t
    assert "Bitcoin rallies hard" in t and '<a href="https://n/1">' in t
    assert "\u2500" in t  # divider present
    fx = msg.related_reading(
        "forex", "EURUSD",
        [("Yahoo Finance", "https://y"), ("Forex Calendar", "https://c")], [])
    assert "economic calendar" in fx
    assert "Related headlines" not in fx
    assert "not financial advice" in t  # footer travels with the tail


class _RssSession(_Session):
    def __init__(self, text):
        super().__init__(None)
        self._text = text

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _RssResp(self._text)


class _RssResp:
    def __init__(self, text):
        self.text = text


def test_news_title_excludes_link_url():
    rss = ("<rss><channel>"
           "<item><title>Apple gains on iPhone news</title>"
           "<link>https://news.google.com/rss/articles/XYZ?oc=5</link>"
           "<source url=\"https://x\">Yahoo Finance</source></item>"
           "</channel></rss>")
    f = Fundamentals(session=_RssSession(rss))
    items = f.news("AAPL")
    assert len(items) == 1
    assert items[0]["title"] == "Apple gains on iPhone news"
    assert "https://" not in items[0]["title"]
    assert items[0]["url"].startswith("https://news.google.com")


def test_report_new_sections_and_length():
    data = {"price": 200.0, "high_52w": 220.0, "low_52w": 150.0,
            "chg_1w": 1.0, "chg_1m": 2.0, "chg_3m": 5.0, "chg_1y": 20.0,
            "vol_pct": 25.0, "avg_volume_20": 1000, "fscore": 72.0,
            "fgrade": "B",
            "fpillars": {"valuation": 15.0, "profitability": 20.0,
                         "growth": 14.0, "health": 12.0, "momentum": 11.0},
            "fpe": 22.0, "fnotes": [],
            "piotroski": {"score": 7, "total": 9, "passed": [], "failed": ["No dilution"]},
            "earn_quality": "cash-backed earnings",
            "dcf": {"intrinsic": 250.0, "growth_used": 0.1, "discount": 0.09,
                    "terminal": 0.025, "years": 5,
                    "assumptions": "FCF $10.0B growing 10% for 5y"},
            "dcf_verdict": {"intrinsic": 250.0, "mos_pct": 20.0,
                            "label": "undervalued"}}
    t = msg.fundamentals_report("stock", "AAPL", data, "live")
    assert "Piotroski F-score" in t and "7/9" in t
    assert "cash-backed earnings" in t
    assert len(t) < 4000
    c = {"price_usd": 50000.0, "mcap": 10 ** 12, "volume_24h": 10 ** 10,
         "change_24h": 1.0, "ath_pct": -20.0, "rank": 1, "cscore": 70.0,
         "cgrade": "B",
         "cpillars": {"trend": 28.0, "drawdown_posture": 22.0,
                      "liquidity": 10.0, "scale": 15.0},
         "sent_up": 78.0, "dev_commits_4w": 1234, "supply_mined_pct": 94.5}
    t2 = msg.fundamentals_report("crypto", "BTCUSD", c, "live")
    assert "78% bullish" in t2 and "1,234 dev commits" in t2 and "94.5%" in t2

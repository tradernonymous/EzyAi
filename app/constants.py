STYLES = ("scalping", "intraday", "swing")
MODES = ("safe", "normal", "aggressive")
SIDES = ("long", "short")

KIND_CRYPTO = "crypto"
KIND_FOREX = "forex"
KIND_STOCK = "stock"
KIND_CFD = "cfd"

INTERVALS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

STYLE_PROFILE = {
    "scalping": {
        "label": "Scalping",
        "base_tf": "5m",
        "direction_tf": "15m",
        "check_interval_s": 60,
        "min_gap_s": 900,
        "candles": 150,
        "hold": "minutes",
    },
    "intraday": {
        "label": "Intraday",
        "base_tf": "15m",
        "direction_tf": "1h",
        "check_interval_s": 300,
        "min_gap_s": 3600,
        "candles": 150,
        "hold": "hours",
    },
    "swing": {
        "label": "Swing",
        "base_tf": "1d",
        "direction_tf": "1d",
        "check_interval_s": 1800,
        "min_gap_s": 21600,
        "candles": 200,
        "hold": "days",
    },
}

MODE_PROFILE = {
    "safe": {
        "label": "Safe",
        "risk_frac": 0.005,
        "rr": 2.5,
        "sl_atr_mult": 1.3,
        "tp_atr_mult": 2.6,
        "daily_limit": 3,
        "aggression": 0.6,
        "extra_confirmation": True,
    },
    "normal": {
        "label": "Normal",
        "risk_frac": 0.01,
        "rr": 2.0,
        "sl_atr_mult": 1.0,
        "tp_atr_mult": 2.0,
        "daily_limit": 6,
        "aggression": 1.0,
        "extra_confirmation": False,
    },
    "aggressive": {
        "label": "Aggressive",
        "risk_frac": 0.02,
        "rr": 1.5,
        "sl_atr_mult": 0.8,
        "tp_atr_mult": 1.5,
        "daily_limit": 10,
        "aggression": 1.5,
        "extra_confirmation": False,
    },
}

CONFIDENCE_GATE = 62

# Phase-3 confluence scoring switch. Backtest evidence (2026-09: patterns,
# vol-regime, session all ~neutral to slightly negative vs tuned gates)
# does not support spending confidence points, so scoring stays OFF.
# Factual confluence notes are still shown in reasons (zero signal impact).
# Revisit only with calibration proof (Phase 4).
CONFLUENCE_SCORING = False

# Tunable signal gates per style. Defaults reproduce the legacy hardcoded
# thresholds exactly; Phase-2 tuning may adjust them based on backtest
# evidence (must beat defaults AND a random baseline to ship).
#   rsi_long/rsi_short: (lo, hi) healthy zones
#   adx_min:           minimum ADX for the strength bonus / trend filter
#   stoch_cut:         stochastic momentum cutoff (long: k > cut)
#   macd_atr_min:      0 = sign only (legacy); >0 requires |hist| >= mult*ATR
#   conf_gate:         base confidence gate (live gate = conf_gate - aggression*6)
_DEFAULT_GATES = {
    "rsi_long": (45.0, 68.0),
    "rsi_short": (30.0, 55.0),
    "adx_min": 25.0,
    "stoch_cut": 50.0,
    "macd_atr_min": 0.0,
    "conf_gate": 62.0,
}
SIGNAL_GATES = {
    # scalping keeps defaults: tuning gains were marginal (lift +0.4pp).
    "scalping": dict(_DEFAULT_GATES),
    # intraday tuned 2026-09-05 (n=2261, PF 1.16->1.22, DD 52.8->38.6R).
    "intraday": {
        "rsi_long": (40.0, 65.0),
        "rsi_short": (28.0, 52.0),
        "adx_min": 32.0,
        "stoch_cut": 50.0,
        "macd_atr_min": 0.0,
        "conf_gate": 66.0,
    },
    # swing tuned 2026-09-05 (n=2501, PF 1.36->1.42, lift +1.8->+3.8pp).
    "swing": {
        "rsi_long": (45.0, 68.0),
        "rsi_short": (30.0, 55.0),
        "adx_min": 28.0,
        "stoch_cut": 45.0,
        "macd_atr_min": 0.0,
        "conf_gate": 70.0,
    },
}

CRYPTO_UNIVERSE = [
    "BTCUSD", "ETHUSD", "BNBUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "LINKUSD", "LTCUSD", "DOTUSD", "TRXUSD",
    "ATOMUSD", "NEARUSD", "ARBUSD", "OPUSD", "INJUSD", "SUIUSD",
    "APTUSD", "FILUSD", "PEPEUSD", "SHIBUSD", "ENAUSD", "ONDOUSD",
    "AAVEUSD", "UNIUSD", "XLMUSD", "VETUSD", "ICPUSD", "HBARUSD",
]

FX_UNIVERSE = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "USDCHF=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

STOCK_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "AMD",
    "NFLX", "PLTR", "COIN", "MSTR", "NIO", "SOFI", "RIVN", "SHOP",
    "SPY", "QQQ", "IWM", "VOO", "TQQQ", "ARKK",
]

CFD_UNIVERSE = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "WTI": "CL=F",
    "UKOIL": "BZ=F",
    "NGAS": "NG=F",
    "COPPER": "HG=F",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "SPX500": "^GSPC",
    "GER40": "^GDAXI",
}

CFD_TRADINGVIEW = {
    "XAUUSD": "OANDA:XAUUSD",
    "XAGUSD": "OANDA:XAGUSD",
    "WTI": "TVC:USOIL",
    "UKOIL": "TVC:UKOIL",
    "NGAS": "TVC:NG",
    "COPPER": "TVC:COPPER",
    "US30": "TVC:DJI",
    "NAS100": "TVC:NASDAQ",
    "SPX500": "TVC:SPX",
    "GER40": "TVC:DAX",
}

ALL_UNIVERSE = (
    CRYPTO_UNIVERSE
    + list(FX_UNIVERSE.keys())
    + STOCK_UNIVERSE
    + list(CFD_UNIVERSE.keys())
)

CRYPTO_REVERSE_URL = {
    "BTCUSD": "https://www.blockchain.com/explorer/transactions/btc",
    "ETHUSD": "https://etherscan.io",
    "BNBUSD": "https://bscscan.com",
    "SOLUSD": "https://solscan.io",
    "XRPUSD": "https://xrpscan.com",
    "ADAUSD": "https://cardanoscan.io",
    "DOGEUSD": "https://dogechain.info",
    "AVAXUSD": "https://snowtrace.io",
    "LINKUSD": "https://linkpool.io",
    "LTCUSD": "https://litecoinspace.org",
    "TRXUSD": "https://tronscan.org",
    "SUIUSD": "https://suiscan.xyz",
    "ARBUSD": "https://arbiscan.io",
    "OPUSD": "https://optimistic.etherscan.io",
}

COINGECKO_IDS = {
    "BTCUSD": "bitcoin",
    "ETHUSD": "ethereum",
    "BNBUSD": "binancecoin",
    "SOLUSD": "solana",
    "XRPUSD": "ripple",
    "ADAUSD": "cardano",
    "DOGEUSD": "dogecoin",
    "AVAXUSD": "avalanche-2",
    "LINKUSD": "chainlink",
    "LTCUSD": "litecoin",
    "DOTUSD": "polkadot",
    "TRXUSD": "tron",
    "ATOMUSD": "cosmos",
    "NEARUSD": "near",
    "ARBUSD": "arbitrum",
    "OPUSD": "optimism",
    "INJUSD": "injective-protocol",
    "SUIUSD": "sui",
    "APTUSD": "aptos",
    "FILUSD": "filecoin",
    "PEPEUSD": "pepe",
    "SHIBUSD": "shiba-inu",
    "ENAUSD": "ethena",
    "ONDOUSD": "ondo-finance",
    "AAVEUSD": "aave",
    "UNIUSD": "uniswap",
    "XLMUSD": "stellar",
    "VETUSD": "vechain",
    "ICPUSD": "internet-computer",
    "HBARUSD": "hedera-hashgraph",
}

# Legacy pre-rename aliases (state.json watches / typed input may still use
# venue-style USDT symbols). Both spellings resolve to the same asset.
for _usd, _cid in list(COINGECKO_IDS.items()):
    COINGECKO_IDS.setdefault(_usd.replace("USD", "USDT"), _cid)
for _usd, _url in list(CRYPTO_REVERSE_URL.items()):
    CRYPTO_REVERSE_URL.setdefault(_usd.replace("USD", "USDT"), _url)


def base_asset(symbol):
    """BTCUSD or BTCUSDT -> BTC. Non-crypto symbols pass through."""
    s = symbol.upper()
    if s.endswith("USDT"):
        return s[:-4]
    if s.endswith("USDC"):
        return s[:-4]
    if s in CRYPTO_UNIVERSE:
        return s[:-3]
    return s


def binance_symbol(symbol):
    """Display/legacy crypto spelling -> Binance venue symbol (always USDT)."""
    return base_asset(symbol) + "USDT"


# SEC EDGAR CIKs for statement-based stock analysis (free, no key).
# ETFs/baskets have no 10-K statements and are handled price-only.
SEC_CIK = {
    "AAPL": 320193, "MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724,
    "NVDA": 1045810, "TSLA": 1318605, "META": 1326801, "AMD": 2488,
    "NFLX": 1065280, "PLTR": 1321655, "COIN": 1679788, "MSTR": 1050446,
    "NIO": 1736548, "SOFI": 1818874, "RIVN": 1874179, "SHOP": 1594805,
}

# G10 policy rates in percent, verified Sep 2026 (hiking cycle). Manually
# maintained: stale values only tilt the carry-bias line, never signals.
POLICY_RATES_ASOF = "2026-09-05"
POLICY_RATES = {
    "USD": (3.62, "Fed"), "EUR": (2.00, "ECB"), "GBP": (3.75, "BoE"),
    "JPY": (1.00, "BoJ"), "AUD": (4.35, "RBA"), "CHF": (0.00, "SNB"),
    "CAD": (2.25, "BoC"), "NZD": (2.75, "RBNZ"),
}

# Near-term policy stance per currency (dated; tilts the narrative only).
POLICY_STANCE_ASOF = "2026-09-05"
POLICY_STANCE = {
    "USD": "hike likely 16 Sep", "EUR": "hike likely 10 Sep",
    "GBP": "on hold", "JPY": "hike likely (to 1.25%)",
    "AUD": "on hold 4.35%", "CHF": "on hold 0%",
    "CAD": "on hold 2.25%", "NZD": "fresh hike to 2.75%",
}

# CFTC legacy-futures targets for COT positioning (free Socrata API).
COT_TARGETS = {
    "XAUUSD": {"code": "088 ", "market": "CMX "},
    "WTI": {"code": "067 ", "market": "NYME"},
    "UKOIL": {"name": "BRENT CRUDE OIL LAST DAY - NEW YORK MERCANTILE EXCHANGE"},
}

# ---- Monetization ----------------------------------------------------
# Free: Analyze (+Quote, Dashboard preview). PRO unlocks Watch alerts,
# Autopilot signals and deep Fundamentals (scores, DCF, COT, macro).
TRIAL_DAYS = 3

# Star amounts target the USD price at Telegram's ~$0.02/star pack rate.
PLANS = {
    "1mo": {"months": 1, "usd": 14.99, "stars": 750, "label": "1 month",
            "save": None, "badge": None},
    "6mo": {"months": 6, "usd": 44.99, "stars": 2250, "label": "6 months",
            "save": "save 50%", "badge": "MOST POPULAR"},
    "12mo": {"months": 12, "usd": 99.99, "stars": 5000, "label": "12 months",
             "save": "save 44%", "badge": None},
}
PLAN_ORDER = ("1mo", "6mo", "12mo")
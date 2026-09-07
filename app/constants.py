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
        "confirm_tf": "1h",
        "check_interval_s": 60,
        "min_gap_s": 900,
        "candles": 150,
        "hold": "minutes",
    },
    "intraday": {
        "label": "Intraday",
        "base_tf": "15m",
        "direction_tf": "1h",
        "confirm_tf": "1d",
        "check_interval_s": 300,
        "min_gap_s": 3600,
        "candles": 150,
        "hold": "hours",
    },
    "swing": {
        "label": "Swing",
        "base_tf": "1d",
        "direction_tf": "1d",
        "confirm_tf": "1d",
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

# Phase-2 (approved) confirm-timeframe ladder. The plan's 4h/1w rungs are
# approximated with 1d: Yahoo (the fallback for every venue except cross
# crypto) has no 4h or 1wk bars -- 4h silently returns 1h and 1wk returns
# 1d, both mislabeled. A venue discipline can later lock 4h/1w for pairs
# on Binance where they truly exist. confirm_tf is only read for structure
# (EMA/ADX direction) and never for a second indicator set.
#
# Tunable signal gates per style. Defaults reproduce the legacy hardcoded
# thresholds exactly; rsi/adx/stoch/macd rules still act as FILTERS here.
# conf_gate is retained for the offline harness only; live evaluation uses
# SIGNAL_THRESHOLDS (per style x mode) below, which replaced the old
# conf_gate - aggression*6 arithmetic.
#   rsi_long/rsi_short: (lo, hi) healthy zones
#   adx_min:           minimum ADX for the strength bonus / trend filter
#   stoch_cut:         stochastic momentum cutoff (long: k > cut)
#   macd_atr_min:      0 = sign only (legacy); >0 requires |hist| >= mult*ATR
#   conf_gate:         legacy base gate, backtest comparison only
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

# Phase-2 approved confidence thresholds (style x mode). They replace the
# legacy formula `conf_gate - aggression*6`; aggression now lives entirely
# in this table so Safe/Normal/Aggressive mean the same thing everywhere.
#   scalping 72/64/56, intraday 76/68/60, swing 80/72/64
SIGNAL_THRESHOLDS = {
    "scalping": {"safe": 72.0, "normal": 64.0, "aggressive": 56.0},
    "intraday": {"safe": 76.0, "normal": 68.0, "aggressive": 60.0},
    "swing":    {"safe": 80.0, "normal": 72.0, "aggressive": 64.0},
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

# Metals, energy and index CFDs, in the order the pair picker shows them:
# metals first, then energy, then the indices. Yahoo tickers are the futures
# / index symbols that back each display name.
CFD_UNIVERSE = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "COPPER": "HG=F",
    "WTI": "CL=F",
    "UKOIL": "BZ=F",
    "NGAS": "NG=F",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "SPX500": "^GSPC",
    "GER40": "^GDAXI",
}

# Metals are quoted SPOT, not off the futures book. GC=F/SI=F carry a basis
# to spot of tens of dollars on gold, so a scalp entry priced off the future
# is nowhere near what a bullion broker fills; and the futures session thins
# out overnight and on CME holidays, which reads downstream as a dead feed
# while the user's platform is still quoting. Yahoo serves spot under the FX
# convention. CFD_UNIVERSE stays the fallback when a spot ticker is
# unavailable, so nothing breaks if one of these stops resolving.
CFD_SPOT = {
    "XAUUSD": "XAUUSD=X",
    "XAGUSD": "XAGUSD=X",
}

# Chart links only -- TradingView has no free data API and its feed may not
# be redistributed, so these symbols are used to build a chart URL and
# nothing else. Prices always come from the providers in app/data.
CFD_TRADINGVIEW = {
    "XAUUSD": "TVC:GOLD",
    "XAGUSD": "TVC:SILVER",
    "COPPER": "TVC:COPPER",
    "WTI": "TVC:USOIL",
    "UKOIL": "TVC:UKOIL",
    "NGAS": "TVC:NG",
    "US30": "TVC:DJI",
    "NAS100": "TVC:NASDAQ",
    "SPX500": "TVC:SPX",
    "GER40": "TVC:DAX",
}

# Display order for every pair picker, autopilot rotation and random pick:
# metals, oil and the indices first, then the FX majors, then crypto, with
# stocks last. ui.pair_keyboard() paginates this list directly, so this
# tuple is the single place the ordering is decided.
ALL_UNIVERSE = (
    list(CFD_UNIVERSE.keys())
    + list(FX_UNIVERSE.keys())
    + CRYPTO_UNIVERSE
    + STOCK_UNIVERSE
)

# Phase-2 ranked scanner: pairs analyzed per autopilot run, round-robined
# across ALL_UNIVERSE with a seeded per-chat shuffle so busy rows spread
# load. Budgeted against the Yahoo rate (~12 live calls/min): three pairs at
# ~3 timeframes each stays inside one short burst, and confirm/direction
# callbacks hit the kline cache on subsequent runs.
SCAN_BATCH = 3

# Phase-3E: static bid/ask spread estimates in basis points, per pair.
# The scheduler widens the stop by this amount and drops setups whose R:R
# drops below the style target. These are config-time constants because the
# scalable style needs quoted SL/TP/TARGET BEFORE the broker touch -- a live
# spread check at every minute would nuke the Yahoo rate budget.
SPREAD_DEFAULT_BPS = 25
SPREAD_ESTIMATES = {
    "BTCUSD": 2, "BTCUSDT": 2, "ETHUSD": 2, "ETHUSDT": 2,
    "BNBUSD": 3, "SOLUSD": 4, "XRPUSD": 4, "ADAUSD": 5,
    "DOGEUSD": 8, "AVAXUSD": 6, "LINKUSD": 6, "LTCUSD": 5,
    "TRXUSD": 8, "SUIUSD": 6, "ARBUSD": 5, "OPUSD": 5,
    "DOTUSD": 6, "ATOMUSD": 8, "NEARUSD": 8, "INJUSD": 8,
    "APTUSD": 8, "FILUSD": 10, "PEPEUSD": 12, "SHIBUSD": 15,
    "ENAUSD": 10, "ONDOUSD": 10, "AAVEUSD": 8, "UNIUSD": 8,
    "XLMUSD": 6, "VETUSD": 10, "ICPUSD": 10, "HBARUSD": 8,
}
# Realistic static spreads per asset class, in basis points. No free feed
# quotes bid/ask, so every non-crypto pair is priced off these: the stop is
# widened by the estimate and the setup is dropped when the widening eats
# the style's R:R. They replace the old flat 25 bps that over-widened every
# FX/metals stop and hid the spread from the R:R entirely.
SPREAD_CLASS_FALLBACK_BPS = {
    "forex": 2, "cfd": 3, "stock": 8, "crypto": 10,
}


def _pair_class(pair):
    s = pair.upper()
    if s in CRYPTO_UNIVERSE or s.endswith(("USDT", "USDC")):
        return "crypto"
    if s in CFD_UNIVERSE:
        return "cfd"
    if s in FX_UNIVERSE:
        return "forex"
    if s in STOCK_UNIVERSE:
        return "stock"
    return None


def spread_bps(pair):
    """Static spread estimate for a pair, in basis points of price.

    Precedence: per-pair table -> per-class fallback (FX/metals now use
    realistic single-digit bps instead of the flat 25) -> conservative
    default for unknown symbols."""
    s = pair.upper()
    if s in SPREAD_ESTIMATES:
        return SPREAD_ESTIMATES[s]
    cls = _pair_class(s)
    if cls in SPREAD_CLASS_FALLBACK_BPS:
        return SPREAD_CLASS_FALLBACK_BPS[cls]
    return SPREAD_DEFAULT_BPS


# Phase 2C: the live spread / ATR ratio ceiling per style. Above this the
# measured bid/ask spread is a material fraction of the bar's own range and
# the stop widening turns "set and forget" into "erase the account", so the
# gate rejects instead of printing a fictionally-wide R:R. Crypto has no
# spread basis (binance mid) and is never gated by this.
SPREAD_ATR_MAX = {"scalping": 0.35, "intraday": 0.20, "swing": 0.10}

# Phase 3A: scalping windows per asset class (UTC minutes-of-day). The
# north-star is that scalping needs the tightest possible live spread, which
# only exists inside London/NY liquidity. Crypto is 24/7 and never gated.
SCALP_SESSIONS = {
    "metals": {
        "windows": ((7 * 60, 16 * 60),),
        "preferred": ((12 * 60, 16 * 60),),
        "label": "the London/NY window (07:00\u201316:00 UTC)",
    },
    "fx_major": {
        "windows": ((7 * 60, 21 * 60),),
        "preferred": ((12 * 60, 16 * 60),),
        "label": "the London/NY window (07:00\u201321:00 UTC)",
    },
    "fx_other": {
        "windows": ((7 * 60, 16 * 60),),
        "preferred": ((12 * 60, 16 * 60),),
        "label": "the London window (07:00\u201316:00 UTC)",
    },
    # US index CFDs are priced off the cash index, which only prints during
    # the New York session -- scalping them at 08:00 UTC would analyse
    # yesterday's close.
    "index_us": {
        "windows": ((13 * 60 + 30, 20 * 60),),
        "preferred": ((13 * 60 + 30, 17 * 60),),
        "label": "the New York cash session (13:30\u201320:00 UTC)",
    },
}

# CFD symbols priced off a US cash index rather than a 24h futures book.
INDEX_US = {"US30", "NAS100", "SPX500"}

# Majors carry the deepest book; crosses/EMs trade thinner and stop earlier.
SCALP_CLASS_MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF",
                      "AUDUSD", "NZDUSD", "USDCAD"}


def scalp_class(pair):
    """Session class for scalping-window gating: 'metals', 'index_us',
    'fx_major', 'fx_other', 'crypto' or None.

    None means the instrument is never scalped, whatever the feed says --
    single stocks and unknown symbols. app/data/quality.py reads this as
    the scalping gate, so adding a class here enables the style.
    """
    s = pair.upper()
    if s in CRYPTO_UNIVERSE or s.endswith(("USDT", "USDC")):
        return "crypto"
    if s in INDEX_US:
        return "index_us"
    if s in CFD_UNIVERSE:  # metals, energy, GER40
        return "metals"
    if s in FX_UNIVERSE:
        return "fx_major" if s in SCALP_CLASS_MAJORS else "fx_other"
    return None

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

# Longest single grant accepted from any payment path (site rows included).
MAX_PLAN_MONTHS = 24
# Per-chat caps so one account cannot monopolise the scheduler or flood
# itself with alerts.
MAX_WATCHES = 10
WATCH_DAILY_LIMIT = 30
# Per-chat command budget (commands + button taps) per minute.
RATE_LIMIT_PER_MINUTE = 20
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
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT",
    "ATOMUSDT", "NEARUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "APTUSDT", "FILUSDT", "PEPEUSDT", "SHIBUSDT", "ENAUSDT", "ONDOUSDT",
    "AAVEUSDT", "UNIUSDT", "XLMUSDT", "VETUSDT", "ICPUSDT", "HBARUSDT",
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
    "BTCUSDT": "https://www.blockchain.com/explorer/transactions/btc",
    "ETHUSDT": "https://etherscan.io",
    "BNBUSDT": "https://bscscan.com",
    "SOLUSDT": "https://solscan.io",
    "XRPUSDT": "https://xrpscan.com",
    "ADAUSDT": "https://cardanoscan.io",
    "DOGEUSDT": "https://dogechain.info",
    "AVAXUSDT": "https://snowtrace.io",
    "LINKUSDT": "https://linkpool.io",
    "LTCUSDT": "https://litecoinspace.org",
    "TRXUSDT": "https://tronscan.org",
    "SUIUSDT": "https://suiscan.xyz",
    "ARBUSDT": "https://arbiscan.io",
    "OPUSDT": "https://optimistic.etherscan.io",
}

COINGECKO_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "SOLUSDT": "solana",
    "XRPUSDT": "ripple",
    "ADAUSDT": "cardano",
    "DOGEUSDT": "dogecoin",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "LTCUSDT": "litecoin",
    "DOTUSDT": "polkadot",
    "TRXUSDT": "tron",
    "ATOMUSDT": "cosmos",
    "NEARUSDT": "near",
    "ARBUSDT": "arbitrum",
    "OPUSDT": "optimism",
    "INJUSDT": "injective-protocol",
    "SUIUSDT": "sui",
    "APTUSDT": "aptos",
    "FILUSDT": "filecoin",
    "PEPEUSDT": "pepe",
    "SHIBUSDT": "shiba-inu",
    "ENAUSDT": "ethena",
    "ONDOUSDT": "ondo-finance",
    "AAVEUSDT": "aave",
    "UNIUSDT": "uniswap",
    "XLMUSDT": "stellar",
    "VETUSDT": "vechain",
    "ICPUSDT": "internet-computer",
    "HBARUSDT": "hedera-hashgraph",
}
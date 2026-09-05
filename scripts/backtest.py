"""Offline backtest harness (DEV ONLY -- never imported by app/ or deployed).

Replays the exact live decision path with zero lookahead:
  direction  via strategy._direction_from      (same function as live)
  confidence via strategy._confidence_from     (same function, candidate gates)
  SL/TP      via strategy._spec                (same function, normal mode)
  levels     via levels.swing_levels/nearest   (same functions, past bars only)

Honesty guards (awesome-quant: honest-signals + purgedcv concepts):
  * random-direction baseline on the same bars -> lift vs coin flip
  * deflated Sharpe helpers live in tune.py (needs all trial Sharpes)

Usage:
  python scripts/backtest.py --style intraday
  python scripts/backtest.py --style scalping --pair BTCUSD --refresh
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import constants  # noqa: E402
from app.analysis import indicators as ind  # noqa: E402
from app.analysis import levels as lv  # noqa: E402
from app.analysis import patterns as pat  # noqa: E402
from app.analysis import regime as rg  # noqa: E402
from app.analysis import strategy as strat  # noqa: E402
from app.data.provider import BinanceProvider, YahooProvider  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

SEEDS = (
    ("BTCUSD", constants.KIND_CRYPTO),
    ("EURUSD", constants.KIND_FOREX),
    ("AAPL", constants.KIND_STOCK),
    ("XAUUSD", constants.KIND_CFD),
)

# months of history per style (Yahoo caps intraday at 60d; see _yahoo_range)
SPAN_DAYS = {"scalping": 90, "intraday": 180, "swing": 1095}
WALK_STEP = {"scalping": 24, "intraday": 8, "swing": 1}
MAXHOLD = {"scalping": 72, "intraday": 64, "swing": 12}
WARMUP = 300


def _cache_path(pair, tf, span):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"{pair}_{tf}_{span}.json")


def _load_cache(path, max_age_s=7 * 86400):
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age_s:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path, candles):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(candles, f)


def _fetch_binance(pair, interval, days):
    prov = BinanceProvider()
    venue = constants.binance_symbol(pair)
    step_ms = constants.INTERVALS[interval] * 1000
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86400 * 1000)
    out, cursor = [], start_ms
    while True:
        chunk = prov._get("/api/v3/klines", {
            "symbol": venue, "interval": interval, "limit": 1000,
            "startTime": cursor,
        })
        if not chunk:
            break
        for k in chunk:
            out.append({"ts": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]),
                        "volume": float(k[5])})
        cursor = int(chunk[-1][0]) + step_ms
        if len(chunk) < 1000 or cursor >= end_ms or len(out) > days * 86400 / constants.INTERVALS[interval] + 1100:
            break
    return out


_YAHOO_RANGE = {
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "1h": ("1h", "730d"),
    "1d": ("1d", "5y"),
}


def _fetch_yahoo(pair, kind, interval):
    prov = YahooProvider(kind=kind)
    prov.RANGE_MAP = dict(YahooProvider.RANGE_MAP)
    if interval in _YAHOO_RANGE:
        prov.RANGE_MAP[interval] = _YAHOO_RANGE[interval]
    sym = prov.resolve_symbol(pair) if kind != constants.KIND_FOREX else pair
    if kind == constants.KIND_FOREX:
        sym = pair if pair.endswith("=X") else pair + "=X"
    elif kind == constants.KIND_CFD:
        sym = constants.CFD_UNIVERSE[pair]
    elif kind == constants.KIND_STOCK:
        sym = pair
    return prov.fetch_klines(sym, interval, limit=5000)


def load_series(pair, kind, style, refresh=False):
    """Return (base_candles, dir_candles) oldest->newest for a style."""
    sp = constants.STYLE_PROFILE[style]
    base_tf, dir_tf = sp["base_tf"], sp["direction_tf"]
    span = SPAN_DAYS[style]

    def one(tf):
        key_span = f"{span}d"
        path = _cache_path(pair, tf, key_span)
        data = None if refresh else _load_cache(path)
        if data is None:
            if kind == constants.KIND_CRYPTO:
                data = _fetch_binance(pair, tf, span)
            else:
                data = _fetch_yahoo(pair, kind, tf)
            _save_cache(path, data)
        return data

    return one(base_tf), one(dir_tf)


_FEATS = {}


def features(pair, kind, style, refresh=False):
    key = (pair, style)
    if key in _FEATS and not refresh:
        return _FEATS[key]
    base, direction = load_series(pair, kind, style, refresh)
    closes = [c["close"] for c in base]
    ml, ms, mh = ind.macd(closes)
    bb_mid, _, _ = ind.bollinger(closes)
    st_k, _ = ind.stochastic(base)
    feats = {
        "base": base, "direction": direction,
        "ema9": ind.ema(closes, 9), "ema21": ind.ema(closes, 21),
        "ema50": ind.ema(closes, 50), "rsi": ind.rsi(closes),
        "atr": ind.atr(base), "macd_h": mh, "bb_mid": bb_mid,
        "stoch_k": st_k, "adx": ind.adx(base),
        "patterns": pat.pattern_bias(base),
        "roll": ind.realized_vol(closes),
    }
    _FEATS[key] = feats
    return feats


def _levels_at(direction, ts, lookback):
    window = [c for c in direction if c["ts"] <= ts][-lookback:]
    if len(window) < 30:
        return [], []
    supports, resistances = lv.swing_levels(window)
    return supports, resistances


def simulate(pair, kind, style, gates, seed=12345, refresh=False,
               confluence=frozenset()):
    """Walk history firing the live decision path; return dict of metrics.

    confluence: subset of {"patterns", "vol", "session"} replaying the same
    helpers analyze() applies live (sentiment excluded: no historical news).
    """
    sp = constants.STYLE_PROFILE[style]
    mp = constants.MODE_PROFILE["normal"]  # backtest in normal mode only
    tf_s = constants.INTERVALS[sp["base_tf"]]
    gate = gates["conf_gate"] - mp["aggression"] * 6
    feats = features(pair, kind, style, refresh)
    base, direction = feats["base"], feats["direction"]
    n = len(base)
    if n < WARMUP + 50:
        return {"pair": pair, "style": style, "n": 0, "note": f"only {n} bars"}
    min_gap = max(1, math.ceil(sp["min_gap_s"] / tf_s))
    maxhold = MAXHOLD[style]
    step = WALK_STEP[style]
    lookback = sp["candles"]

    strat_rs, base_rs = [], []
    strat_confs = []
    rng = random.Random(seed)
    last_fire = -10 ** 9
    for t in range(WARMUP, n - maxhold - 1, step):
        f = {
            "ema21": feats["ema21"][t], "ema50": feats["ema50"][t],
            "adx": feats["adx"][t], "macd_hist": feats["macd_h"][t],
            "rsi": feats["rsi"][t], "bb_mid": feats["bb_mid"][t],
            "stoch_k": feats["stoch_k"][t], "close": base[t]["close"],
            "atr": feats["atr"][t],
        }
        if any(v is None for v in f.values()):
            continue
        side_dir, _, _ = strat._direction_from(
            feats["ema9"][t], f["ema21"], f["ema50"], f["macd_hist"])
        if side_dir == "neutral":
            continue
        side = "long" if side_dir == "up" else "short"
        conf = strat._confidence_from(side, f, gates, [])
        conf = min(100.0, conf * (0.9 + mp["aggression"] * 0.12))
        # confluence replay mirrors strategy.analyze() order exactly
        if "patterns" in confluence:
            bias = feats["patterns"][t]
            if bias != 0:
                agrees = ((bias == 1 and side == "long")
                          or (bias == -1 and side == "short"))
                conf += strat.PATTERN_POINTS if agrees else -strat.PATTERN_POINTS
        if "vol" in confluence:
            conf = rg.apply_vol_regime(
                conf, rg.vol_ratio(feats["roll"], t), [])
        if "session" in confluence:
            conf = rg.apply_session(conf, kind, base[t]["ts"], [])
        conf = max(0.0, min(100.0, conf))
        if conf < gate:
            continue
        if t - last_fire < min_gap:
            continue
        last_fire = t
        supports, resistances = _levels_at(direction, base[t]["ts"], lookback)
        sup_lv, res_lv = lv.nearest(base[t]["close"], supports, resistances)
        spec = strat._spec(side, base[t]["close"], f["atr"], sup_lv, res_lv, mp)
        sl_dist = abs(base[t]["close"] - spec["sl"])
        if sl_dist <= 0:
            continue
        r = _outcome(base, t, side, base[t]["close"], spec["sl"], spec["tp1"],
                     mp["rr"], maxhold)
        strat_rs.append(r)
        strat_confs.append(conf)
        # random-direction baseline on the same bar (honest-signals concept)
        bside = "long" if rng.random() < 0.5 else "short"
        if bside == "long":
            bsl, btp = base[t]["close"] - sl_dist, base[t]["close"] + sl_dist * mp["rr"]
        else:
            bsl, btp = base[t]["close"] + sl_dist, base[t]["close"] - sl_dist * mp["rr"]
        base_rs.append(_outcome(base, t, bside, base[t]["close"], bsl, btp,
                               mp["rr"], maxhold))
    return _metrics(pair, style, strat_rs, base_rs, strat_confs)


def _outcome(bars, t, side, entry, sl, tp, rr, maxhold):
    end = min(len(bars) - 1, t + maxhold)
    for j in range(t + 1, end + 1):
        h, lo = bars[j]["high"], bars[j]["low"]
        if side == "long":
            hit_sl = lo <= sl
            hit_tp = h >= tp
            if hit_sl and hit_tp:
                return -1.0  # conservative: stopped first
            if hit_sl:
                return -1.0
            if hit_tp:
                return rr
        else:
            hit_sl = h >= sl
            hit_tp = lo <= tp
            if hit_sl and hit_tp:
                return -1.0
            if hit_sl:
                return -1.0
            if hit_tp:
                return rr
    dist = abs(entry - sl)
    if dist <= 0:
        return 0.0
    px = bars[end]["close"]
    return (px - entry) / dist if side == "long" else (entry - px) / dist


def _metrics(pair, style, rs, base_rs, confs=None):
    n = len(rs)
    if n == 0:
        return {"pair": pair, "style": style, "n": 0}
    wins = [r for r in rs if r > 0]
    wins = [r for r in rs if r > 0]
    gross_w = sum(wins)
    gross_l = -sum(r for r in rs if r < 0)
    mean = statistics.fmean(rs)
    std = statistics.pstdev(rs) if n > 1 else 0.0
    sharpe = mean / std * math.sqrt(n) if std > 0 else 0.0
    curve, peak, maxdd = 0.0, 0.0, 0.0
    for r in rs:
        curve += r
        peak = max(peak, curve)
        maxdd = max(maxdd, peak - curve)
    bwins = sum(1 for r in base_rs if r > 0)
    out = {
        "pair": pair, "style": style, "n": n,
        "win_pct": 100.0 * len(wins) / n,
        "profit_factor": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "expectancy_r": mean,
        "maxdd_r": maxdd,
        "sharpe": sharpe,
        "base_win_pct": 100.0 * bwins / len(base_rs) if base_rs else 0.0,
        "lift_pp": 100.0 * len(wins) / n - (100.0 * bwins / len(base_rs) if base_rs else 0.0),
        "rs": rs,
        "base_rs": base_rs,
    }
    if confs is not None:
        out["confs"] = confs
    return out


def aggregate(results):
    rs, base_rs, confs = [], [], []
    for m in results:
        rs.extend(m.get("rs", []))
        base_rs.extend(m.get("base_rs", []))
        confs.extend(m.get("confs", []))
    agg = _metrics("ALL", results[0]["style"] if results else "?", rs, base_rs,
                   confs or None)
    return agg


def fmt_row(m):
    if m.get("n", 0) == 0:
        return f"{m['pair']:8s} no trades"
    pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "inf"
    return (f"{m['pair']:8s} n={m['n']:4d} win={m['win_pct']:5.1f}% "
            f"PF={pf:>5s} exp={m['expectancy_r']:+.2f}R "
            f"DD={m['maxdd_r']:.1f}R Sh={m['sharpe']:+.2f} "
            f"base={m['base_win_pct']:.1f}% lift={m['lift_pp']:+.1f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="intraday",
                    choices=["scalping", "intraday", "swing"])
    ap.add_argument("--pair", default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--confluence", default="",
                    help="comma list of patterns,vol,session")
    args = ap.parse_args()
    conf = frozenset(c for c in args.confluence.split(",") if c)
    pairs = [(p, k) for p, k in SEEDS if args.pair is None or p == args.pair]
    gates = constants.SIGNAL_GATES[args.style]
    print(f"style={args.style} confluence={sorted(conf) or 'off'} gates={gates}")
    results = []
    for pair, kind in pairs:
        m = simulate(pair, kind, args.style, gates, refresh=args.refresh,
                     confluence=conf)
        results.append(m)
        print(fmt_row(m))
    print(fmt_row(aggregate(results)))


if __name__ == "__main__":
    main()
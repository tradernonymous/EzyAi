"""Coordinate-descent tuner over SIGNAL_GATES (DEV ONLY).

For each style, sweeps one axis at a time (2 rounds), keeping the change
only if the aggregate objective improves. Objective (lexicographic):
  1. deflated Sharpe ratio across all evaluated candidates (purgedcv concept:
     accounts for multiple testing; K = number of candidates tried)
  2. profit factor
  3. trade count (must clear MIN_TRADES or the candidate is rejected)

A tuned set ships only if it beats BOTH the current defaults AND the
random-direction baseline. Results JSON -> scripts/.cache/tune_results.json.

Usage: python scripts/tune.py [--style intraday] [--refresh]
"""
import argparse
import copy
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import constants  # noqa: E402

import backtest  # noqa: E402  (sibling script)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
MIN_TRADES = 30
GAMMA = 0.5772156649  # Euler-Mascheroni, for the DSR null


def AXES():
    return [
        ("adx_min", [18.0, 22.0, 25.0, 28.0, 32.0]),
        ("rsi", [((45.0, 68.0), (30.0, 55.0)),
                 ((40.0, 65.0), (28.0, 52.0)),
                 ((50.0, 70.0), (32.0, 58.0)),
                 ((35.0, 70.0), (25.0, 60.0))]),
        ("conf_gate", [58.0, 62.0, 66.0, 70.0]),
        ("stoch_cut", [45.0, 50.0, 55.0]),
        ("macd_atr_min", [0.0, 0.05, 0.10]),
    ]


def apply_axis(gates, axis, value):
    g = copy.deepcopy(gates)
    if axis == "rsi":
        g["rsi_long"], g["rsi_short"] = value
    else:
        g[axis] = value
    return g


def summarize(style, gates, refresh=False):
    results = [backtest.simulate(p, k, style, gates) for p, k in backtest.SEEDS]
    agg = backtest.aggregate(results)
    return agg


def trial_sharpe(agg):
    return agg.get("sharpe", 0.0) if agg.get("n", 0) >= MIN_TRADES else float("-inf")


def deflated_sharpe(sr_hat, t, rs, trials):
    """Bailey & Lopez de Prado (2014) DSR. trials = Sharpe of every
    candidate evaluated (the multiple-testing correction)."""
    if t < MIN_TRADES or len(trials) < 3:
        return None
    var = statistics.pvariance(trials)
    if var <= 0:
        return None
    nd = statistics.NormalDist()
    k = len(trials)
    sr0 = math.sqrt(var) * ((1 - GAMMA) * nd.inv_cdf(1 - 1 / k)
                            + GAMMA * nd.inv_cdf(1 - 1 / (k * math.e)))
    skew = _skew(rs)
    kurt = _kurt(rs)
    denom = math.sqrt(max(1e-12, 1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2))
    return nd.cdf((sr_hat - sr0) * math.sqrt(max(t - 1, 1)) / denom)


def _skew(xs):
    n = len(xs)
    m = statistics.fmean(xs)
    s = statistics.pstdev(xs)
    if s == 0 or n < 3:
        return 0.0
    return sum((x - m) ** 3 for x in xs) / n / s ** 3


def _kurt(xs):
    n = len(xs)
    m = statistics.fmean(xs)
    s = statistics.pstdev(xs)
    if s == 0 or n < 4:
        return 3.0
    return sum((x - m) ** 4 for x in xs) / n / s ** 4


def objective(agg, trials):
    if agg.get("n", 0) < MIN_TRADES:
        return (-1.0, 0.0, 0)
    dsr = deflated_sharpe(agg["sharpe"], agg["n"], agg["rs"], trials)
    dsr = dsr if dsr is not None else -1.0
    pf = agg["profit_factor"] if agg["profit_factor"] != float("inf") else 99.0
    return (dsr, pf, agg["n"])


def tune_style(style, refresh=False):
    base = copy.deepcopy(constants.SIGNAL_GATES[style])
    best = base
    best_agg = summarize(style, best, refresh)
    history = [(dict(best), best_agg)]
    print(f"[{style}] defaults: {backtest.fmt_row(best_agg)}")
    for rnd in (1, 2):
        for axis, values in AXES():
            for v in values:
                cand = apply_axis(best, axis, v)
                if cand == best:
                    continue
                agg = summarize(style, cand)
                history.append((dict(cand), {k: val for k, val in agg.items()
                                             if k not in ("rs", "base_rs", "confs")}))
                trials = [trial_sharpe(h[1]) for h in history
                          if trial_sharpe(h[1]) != float("-inf")]
                if objective(agg, trials) > objective(best_agg, trials):
                    best, best_agg = cand, agg
                    print(f"[{style}] r{rnd} {axis}={v}: {backtest.fmt_row(agg)}")
    trials = [trial_sharpe(h[1]) for h in history
              if trial_sharpe(h[1]) != float("-inf")]
    ob = objective(best_agg, trials)
    od = objective(history[0][1], trials)
    return {"style": style, "defaults": base, "tuned": best,
            "default_agg": _slim(history[0][1]), "tuned_agg": _slim(best_agg),
            "default_obj": ob if best == base else od,
            "tuned_obj": ob, "beats_defaults": ob > od,
            "beats_baseline": best_agg.get("lift_pp", 0) > 0}


def _slim(agg):
    return {k: v for k, v in agg.items() if k not in ("rs", "base_rs", "confs")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default=None,
                    choices=["scalping", "intraday", "swing"])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    styles = [args.style] if args.style else ["scalping", "intraday", "swing"]
    out = {}
    for style in styles:
        out[style] = tune_style(style, args.refresh)
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, "tune_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    print("\n==== SUMMARY ====")
    for style in styles:
        r = out[style]
        print(f"-- {style}: defaults {backtest.fmt_row(r['default_agg'])}")
        print(f"   tuned    {backtest.fmt_row(r['tuned_agg'])}")
        print(f"   tuned gates: {r['tuned']}")
        print(f"   beats defaults: {r['beats_defaults']}  "
              f"beats baseline (lift>0): {r['beats_baseline']}")


if __name__ == "__main__":
    main()
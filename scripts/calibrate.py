"""Confidence calibration (DEV ONLY).

Builds per-style raw-score -> observed-hit-rate tables from the backtest
trade log so a displayed "52" means similar past setups won ~52%.

  python scripts/calibrate.py --report          # bucket hit-rate table
  python scripts/calibrate.py --emit            # print constants-ready table
                                                # + holdout validation

Method: chronological 70/30 split per pair (train/holdout). Bayesian
smoothing toward the global mean (prior weight M) for sparse buckets,
then isotonic (pool-adjacent-violators) enforcement so higher buckets
never map lower. Holdout check: buckets with >=30 holdout samples must
land within 10pp of predicted.

FINDING 2026-09: raw scores do NOT rank outcomes. Intraday train buckets
hit 43-50% flat across 60-100 (841/1581 trades sit in [85,90)); PAV merges
everything to ~44.7%; per-feature |corr(win, x)| < 0.07 for conf, ADX, RSI
margin, MACD hist and stoch margin on all styles. The score is a
participation checklist (12 + fixed bonuses, most fires land ~86.7), and
gating on it works -- the edge is structural via reward:risk (PF 1.2-1.4)
-- but mapping it to win-rate would print ~45 for nearly every signal
and break the tuned gates. So: NO calibration table ships. Revisit only
after replacing binary bonus checklists with a continuous re-score
(margins, not checklists), then re-tune, then re-run this script.
"""
import argparse
import copy
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import constants  # noqa: E402

import backtest  # noqa: E402

EDGES = [0, 60, 65, 70, 75, 80, 85, 90, 101]
PRIOR_M = 30
HOLDOUT_TOL_PP = 10.0
HOLDOUT_MIN_N = 30


def bucketize(confs, rs, edges=EDGES):
    buckets = [[] for _ in range(len(edges) - 1)]
    for c, r in zip(confs, rs):
        for i in range(len(edges) - 1):
            if edges[i] <= c < edges[i + 1]:
                buckets[i].append(1.0 if r > 0 else 0.0)
                break
    return buckets


def smooth_rates(buckets, global_mean, m=PRIOR_M):
    return [((sum(b) + m * global_mean) / (len(b) + m)) if True else global_mean
            for b in buckets]


def fit_table(train_confs, train_rs, edges=EDGES):
    buckets = bucketize(train_confs, train_rs, edges)
    n_total = sum(len(b) for b in buckets)
    gmean = (sum(sum(b) for b in buckets) / n_total) if n_total else 0.5
    rates = smooth_rates(buckets, gmean)
    weights = [len(b) + PRIOR_M for b in buckets]
    # PAV over per-bucket rates with weights
    seq = list(rates)
    wts = list(weights)
    i = 0
    spans = [[j] for j in range(len(seq))]
    while i < len(seq) - 1:
        if seq[i] <= seq[i + 1]:
            i += 1
            continue
        w = wts[i] + wts[i + 1]
        seq[i:i + 2] = [(seq[i] * wts[i] + seq[i + 1] * wts[i + 1]) / w]
        wts[i:i + 2] = [w]
        spans[i:i + 2] = [spans[i] + spans[i + 1]]
        if i > 0:
            i -= 1
    flat = [0.0] * len(rates)
    for val, span in zip(seq, spans):
        for j in span:
            flat[j] = val
    counts = [len(b) for b in buckets]
    return {"edges": list(edges), "hit": [round(v, 4) for v in flat],
            "n": counts, "global": round(gmean, 4),
            "n_total": n_total}


def validate(table, hold_confs, hold_rs):
    buckets = bucketize(hold_confs, hold_rs, table["edges"])
    problems = []
    checked = 0
    for i, b in enumerate(buckets):
        if len(b) < HOLDOUT_MIN_N:
            continue
        checked += 1
        obs = sum(b) / len(b)
        pred = table["hit"][i]
        if abs(obs - pred) * 100 > HOLDOUT_TOL_PP:
            problems.append((i, len(b), obs, pred))
    return checked, problems


def load_trades(style, refresh=False):
    gates = constants.SIGNAL_GATES[style]
    per_pair = []
    for pair, kind in backtest.SEEDS:
        m = backtest.simulate(pair, kind, style, gates, refresh=refresh)
        per_pair.append((pair, m.get("confs", []), m.get("rs", [])))
    return per_pair


def split_chrono(confs, rs, frac=0.7):
    k = int(len(confs) * frac)
    return (confs[:k], rs[:k]), (confs[k:], rs[k:])


def report(style, refresh=False):
    per_pair = load_trades(style, refresh)
    train_c, train_r, hold_c, hold_r = [], [], [], []
    for _, confs, rs in per_pair:
        (tc, tr), (hc, hr) = split_chrono(confs, rs)
        train_c += tc
        train_r += tr
        hold_c += hc
        hold_r += hr
    table = fit_table(train_c, train_r)
    print(f"-- {style}: train n={len(train_c)} holdout n={len(hold_c)}")
    buckets = bucketize(train_c, train_r)
    for i in range(len(EDGES) - 1):
        b = buckets[i]
        raw = (sum(b) / len(b) * 100) if b else float("nan")
        print(f"   [{EDGES[i]:3d},{EDGES[i+1]:3d}) n={len(b):5d} "
              f"raw_hit={raw:5.1f}% -> cal={table['hit'][i]*100:5.1f}%")
    checked, problems = validate(table, hold_c, hold_r)
    print(f"   holdout buckets checked: {checked}, violations: {len(problems)}")
    for i, n, obs, pred in problems:
        print(f"   !! bucket {i} n={n} observed={obs*100:.1f}% predicted={pred*100:.1f}%")
    return table, (hold_c, hold_r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--style", default=None,
                    choices=["scalping", "intraday", "swing"])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    styles = [args.style] if args.style else ["scalping", "intraday", "swing"]
    tables = {}
    ok = True
    for style in styles:
        table, _ = report(style, args.refresh)
        tables[style] = table
    if args.emit:
        print("\nCALIBRATION = {")
        for style in styles:
            t = tables[style]
            print(f'    "{style}": {{"edges": {t["edges"]}, "hit": {t["hit"]}, '
                  f'"n": {t["n"]}}},')
        print("}")
    if not (args.report or args.emit):
        ap.print_help()


if __name__ == "__main__":
    main()
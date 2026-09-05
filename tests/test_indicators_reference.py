"""Cross-validate app/analysis/indicators.py against the `ta` reference library.

Source: awesome-quant "Technical Indicators" (bukosabino/ta, MIT).

`ta` is a DEV-ONLY dependency (requirements-dev.txt) and must never be
imported at runtime -- see test_reference_lib_is_dev_only.

Findings (2026-09-05, ta 0.11.0, probed on seeded random walks):
- SMA / Bollinger / Stochastic: bit-identical on trailing values.
- EMA / RSI / MACD / ATR: agree to ~1e-9 relative after warmup
  (different seeding conventions: SMA-seed vs recursive ewm seed).
- ADX: `ta` is NOT used -- its implementation feeds yesterday's DX into
  today's ADX (ta/trend.py ADXIndicator.adx, off-by-one) and drops the
  tail sample in its smoothing loops. Ours implements textbook Wilder's
  ADX, so ADX is checked against an independent Wilder implementation
  written plainly in this file instead.
"""
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import ta as ref  # noqa: E402  (dev-only reference)

from app.analysis import indicators as ours  # noqa: E402

PERIODS_EMA = (9, 21, 50)  # the exact periods strategy.py consumes
N = 400  # long enough that warmup seeding has decayed: (13/14)^400 ~ 1e-13


def gen(seed, start=100.0, drift=0.0, vol=0.01, n=N):
    rng = random.Random(seed)
    closes, candles = [], []
    p = start
    for _ in range(n):
        p *= 1 + drift + rng.gauss(0, vol)
        o = p * (1 + rng.gauss(0, vol * 0.2))
        h = max(o, p) * (1 + abs(rng.gauss(0, vol * 0.3)))
        lo = min(o, p) * (1 - abs(rng.gauss(0, vol * 0.3)))
        closes.append(p)
        candles.append({"open": o, "high": h, "low": lo, "close": p})
    return closes, candles


def frame(candles):
    return pd.DataFrame(candles)


def tail(xs, k=50):
    return [x for x in xs[-k:] if x is not None]


def test_sma_matches_reference():
    for seed in (7, 42):
        closes, _ = gen(seed)
        df = frame([{"close": c} for c in closes])
        got = tail(ours.sma(closes, 20))
        want = ref.trend.SMAIndicator(df["close"], 20).sma_indicator().iloc[-50:]
        for g, w in zip(got, want):
            assert g == w or math.isclose(g, w, rel_tol=1e-12, abs_tol=1e-12)


def test_ema_matches_reference():
    for seed in (7, 42):
        closes, _ = gen(seed)
        df = frame([{"close": c} for c in closes])
        for period in PERIODS_EMA:
            got = tail(ours.ema(closes, period), k=20)
            want = ref.trend.EMAIndicator(df["close"], period).ema_indicator().iloc[-20:]
            for g, w in zip(got, want):
                assert math.isclose(g, w, rel_tol=1e-6), (seed, period, g, w)


def test_rsi_matches_reference():
    for seed in (7, 42):
        closes, _ = gen(seed)
        df = frame([{"close": c} for c in closes])
        got = tail(ours.rsi(closes), k=20)
        want = ref.momentum.RSIIndicator(df["close"], 14).rsi().iloc[-20:]
        for g, w in zip(got, want):
            assert math.isclose(g, w, rel_tol=1e-6, abs_tol=1e-6), (seed, g, w)


def test_macd_matches_reference():
    for seed, start in ((7, 100.0), (42, 100.0), (99, 60000.0)):
        closes, _ = gen(seed, start=start)
        df = frame([{"close": c} for c in closes])
        ml, ms, mh = ours.macd(closes)
        rm = ref.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
        for g, w in (
            (ml[-1], float(rm.macd().iloc[-1])),
            (ms[-1], float(rm.macd_signal().iloc[-1])),
            (mh[-1], float(rm.macd_diff().iloc[-1])),
        ):
            assert math.isclose(g, w, rel_tol=1e-6, abs_tol=1e-9), (seed, g, w)


def test_atr_matches_reference():
    for seed, start in ((7, 100.0), (42, 100.0), (99, 1.08)):
        closes, candles = gen(seed, start=start)
        df = frame(candles)
        got = ours.atr(candles)[-1]
        want = float(ref.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], 14).average_true_range().iloc[-1])
        assert math.isclose(got, want, rel_tol=1e-9), (seed, got, want)


def test_bollinger_matches_reference():
    for seed in (7, 42):
        closes, _ = gen(seed)
        df = frame([{"close": c} for c in closes])
        mid, up, lo = ours.bollinger(closes, 20, 2.0)
        rb = ref.volatility.BollingerBands(df["close"], 20, 2)
        for g, w in (
            (mid[-1], float(rb.bollinger_mavg().iloc[-1])),
            (up[-1], float(rb.bollinger_hband().iloc[-1])),
            (lo[-1], float(rb.bollinger_lband().iloc[-1])),
        ):
            assert math.isclose(g, w, rel_tol=1e-12), (seed, g, w)


def test_stochastic_matches_reference():
    for seed in (7, 42):
        _, candles = gen(seed)
        df = frame(candles)
        k, d = ours.stochastic(candles, 14, 3)
        rs = ref.momentum.StochasticOscillator(df["high"], df["low"], df["close"], 14, 3)
        assert math.isclose(k[-1], float(rs.stoch().iloc[-1]), rel_tol=1e-12)
        assert math.isclose(d[-1], float(rs.stoch_signal().iloc[-1]), rel_tol=1e-12)


def textbook_wilder_adx(candles, period=14):
    """Plain, obviously-correct Wilder ADX written independently of
    app/analysis/indicators.py for cross-checking."""
    n = len(candles)
    tr, pdm, mdm = [], [], []
    for i in range(1, n):
        h, lo, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        up = candles[i]["high"] - candles[i - 1]["high"]
        dn = candles[i - 1]["low"] - candles[i]["low"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)

    def wilder(xs):
        s = sum(xs[:period]) / period
        out = [None] * len(xs)
        out[period - 1] = s
        for j in range(period, len(xs)):
            s = (s * (period - 1) + xs[j]) / period
            out[j] = s
        return out

    str_, sp, sm = wilder(tr), wilder(pdm), wilder(mdm)
    # tr[j] <-> candle j+1 ; first smoothed value (j=period-1) <-> candle `period`
    dx_at = {}
    for j in range(period - 1, len(tr)):
        if str_[j]:
            p = 100.0 * sp[j] / str_[j]
            m = 100.0 * sm[j] / str_[j]
            dx_at[j + 1] = 0.0 if p + m == 0 else 100.0 * abs(p - m) / (p + m)
    bars = sorted(dx_at)
    out = [None] * n
    a = sum(dx_at[b] for b in bars[:period]) / period
    out[bars[period - 1]] = a
    for b in bars[period:]:
        a = (a * (period - 1) + dx_at[b]) / period
        out[b] = a
    return out


def test_adx_matches_textbook_wilder():
    for seed in (7, 42, 123):
        _, candles = gen(seed)
        got = ours.adx(candles, 14)
        want = textbook_wilder_adx(candles, 14)
        pairs = [(g, w) for g, w in zip(got[-100:], want[-100:])
                 if g is not None and w is not None]
        assert len(pairs) > 80
        for g, w in pairs:
            assert math.isclose(g, w, rel_tol=1e-9, abs_tol=1e-9), (seed, g, w)


def test_adx_first_valid_index_and_bounds():
    _, candles = gen(7)
    out = ours.adx(candles, 14)
    first = next(i for i, v in enumerate(out) if v is not None)
    assert first == 2 * 14 - 1  # seeded after `period` DX values
    assert all(v is None for v in out[:first])
    assert all(0.0 <= v <= 100.0 for v in out[first:])


def test_reference_lib_is_dev_only():
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    for line in req.read_text().splitlines():
        name = re.split(r"[<>=!~\s\[]", line.strip(), 1)[0].lower()
        assert name != "ta", "ta must stay dev-only (requirements-dev.txt)"
    app_dir = Path(__file__).resolve().parent.parent / "app"
    for py in app_dir.rglob("*.py"):
        for line in py.read_text().splitlines():
            s = line.strip()
            assert not re.match(r"(import ta\b|from ta[\s.])", s), \
                f"runtime import of ta in {py.name}: {s}"

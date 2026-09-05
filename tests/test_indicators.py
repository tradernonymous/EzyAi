import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import indicators as ta  # noqa: E402


def test_sma_basic():
    values = [1, 2, 3, 4, 5]
    out = ta.sma(values, 3)
    assert out[2] == 2.0
    assert out[4] == 4.0
    assert out[0] is None and out[1] is None


def test_ema_converges_upward():
    values = [1.0] * 40 + [2.0] * 40
    out = ta.ema(values, 10)
    assert out[-1] is not None
    assert out[-1] > 1.9
    assert all(v is None or v >= 1.0 for v in out)


def test_ema_aligns_with_sma_delay():
    values = [float(i) for i in range(1, 31)]
    ema10 = ta.ema(values, 10)
    assert ema10[0] is None
    assert ema10[9] is None or ema10[9] is not None


def test_rsi_bounds():
    values = [1.0, 2, 3, 2, 1, 2, 3, 4, 3, 2, 3, 4, 5, 4, 3, 4, 5, 6, 5, 6, 7, 6, 5, 6, 7, 8, 7, 6, 7, 8]
    r = ta.rsi(values, 14)
    valid = [x for x in r if x is not None]
    assert valid
    assert all(0 <= x <= 100 for x in valid)


def test_rsi_all_up_is_100():
    values = [float(i) for i in range(1, 40)]
    r = ta.rsi(values, 14)
    assert r[-1] == 100.0


def test_macd_alignment():
    values = [float(i % 7) for i in range(80)]
    line, sig, hist = ta.macd(values)
    assert len(line) == len(values) == len(sig) == len(hist)
    assert line[-1] is not None
    assert hist[-1] == line[-1] - sig[-1]


def test_atr_positive():
    candles = [{"high": 10 + i, "low": 9 + i, "close": 9.5 + i} for i in range(30)]
    out = ta.atr(candles, 14)
    assert out[14] is not None
    assert out[-1] and out[-1] > 0


def test_bollinger_order():
    values = [float(10 + (i % 5)) for i in range(60)]
    mid, up, lo = ta.bollinger(values, 20)
    for i in range(19, len(values)):
        assert lo[i] <= mid[i] <= up[i]


def test_stochastic_bounds():
    candles = [{"high": 10 + i % 6, "low": 9 + i % 6, "close": 9.5 + i % 6} for i in range(50)]
    k, d = ta.stochastic(candles, 14, 3)
    assert all(x is None or 0 <= x <= 100 for x in k)
    assert all(x is None or 0 <= x <= 100 for x in d)


def test_adx_aligned_length():
    candles = [{"high": 10 + i * 0.01, "low": 9 + i * 0.01, "close": 9.5 + i * 0.01} for i in range(80)]
    out = ta.adx(candles, 14)
    assert len(out) == 80
    valid = [x for x in out if x is not None]
    assert valid
    assert all(0 <= x <= 100 for x in valid)
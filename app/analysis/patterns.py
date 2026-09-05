"""Candlestick pattern detector (pure functions, no lookahead).

Each detector inspects only the last two CLOSED bars of the window passed
in. pattern_bias() maps the same logic over a full series so the offline
backtest harness replays exactly what live analyze() sees.
 convention: +1 = bullish, -1 = bearish, 0 = none.
"""


def _body(c):
    return c["close"] - c["open"]


def _range(c):
    return c["high"] - c["low"]


def bullish_engulfing(prev, cur):
    if _body(prev) >= 0 or _body(cur) <= 0:
        return False
    return cur["open"] <= prev["close"] and cur["close"] >= prev["open"]


def bearish_engulfing(prev, cur):
    if _body(prev) <= 0 or _body(cur) >= 0:
        return False
    return cur["open"] >= prev["close"] and cur["close"] <= prev["open"]


def hammer(c):
    rng = _range(c)
    if rng <= 0:
        return False
    body = abs(_body(c))
    lower = min(c["open"], c["close"]) - c["low"]
    upper = c["high"] - max(c["open"], c["close"])
    return lower >= 2 * body and upper <= body and body <= rng * 0.35


def shooting_star(c):
    rng = _range(c)
    if rng <= 0:
        return False
    body = abs(_body(c))
    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]
    return upper >= 2 * body and lower <= body and body <= rng * 0.35


def doji(c):
    rng = _range(c)
    if rng <= 0:
        return True
    return abs(_body(c)) <= rng * 0.1


def detect(window):
    """Bias of the last closed bar given a window of >= 2 candles."""
    if len(window) < 2:
        return 0
    prev, cur = window[-2], window[-1]
    if bullish_engulfing(prev, cur) or hammer(cur):
        return 1
    if bearish_engulfing(prev, cur) or shooting_star(cur):
        return -1
    return 0


def pattern_bias(candles):
    """Per-bar bias over a full series. out[i] uses only bars i-1 and i,
    so replaying history introduces no lookahead."""
    out = [0] * len(candles)
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        if bullish_engulfing(prev, cur) or hammer(cur):
            out[i] = 1
        elif bearish_engulfing(prev, cur) or shooting_star(cur):
            out[i] = -1
    return out


def describe(bias, side):
    if bias == 0:
        return None
    agrees = (bias == 1 and side == "long") or (bias == -1 and side == "short")
    name = "bullish engulfing/hammer" if bias == 1 else "bearish engulfing/shooting star"
    if agrees:
        return f"Candlestick pattern supports the trade ({name})"
    return f"Caution: {name} pattern leans against this trade"

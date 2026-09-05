def sma(values, period):
    out = [None] * len(values)
    window = []
    for i, v in enumerate(values):
        if v is not None:
            window.append(v)
            if len(window) > period:
                window.pop(0)
            if len(window) >= period:
                out[i] = sum(window) / period
    return out


def ema(values, period):
    n = len(values)
    out = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for i in range(period, n):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(values, period=14):
    n = len(values)
    out = [None] * n
    if n <= period:
        return out
    gains = []
    losses = []
    for i in range(1, n):
        ch = values[i] - values[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = _rs_to_rsi(ag, al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = _rs_to_rsi(ag, al)
    return out


def _rs_to_rsi(ag, al):
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def macd(values, fast=12, slow=26, signal=9):
    n = len(values)
    ef = ema(values, fast)
    es = ema(values, slow)
    line = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    sig = [None] * n
    hist = [None] * n
    vals = [v for v in line if v is not None]
    if vals:
        smoothed = ema(vals, signal)
        j = 0
        for i in range(n):
            if line[i] is not None:
                if j < len(smoothed) and smoothed[j] is not None:
                    sig[i] = smoothed[j]
                    hist[i] = line[i] - sig[i]
                j += 1
    return line, sig, hist


def atr(candles, period=14):
    n = len(candles)
    out = [None] * n
    if n < 2:
        return out
    trs = []
    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return out
    avg = sum(trs[:period]) / period
    out[period] = avg
    for i in range(period, len(trs)):
        avg = (avg * (period - 1) + trs[i]) / period
        out[i + 1] = avg
    return out


def bollinger(values, period=20, mult=2.0):
    n = len(values)
    mid = sma(values, period)
    upper = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
    return mid, upper, lower


def stochastic(candles, k_period=14, d_period=3):
    n = len(candles)
    k = [None] * n
    for i in range(k_period - 1, n):
        highs = [c["high"] for c in candles[i - k_period + 1:i + 1]]
        lows = [c["low"] for c in candles[i - k_period + 1:i + 1]]
        hh = max(highs)
        ll = min(lows)
        if hh == ll:
            k[i] = 50.0
        else:
            k[i] = 100.0 * (candles[i]["close"] - ll) / (hh - ll)
    d = sma(k, d_period)
    return k, d


def adx(candles, period=14):
    n = len(candles)
    out = [None] * n
    if n < period + 1:
        return out
    trs = []
    pdm = []
    mdm = []
    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        ph = candles[i - 1]["high"]
        pl = candles[i - 1]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = h - ph
        dn = pl - l
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)

    def smooth(vals):
        s = sum(vals[:period]) / period
        outl = [None] * len(vals)
        outl[period - 1] = s
        for j in range(period, len(vals)):
            s = (s * (period - 1) + vals[j]) / period
            outl[j] = s
        return outl

    at_s = smooth(trs)
    pd_s = smooth(pdm)
    md_s = smooth(mdm)
    dx_map = {}
    for j in range(period - 1, len(trs)):
        atv = at_s[j]
        if atv == 0:
            continue
        p = pd_s[j] / atv * 100.0
        m = md_s[j] / atv * 100.0
        dx_map[j + 1] = abs(p - m) / (p + m) * 100.0 if p + m else 0.0
    indexes = sorted(dx_map)
    for i in range(period - 1, len(indexes)):
        window = sum(dx_map[indexes[k]] for k in range(i - period + 1, i + 1))
        out[indexes[i]] = window / period
    return out


def last(values):
    for v in reversed(values):
        if v is not None:
            return v
    return None


def all_last(values):
    out = []
    for fn in values:
        out.append(last(fn))
    return out
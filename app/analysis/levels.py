def swing_levels(candles, window=5, lookback=200):
    n = min(len(candles), lookback)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    support = []
    resistance = []
    if n < window * 2 + 1:
        return support, resistance
    for i in range(window, n - window):
        h_left = max(highs[i - window:i])
        h_right = max(highs[i + 1:i + 1 + window])
        if highs[i] >= h_left and highs[i] >= h_right:
            resistance.append(highs[i])
        l_left = min(lows[i - window:i])
        l_right = min(lows[i + 1:i + 1 + window])
        if lows[i] <= l_left and lows[i] <= l_right:
            support.append(lows[i])
    return support, resistance


def cluster(levels, tolerance_pct=0.15):
    if not levels:
        return []
    ordered = sorted(levels)
    clusters = []
    current = [ordered[0]]
    for x in ordered[1:]:
        base = sum(current) / len(current)
        if base == 0:
            clusters.append(base)
            current = [x]
            continue
        if abs(x - base) / abs(base) * 100 <= tolerance_pct:
            current.append(x)
        else:
            clusters.append(sum(current) / len(current))
            current = [x]
    clusters.append(sum(current) / len(current))
    return clusters


def nearest(price, supports, resistances, top=2):
    below = cluster(sorted(s for s in supports if s < price))
    above = cluster(sorted(r for r in resistances if r > price))
    support_levels = sorted(below, reverse=True)[:top]
    resistance_levels = sorted(above)[:top]
    return support_levels, resistance_levels


def latest_window(candles, limit):
    return candles[-limit:]
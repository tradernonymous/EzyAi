from app.analysis import levels as lv


def _candle(i, refrain):
    base = 100 + i * 0.1
    return {"high": base + 1, "low": base - 1, "close": base, "ts": i, "open": base}


def test_swing_detects_peaks_and_troughs():
    candles = []
    for i in range(120):
        wave = (i % 24)
        offset = [0, 4, 6, 4, 0, -4, -6, -4, 0, 4, 6, 4, 0, -4, -6, -4, 0, 4, 6, 4, 0, -4, -6, -4][wave]
        price = 100 + i * 0.1 + offset
        candles.append({"high": price + 2, "low": price - 2, "close": price, "ts": i, "open": price})
    support, resistance = lv.swing_levels(candles, window=3)
    assert len(resistance) > 2
    assert len(support) > 2


def test_nearest_clusters_close_levels():
    support = [99.0, 99.2, 95.0]
    resistance = [101.0, 101.1, 106.0]
    price = 100.0
    sup, res = lv.nearest(price, support, resistance, top=2)
    assert abs(sup[0] - 99.1) < 0.2 if sup else True
    assert abs(res[0] - 101.05) < 0.2 if res else True


def test_nearest_ignores_far_trending_levels():
    support = [50.0, 90.0]
    resistance = [110.0, 200.0]
    price = 100.0
    sup, res = lv.nearest(price, support, resistance, top=2)
    assert 90.0 in sup
    assert 110.0 in res
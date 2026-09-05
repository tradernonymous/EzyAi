from app import constants


def test_style_profiles_keyed():
    assert set(constants.STYLES) == {"scalping", "intraday", "swing"}
    for style in constants.STYLES:
        p = constants.STYLE_PROFILE[style]
        assert p["base_tf"] in constants.INTERVALS
        assert p["direction_tf"] in constants.INTERVALS
        assert p["check_interval_s"] > 0
        assert p["min_gap_s"] > p["check_interval_s"]


def test_mode_risk_ordering():
    safe = constants.MODE_PROFILE["safe"]
    normal = constants.MODE_PROFILE["normal"]
    aggressive = constants.MODE_PROFILE["aggressive"]
    assert safe["daily_limit"] < normal["daily_limit"] < aggressive["daily_limit"]
    assert safe["risk_frac"] < normal["risk_frac"] < aggressive["risk_frac"]
    assert safe["rr"] > normal["rr"] > aggressive["rr"]
    assert safe["sl_atr_mult"] > normal["sl_atr_mult"] > aggressive["sl_atr_mult"]
    assert safe["extra_confirmation"]
    assert not aggressive["extra_confirmation"]


def test_mode_safe_smaller_risk_curve():
    for mode in constants.MODES:
        p = constants.MODE_PROFILE[mode]
        assert 0 < p["risk_frac"] <= 0.05
        assert 1.0 <= p["rr"] <= 4.0
        assert p["aggression"] > 0


def test_universes_nonempty():
    assert constants.CRYPTO_UNIVERSE
    assert constants.FX_UNIVERSE
    assert constants.STOCK_UNIVERSE
    assert all(s.endswith("USDT") for s in constants.CRYPTO_UNIVERSE)
    assert all(len(s) == 6 for s in constants.FX_UNIVERSE)
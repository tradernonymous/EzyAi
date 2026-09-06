from .. import constants


def position_size(capital, entry, stop, risk_frac):
    """Units to buy/sell so that a stop-out loses capital * risk_frac.
    Returns 0.0 for any input that cannot produce a sane size."""
    try:
        capital, entry, stop, risk_frac = (float(capital), float(entry),
                                           float(stop), float(risk_frac))
    except (TypeError, ValueError):
        return 0.0
    if capital <= 0 or entry <= 0 or stop <= 0 or not 0 < risk_frac <= 1:
        return 0.0
    distance = abs(entry - stop)
    # Guard against a stop sitting a rounding error away from entry, which
    # would size the position into the millions.
    if distance <= entry * 1e-6:
        return 0.0
    return capital * risk_frac / distance


def max_position_value(capital, leverage=1.0):
    return max(0.0, float(capital)) * max(0.0, float(leverage))


def risk_summary(analysis):
    spec = analysis.get("spec")
    if not spec:
        return None
    mode = constants.MODE_PROFILE[analysis["mode"]]
    return {
        "risk_per_trade_pct": spec["risk_pct"],
        "reward_per_trade_pct": spec["risk_pct"] * spec["rr"],
        "sl_atr_mult": mode["sl_atr_mult"],
        "daily_signal_limit": mode["daily_limit"],
        "sizing_formula": "size = (capital x risk%) / (entry - stop)",
        "leverage_note": "keep total margin exposure below neutral levels when aggressive",
    }

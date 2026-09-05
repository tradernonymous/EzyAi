def position_size(capital, entry, stop, risk_frac):
    if entry == stop:
        return 0.0
    risk_dollars = capital * risk_frac
    return risk_dollars / abs(entry - stop)


def max_position_value(capital, leverage=1.0):
    return capital * leverage


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
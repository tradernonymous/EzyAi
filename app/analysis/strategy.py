from . import indicators as ta
from . import levels as lv
from .. import constants


def _spec(side, price, atr_value, support_levels, resistance_levels, mode_profile):
    if side == "long":
        s_ref = support_levels[0] if support_levels else None
        floor = s_ref - atr_value * mode_profile["sl_atr_mult"] if s_ref else \
            price - atr_value * mode_profile["sl_atr_mult"] * 1.2
        if floor >= price:
            floor = price - atr_value * mode_profile["sl_atr_mult"]
        rr = mode_profile["rr"]
        sl_dist = max(price - floor, atr_value * mode_profile["sl_atr_mult"] * 0.5)
        sl = price - sl_dist
        tp1 = price + sl_dist * rr
        tp2 = price + sl_dist * rr * 2.0
        entry_limit = s_ref if s_ref and (price - s_ref) < atr_value * 2 else price
        zone = (min(entry_limit, price), price)
    else:
        r_ref = resistance_levels[0] if resistance_levels else None
        ceil = r_ref + atr_value * mode_profile["sl_atr_mult"] if r_ref else \
            price + atr_value * mode_profile["sl_atr_mult"] * 1.2
        if ceil <= price:
            ceil = price + atr_value * mode_profile["sl_atr_mult"]
        rr = mode_profile["rr"]
        sl_dist = max(ceil - price, atr_value * mode_profile["sl_atr_mult"] * 0.5)
        sl = price + sl_dist
        tp1 = price - sl_dist * rr
        tp2 = price - sl_dist * rr * 2.0
        entry_limit = r_ref if r_ref and (r_ref - price) < atr_value * 2 else price
        zone = (price, max(entry_limit, price))
    return {
        "market": price,
        "limit": entry_limit,
        "zone_low": zone[0],
        "zone_high": zone[1],
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "risk_pct": mode_profile["risk_frac"] * 100.0,
    }


def _direction_from(ema9, ema21, ema50, macd_h):
    """Scalar direction rule shared by analyze() and the offline backtest
    harness (single source of truth, no lookahead by construction)."""
    bull_align = bool(ema9 and ema21 and ema50 and ema9 > ema21 > ema50)
    bear_align = bool(ema9 and ema21 and ema50 and ema9 < ema21 < ema50)
    if bull_align or (ema21 and ema50 and ema21 > ema50 and macd_h and macd_h > 0):
        return "up", bull_align, bear_align
    if bear_align or (ema21 and ema50 and ema21 < ema50 and macd_h and macd_h < 0):
        return "down", bull_align, bear_align
    return "neutral", bull_align, bear_align


def _confidence_from(side, feats, gates, reasons):
    """Scalar confidence scorer. feats holds precomputed indicator values
    for one bar; gates is a SIGNAL_GATES entry (live or candidate)."""
    score = 12.0
    ema21 = feats["ema21"]
    ema50 = feats["ema50"]
    adx_v = feats["adx"]
    macd_hist = feats["macd_hist"]
    rsi_v = feats["rsi"]
    bb_mid = feats["bb_mid"]
    stoch_k = feats["stoch_k"]
    close = feats["close"]
    atr_v = feats["atr"]
    rsi_lo, rsi_hi = gates["rsi_long"] if side == "long" else gates["rsi_short"]
    macd_ok = macd_hist is not None and (
        (macd_hist > 0) if side == "long" else (macd_hist < 0))
    if gates["macd_atr_min"] > 0 and macd_ok and atr_v:
        macd_ok = abs(macd_hist) >= gates["macd_atr_min"] * atr_v
    if side == "long":
        if ema21 is not None and ema50 is not None and ema21 > ema50:
            score += 22
            reasons.append("EMA21 above EMA50 (bull alignment)")
        if adx_v is not None and adx_v >= gates["adx_min"]:
            score += 15
            reasons.append(f"ADX {adx_v:.0f} confirms directional strength")
        if macd_ok:
            score += 15
            reasons.append("MACD histogram positive")
        if rsi_v is not None and rsi_lo <= rsi_v <= rsi_hi:
            score += 15
            reasons.append(f"RSI {rsi_v:.0f} in healthy bullish zone")
        if bb_mid is not None and close > bb_mid:
            score += 12
            reasons.append("Price above midline (bullish bias)")
        if stoch_k is not None and stoch_k > gates["stoch_cut"]:
            score += 9
            reasons.append(f"Stochastic above {gates['stoch_cut']:.0f} (momentum)")
    else:
        if ema21 is not None and ema50 is not None and ema21 < ema50:
            score += 22
            reasons.append("EMA21 below EMA50 (bear alignment)")
        if adx_v is not None and adx_v >= gates["adx_min"]:
            score += 15
            reasons.append(f"ADX {adx_v:.0f} confirms directional strength")
        if macd_ok:
            score += 15
            reasons.append("MACD histogram negative")
        if rsi_v is not None and rsi_lo <= rsi_v <= rsi_hi:
            score += 15
            reasons.append(f"RSI {rsi_v:.0f} in healthy bearish zone")
        if bb_mid is not None and close < bb_mid:
            score += 12
            reasons.append("Price below midline (bearish bias)")
        if stoch_k is not None and stoch_k < gates["stoch_cut"]:
            score += 9
            reasons.append(f"Stochastic below {gates['stoch_cut']:.0f} (momentum)")
    return max(0, min(100.0, score))


def _confidence(side, candles, closes, trend, macd_hist, rsi_v, bb_mid, stoch_k, adx_v, reasons,
                gates=None):
    ema21 = ta.last(ta.ema(closes, 21))
    ema50 = ta.last(ta.ema(closes, 50))
    if gates is None:
        gates = constants.SIGNAL_GATES["intraday"]
    feats = {"ema21": ema21, "ema50": ema50, "adx": adx_v,
             "macd_hist": macd_hist, "rsi": rsi_v, "bb_mid": bb_mid,
             "stoch_k": stoch_k, "close": closes[-1],
             "atr": ta.last(ta.atr(candles))}
    return _confidence_from(side, feats, gates, reasons)


def analyze(pair, style, mode, hub, interval=None):
    style_profile = constants.STYLE_PROFILE[style]
    mode_profile = constants.MODE_PROFILE[mode]
    base_tf = interval or style_profile["base_tf"]
    direction_tf = style_profile["direction_tf"]

    candles = hub.fetch_klines(pair, base_tf, style_profile["candles"])
    dir_candles = hub.fetch_klines(pair, direction_tf, style_profile["candles"])

    closes = [c["close"] for c in candles]
    price = closes[-1]

    ema9_l, ema21_l, ema50_l = ta.last(ta.ema(closes, 9)), ta.last(ta.ema(closes, 21)), ta.last(ta.ema(closes, 50))
    rsi_v = ta.last(ta.rsi(closes))
    atr_v = ta.last(ta.atr(candles))
    macd_line, macd_sig, macd_hist = ta.macd(closes)
    macd_l, macd_s_l, macd_h_l = ta.last(macd_line), ta.last(macd_sig), ta.last(macd_hist)
    bb_mid, bb_up, bb_lo = ta.bollinger(closes)
    bb_mid_l, bb_up_l, bb_lo_l = ta.last(bb_mid), ta.last(bb_up), ta.last(bb_lo)
    st_k, st_d = ta.stochastic(candles)
    st_k_l, st_d_l = ta.last(st_k), ta.last(st_d)
    adx_l = ta.last(ta.adx(candles))

    supports, resistances = lv.swing_levels(dir_candles)
    sup_lv, res_lv = lv.nearest(price, supports, resistances)

    bull_align = ema9_l and ema21_l and ema50_l and ema9_l > ema21_l > ema50_l
    bear_align = ema9_l and ema21_l and ema50_l and ema9_l < ema21_l < ema50_l
    direction, bull_align, bear_align = _direction_from(ema9_l, ema21_l, ema50_l, macd_h_l)

    if direction == "up":
        side = "long"
    elif direction == "down":
        side = "short"
    else:
        side = "neutral"

    reasons = []
    if direction == "up":
        reasons.append("Price structure is bullish on the active timeframe")
    elif direction == "down":
        reasons.append("Price structure is bearish on the active timeframe")
    else:
        reasons.append("Trend is neutral; signal quality is low")

    spec = None
    confidence = 0.0
    if side != "neutral":
        spec = _spec(side, price, atr_v, sup_lv, res_lv, mode_profile)
        gates = constants.SIGNAL_GATES[style]
        confidence = _confidence(side, candles, closes, direction, macd_h_l, rsi_v,
                                 bb_mid_l, st_k_l, adx_l, reasons, gates)
        confidence = min(100.0, confidence * (0.9 + mode_profile["aggression"] * 0.12))

    strength = "weak"
    if adx_l is not None:
        if adx_l >= 25:
            strength = "strong"
        elif adx_l >= 18:
            strength = "moderate"

    exit_notes = []
    if spec:
        exit_notes = [
            f"TP1 {_fmt(spec['tp1'])}: close 50% of the position to lock profit",
            f"TP2 {_fmt(spec['tp2'])}: let the remainder run for the larger move",
            f"SL {_fmt(spec['sl'])}: hard invalidation; move to breakeven after TP1",
        ]

    return {
        "pair": pair.upper(),
        "style": style,
        "mode": mode,
        "base_tf": base_tf,
        "direction_tf": direction_tf,
        "ts": candles[-1]["ts"],
        "price": price,
        "trend": {"direction": direction, "strength": strength,
                  "align": "bull" if bull_align else ("bear" if bear_align else "mixed"),
                  "adx": adx_l},
        "ind": {
            "ema9": ema9_l, "ema21": ema21_l, "ema50": ema50_l,
            "rsi": rsi_v, "atr": atr_v,
            "macd": macd_l, "macd_signal": macd_s_l, "macd_hist": macd_h_l,
            "bb": {"upper": bb_up_l, "mid": bb_mid_l, "lower": bb_lo_l},
            "stoch": {"k": st_k_l, "d": st_d_l},
        },
        "levels": {"support": sup_lv, "resistance": res_lv},
        "side": side,
        "spec": spec,
        "confidence": float(round(confidence, 1)),
        "reasons": reasons,
        "exit_notes": exit_notes,
        "hold_horizon": style_profile["hold"],
        "data_mode": hub.mode,
    }


def _fmt(v):
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:.6f}"
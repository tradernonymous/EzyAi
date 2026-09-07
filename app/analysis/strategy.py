from . import indicators as ta
from . import levels as lv
from . import patterns as pat
from . import regime as rg
from . import sentiment as sent
from .. import constants
from ..data import freshness as fr
from ..data import quality
from ..data.provider import DataHub

PATTERN_POINTS = 4.0
SENTIMENT_POINTS = 3.0
SENTIMENT_CUT = 0.15


def _spec(side, price, atr_value, support_levels, resistance_levels,
          mode_profile, spread_bps=0):
    # 3E: the stop is widened by the (static) bid/ask spread so that what the
    # bot quotes is what the broker can actually fill. The take-profit sizes
    # are kept, so the R:R falls; a setup that can no longer meet the style's
    # target is dropped instead of emitted with a fictionally-wide R:R.
    spread_price = price * spread_bps / 10000.0
    if side == "long":
        s_ref = support_levels[0] if support_levels else None
        floor = s_ref - atr_value * mode_profile["sl_atr_mult"] if s_ref else \
            price - atr_value * mode_profile["sl_atr_mult"] * 1.2
        if floor >= price:
            floor = price - atr_value * mode_profile["sl_atr_mult"]
        rr = mode_profile["rr"]
        sl_dist = max(price - floor, atr_value * mode_profile["sl_atr_mult"] * 0.5)
        sl = price - sl_dist - spread_price
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
        sl = price + sl_dist + spread_price
        tp1 = price - sl_dist * rr
        tp2 = price - sl_dist * rr * 2.0
        entry_limit = r_ref if r_ref and (r_ref - price) < atr_value * 2 else price
        zone = (price, max(entry_limit, price))
    eff_risk = sl_dist + spread_price
    if eff_risk <= 0:
        return None
    rr_eff = sl_dist * rr / eff_risk
    if rr_eff < rr * 0.8:
        return None  # spread ate too much of the target R:R: no setup
    return {
        "market": price,
        "limit": entry_limit,
        "zone_low": zone[0],
        "zone_high": zone[1],
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr_eff,
        "risk_pct": mode_profile["risk_frac"] * 100.0,
        "spread_estimate": spread_bps,
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


def _structure_direction(candles):
    """EMA-stack + MACD of a series -> ('up'|'down'|'neutral', adx)."""
    if not candles or len(candles) < 60:
        return "neutral", None
    closes = [c["close"] for c in candles]
    ema9 = ta.last(ta.ema(closes, 9))
    ema21 = ta.last(ta.ema(closes, 21))
    ema50 = ta.last(ta.ema(closes, 50))
    _, _, macd_h = ta.macd(closes)
    direction, _, _ = _direction_from(ema9, ema21, ema50, ta.last(macd_h))
    return direction, ta.last(ta.adx(candles))


def _cross_score(side, confirm, pair, confirm_tf, hub):
    """Cross-asset tilt (5 points max): crypto majors lean on the BTC regime
    on the same confirm timeframe; every other asset has no reliable live
    proxy here and keeps the neutral midpoint (2.5). Never raises: any
    failure degrades to neutral."""
    try:
        kind = hub.classify(pair)
    except Exception:
        kind = None
    if kind != constants.KIND_CRYPTO:
        return 2.5
    try:
        if pair == "BTCUSD":
            btc_dir, _ = confirm
        else:
            candles, _m = hub.fetch_klines_ex("BTCUSD", confirm_tf, 150)
            if not candles:
                return 2.5
            btc_dir, _ = _structure_direction(candles)
    except Exception:
        return 2.5
    if btc_dir == "neutral":
        return 2.5
    agree = (btc_dir == "up" and side == "long") or \
            (btc_dir == "down" and side == "short")
    return 5.0 if agree else 0.0


def _score(side, ctx, gates, reasons):
    """Phase-2 weighted confidence. ctx holds precomputed inputs and the
    weights sum to exactly 100 (base 10, HTF 25, momentum 25, vol 12,
    levels 15, session 8, cross-asset 5 -- see constants). Returns 0-100."""
    b = ctx["base"]
    close, atr_v = b["close"], b["atr"]
    score = 10.0  # base: a live, tradable setup starts here
    notes = []

    # consolidated momentum (25): trend, pace, strength, zone, position
    if side == "long":
        checks = (
            ("EMA21 > EMA50", b["ema21"] and b["ema50"] and b["ema21"] > b["ema50"]),
            ("MACD hist positive", b["macd_hist"] is not None and b["macd_hist"] > 0),
            ("ADX trend strength", b["adx"] is not None and b["adx"] >= gates["adx_min"]),
            ("RSI healthy bullish", b["rsi"] is not None
             and gates["rsi_long"][0] <= b["rsi"] <= gates["rsi_long"][1]),
            ("Above BB midline", b["bb_mid"] is not None and close > b["bb_mid"]),
        )
    else:
        checks = (
            ("EMA21 < EMA50", b["ema21"] and b["ema50"] and b["ema21"] < b["ema50"]),
            ("MACD hist negative", b["macd_hist"] is not None and b["macd_hist"] < 0),
            ("ADX trend strength", b["adx"] is not None and b["adx"] >= gates["adx_min"]),
            ("RSI healthy bearish", b["rsi"] is not None
             and gates["rsi_short"][0] <= b["rsi"] <= gates["rsi_short"][1]),
            ("Below BB midline", b["bb_mid"] is not None and close < b["bb_mid"]),
        )
    for tag, cond in checks:
        if cond:
            score += 5.0
            notes.append(tag)

    # HTF structure (25): the confirm timeframe must agree. Conflict is hard
    # gated in analyze(); if it still reaches here it scores zero.
    cdx, cadx = ctx["confirm"]
    if (cdx == "up" and side == "long") or (cdx == "down" and side == "short"):
        score += 25.0 if (cadx is not None and cadx >= 18) else 15.0
        notes.append(f"{ctx['confirm_tf']} confirms {side}")
    elif cdx in ("up", "down"):
        reasons.append(f"{ctx['confirm_tf']} opposes {side} -- gated")
    else:
        score += 10.0
        notes.append(f"{ctx['confirm_tf']} flat")

    # volatility regime (12)
    ratio = ctx.get("vol_ratio")
    if ratio is None:
        score += 6.0
    elif rg.VOL_DEAD_RATIO < ratio < rg.VOL_CHAOS_RATIO:
        score += 12.0
    elif ratio >= rg.VOL_CHAOS_RATIO:
        score += 4.0
        reasons.append(f"Volatility x{ratio:.1f} -- chaotic")
    else:
        score += 6.0
        reasons.append(f"Volatility x{ratio:.1f} -- dead")

    # level proximity (15): is price near the level in our favour?
    # analyze passes lv.nearest() output (lists of up to 2, closest first).
    sup = ctx["levels"][0][0] if ctx["levels"][0] else None
    res = ctx["levels"][1][0] if ctx["levels"][1] else None
    if side == "long" and sup:
        d = (close - sup) / atr_v if atr_v else 9.0
        score += 15.0 if d <= 1.0 else 10.0 if d <= 2.5 else 5.0
    elif side == "short" and res:
        d = (res - close) / atr_v if atr_v else 9.0
        score += 15.0 if d <= 1.0 else 10.0 if d <= 2.5 else 5.0
    elif side == "long" and res:  # flushed against resistance: headwind
        d = (res - close) / atr_v if atr_v else 9.0
        score += 0.0 if d <= 1.0 else 10.0
    elif side == "short" and sup:
        d = (close - sup) / atr_v if atr_v else 9.0
        score += 0.0 if d <= 1.0 else 10.0
    else:
        score += 5.0

    # session (8); daily bars print at 00:00 UTC so sessions are meaningless
    if ctx.get("base_tf") == "1d":
        score += 8.0
    elif ctx.get("session") == "open":
        score += 8.0
    elif ctx.get("session") == "thin":
        score += 4.0
        reasons.append("Thin trading session")
    else:
        reasons.append("Weekend / closed venue")

    # cross-asset (5)
    score += ctx.get("cross", 2.5)

    reasons.extend(notes)
    return max(0.0, min(100.0, score))


def analyze(pair, style, mode, hub, interval=None, sentiment=None):
    """sentiment: optional precomputed headline compound in [-1, 1] (or None).

    Only the on-demand /analyze path supplies it; background watch/autopilot
    ticks pass nothing so they stay pure-technical and fast.
    """
    style_profile = constants.STYLE_PROFILE[style]
    mode_profile = constants.MODE_PROFILE[mode]
    base_tf = interval or style_profile["base_tf"]
    direction_tf = style_profile["direction_tf"]
    confirm_tf = style_profile["confirm_tf"]

    fetch_ex = getattr(hub, "fetch_klines_ex", None)
    if fetch_ex is not None:
        candles, mode_a = fetch_ex(pair, base_tf, style_profile["candles"])
        dir_candles, mode_b = fetch_ex(pair, direction_tf, style_profile["candles"])
        conf_candles, mode_c = fetch_ex(pair, confirm_tf, style_profile["candles"])
        data_mode = "demo" if "demo" in (mode_a, mode_b, mode_c) else "live"
        source = candles[-1].get("source") if candles else None
    else:  # test stubs and older hubs
        candles = hub.fetch_klines(pair, base_tf, style_profile["candles"])
        dir_candles = hub.fetch_klines(pair, direction_tf, style_profile["candles"])
        conf_candles = hub.fetch_klines(pair, confirm_tf, style_profile["candles"])
        data_mode = getattr(hub, "mode", "live")
        source = (candles[-1].get("source") if candles
                  else getattr(hub, "mode", None))
    if not candles or not dir_candles or not conf_candles:
        raise ValueError(f"no candles for {pair}")
    # 3B: never analyse a stale/gapped/implausible series. On rejection the
    # scheduler counts this as a feed failure and backs off -- degraded data
    # is skipped, never silently consumed.
    if isinstance(hub, DataHub):
        ok, why = fr.check(candles, base_tf)
        if not ok:
            raise ValueError(f"quality gate: {pair} {why}")

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

    # Phase-2 HTF confirm gate: an explicit conflicting read on the style's
    # confirm timeframe kills the setup outright (no scoring, no spec). A
    # flat confirm timeframe merely scores a partial 10 in _score.
    confirm_dir, confirm_adx = _structure_direction(conf_candles)
    confirm_conflict = ((side == "long" and confirm_dir == "down")
                        or (side == "short" and confirm_dir == "up"))
    if confirm_conflict:
        side = "neutral"
        reasons.append(f"{confirm_tf} opposes the base trend -- no signal")

    spec = None
    confidence = 0.0
    confluence = {"pattern": 0, "sentiment": sentiment, "vol_ratio": None,
                  "session": "open"}
    if side != "neutral":
        spec = _spec(side, price, atr_v, sup_lv, res_lv, mode_profile,
                     spread_bps=constants.spread_bps(pair))
        gates = constants.SIGNAL_GATES[style]
        # Phase-3 confluence: factual notes always shown (zero signal
        # impact); confidence points only when CONFLUENCE_SCORING is on,
        # which requires backtest proof (currently off -- see constants).
        # Feeds return the still-forming bar last; patterns need closed bars.
        bias = pat.detect(candles[:-1] if len(candles) > 3 else candles)
        confluence["pattern"] = bias
        if bias != 0:
            name = ("bullish engulfing/hammer" if bias == 1
                    else "bearish engulfing/shooting star")
            reasons.append(f"Last-bar candle pattern: {name}")
        if sentiment is not None and -1.0 <= sentiment <= 1.0:
            reasons.append(f"\U0001f4f0 Headline sentiment ({sentiment:+.2f})")
        ratio = rg.vol_ratio(ta.realized_vol(closes), len(closes) - 1)
        confluence["vol_ratio"] = ratio
        try:
            kind = hub.classify(pair)
        except Exception:
            kind = None
        state = rg.session_state(kind, candles[-1]["ts"]) if kind else "open"
        confluence["session"] = state
        if state != "open" and base_tf != "1d":
            # daily bars print at 00:00 UTC; session labels are meaningless there
            label = {"closed": "Weekend market (thin/stale quotes)",
                     "thin": "Thin trading session"}.get(state, state)
            reasons.append(f"{label} -- interpret with care")
        # Phase-2 weighted confidence (sums to 100; see constants).
        cross = _cross_score(side, (confirm_dir, confirm_adx), pair,
                             confirm_tf, hub)
        ctx = {
            "base": {"ema21": ema21_l, "ema50": ema50_l, "adx": adx_l,
                     "macd_hist": macd_h_l, "rsi": rsi_v, "bb_mid": bb_mid_l,
                     "stoch_k": st_k_l, "close": price, "atr": atr_v},
            "confirm": (confirm_dir, confirm_adx),
            "confirm_tf": confirm_tf,
            "levels": (sup_lv, res_lv),
            "vol_ratio": ratio,
            "session": state,
            "base_tf": base_tf,
            "cross": cross,
        }
        confidence = _score(side, ctx, gates, reasons)
        confidence = max(0.0, min(100.0, confidence))

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
        "confirm": {"tf": confirm_tf, "direction": confirm_dir,
                    "adx": confirm_adx,
                    "agree": side != "neutral"},
        "ind": {
            "ema9": ema9_l, "ema21": ema21_l, "ema50": ema50_l,
            "rsi": rsi_v, "atr": atr_v,
            "macd": macd_l, "macd_signal": macd_s_l, "macd_hist": macd_h_l,
            "bb": {"upper": bb_up_l, "mid": bb_mid_l, "lower": bb_lo_l},
            "stoch": {"k": st_k_l, "d": st_d_l},
        },
        "data_source": source,
        "quality_note": _quality_note(pair, style, hub),
        "levels": {"support": sup_lv, "resistance": res_lv},
        "side": side,
        "spec": spec,
        "confidence": float(round(confidence, 1)),
        "confluence": confluence,
        "cross": ctx.get("cross", 2.5) if side != "neutral" else 2.5,
        "reasons": reasons,
        "exit_notes": exit_notes,
        "hold_horizon": style_profile["hold"],
        "data_mode": data_mode,
    }


def _quality_note(pair, style, hub):
    """Quality warning for informational /analyze output. None on test stubs
    and internal hubs so the message stays clean off production feeds."""
    if not isinstance(hub, DataHub):
        return None
    return quality.quality_warning(pair, style)


def _fmt(v):
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:.6f}"
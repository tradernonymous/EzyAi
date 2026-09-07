import time

from .. import constants
from ..analysis import strategy as strat


def evaluate(analysis):
    mode_profile = constants.MODE_PROFILE[analysis["mode"]]
    if analysis["side"] == "neutral":
        return None
    spec = analysis["spec"]
    if not spec:
        return None
    # A flat or halted instrument (ATR 0) yields SL == entry == TP: not a
    # tradeable idea, never alert on it.
    if abs(spec["market"] - spec["sl"]) <= 0 or abs(spec["tp1"] - spec["market"]) <= 0:
        return None

    # Phase-2 per-mode thresholds (replaced conf_gate - aggression*6; the
    # aggression lever now lives entirely in SIGNAL_THRESHOLDS).
    thresholds = constants.SIGNAL_THRESHOLDS.get(
        analysis.get("style", "intraday"), constants.SIGNAL_THRESHOLDS["intraday"])
    gate = thresholds[analysis["mode"]]
    if analysis["confidence"] < gate:
        return None

    if mode_profile["extra_confirmation"]:
        ind = analysis["ind"]
        hist = ind["macd_hist"]
        if hist is None:
            return None
        if analysis["side"] == "long" and hist < 0:
            return None
        if analysis["side"] == "short" and hist > 0:
            return None

    ind = analysis["ind"]
    bb = ind.get("bb") or {}
    stoch = ind.get("stoch") or {}
    trend = analysis.get("trend") or {}
    return {
        "pair": analysis["pair"],
        "side": analysis["side"],
        "style": analysis["style"],
        "mode": analysis["mode"],
        "tf": analysis["base_tf"],
        "entry": spec["market"],
        "entry_limit": spec["limit"],
        "entry_zone": (spec["zone_low"], spec["zone_high"]),
        "sl": spec["sl"],
        "tp1": spec["tp1"],
        "tp2": spec["tp2"],
        "rr": spec["rr"],
        "risk_pct": spec["risk_pct"],
        "confidence": analysis["confidence"],
        "reasons": list(analysis["reasons"]),
        "exit_notes": list(analysis["exit_notes"]),
        "hold_horizon": analysis["hold_horizon"],
        "support": analysis["levels"]["support"],
        "resistance": analysis["levels"]["resistance"],
        "ts": time.time(),
        "data_mode": analysis["data_mode"],
        "data_source": analysis.get("data_source"),
        "spread_estimate": spec.get("spread_estimate"),
        "spread_unit": spec.get("spread_unit"),
        # Confidence inputs at emission time, stored verbatim for Phase-4
        # calibration (confidence = 12 base + 22 trend + 15 adx + 15 macd +
        # 15 rsi + 12 bb-mid + 9 stoch, times mode aggression).
        "component_scores": {
            "ema21": ind.get("ema21"), "ema50": ind.get("ema50"),
            "adx": trend.get("adx"), "macd_hist": ind.get("macd_hist"),
            "rsi": ind.get("rsi"), "bb_mid": bb.get("mid"),
            "stoch_k": stoch.get("k"), "atr": ind.get("atr"),
            "close": spec["market"], "gate": gate,
            "confirm_tf": analysis.get("confirm", {}).get("tf"),
            "confirm_dir": analysis.get("confirm", {}).get("direction"),
            "cross": analysis.get("cross"),
        },
    }


def quick_analyze(pair, style, mode, hub, interval=None, sentiment=None):
    analysis = strat.analyze(pair, style, mode, hub, interval=interval,
                             sentiment=sentiment)
    signal = evaluate(analysis)
    return analysis, signal
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

    gate = constants.CONFIDENCE_GATE - mode_profile["aggression"] * 6
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
    }


def quick_analyze(pair, style, mode, hub, interval=None):
    analysis = strat.analyze(pair, style, mode, hub, interval=interval)
    signal = evaluate(analysis)
    return analysis, signal
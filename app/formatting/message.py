from html import escape

from .. import constants
from ..analysis import regime as _regime
from ..outcomes import calibration as _calibration

BADGE = {
    "long": "\U0001f7e2",
    "short": "\U0001f534",
    "neutral": "\u26ab",
}

SIDE_LABEL = {"long": "LONG", "short": "SHORT", "neutral": "NEUTRAL"}


def price(p):
    if p is None:
        return "-"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6f}"


def _arrow(direction):
    return {"up": "\u2191", "down": "\u2193"}.get(direction, "\u2194")


def pct(v, digits=1):
    """Signed percentage or n/a when the window was not available."""
    if v is None:
        return "n/a"
    return f"{v:+.{digits}f}%"


def analysis_report(a):
    e = escape
    trend = a["trend"]
    spec = a["spec"]
    ind = a["ind"]
    style_label = constants.STYLE_PROFILE[a["style"]]["label"]
    mode_label = constants.MODE_PROFILE[a["mode"]]["label"]

    lines = [f"\U0001f4c8 <b>{e(a['pair'])}</b> \u00b7 {BADGE[a['side']]} "
             f"<b>{SIDE_LABEL[a['side']]}</b> idea",
             f"{style_label} \u00b7 {mode_label} risk \u00b7 {a['base_tf']} chart, "
             f"trend from {a['direction_tf']}",
             f"Price <b>{price(a['price'])}</b> \u00b7 Data {a['data_mode']}",
             ""]

    adx_text = f"ADX {trend['adx']:.0f}" if trend["adx"] is not None else "ADX \u2014"
    lines.append("<b>Trend</b>")
    lines.append(f"{_arrow(trend['direction'])} {e(trend['direction'].upper())} "
                 f"({trend['strength']}) \u00b7 EMA align {trend['align']} \u00b7 {adx_text}")
    dash = "\u2014"
    rsi = ind["rsi"] if ind["rsi"] is not None else dash
    macd_h = ind["macd_hist"] if ind["macd_hist"] is not None else dash
    lines.append(f"RSI {rsi} \u00b7 MACD hist {macd_h} \u00b7 ATR {price(ind['atr'])}")
    bb = ind["bb"]
    bb_bar = position_bar(bb["lower"], bb["upper"], a["price"])
    if bb_bar:
        lines.append(f"Bollinger {bb_bar} {price(bb['lower'])} to {price(bb['upper'])}")
    if a["levels"]["support"] or a["levels"]["resistance"]:
        sup = " \u00b7 ".join(price(x) for x in a["levels"]["support"]) or "\u2014"
        res = " \u00b7 ".join(price(x) for x in a["levels"]["resistance"]) or "\u2014"
        lines.append(f"Support {sup}")
        lines.append(f"Resistance {res}")
    sp = a.get("spread")
    if sp and sp.get("atr_ratio") is not None:
        est = " (estimate)" if sp.get("estimated") else ""
        lines.append(f"Spread{est} {price(sp['latest'])} \u00b7 {sp['atr_ratio']:.2f} ATR")
    lines.append("")

    if spec:
        lines.append(f"<b>Signal</b> \u00b7 {BADGE[a['side']]} {SIDE_LABEL[a['side']]}")
        lines.extend(_levels_block(spec["zone_low"], spec["zone_high"],
                                   spec["sl"], spec["tp1"], spec["tp2"]))
        lines.append(f"Market {price(spec['market'])} \u00b7 Limit {price(spec['limit'])}")
        lines.append(f"Risk {spec['risk_pct']:.1f}% of capital \u00b7 Reward {spec['rr']:.1f}R")
        lines.append(f"Setup score <b>{a['confidence']:.0f}</b>/100 {meter(a['confidence'])} "
                     "\u00b7 aligned indicators, not a win probability")
    else:
        lines.append(f"<b>Signal</b> \u00b7 {BADGE[a['side']]} no trade setup")
        lines.append(f"Trend {trend['direction']} \u00b7 setup score "
                     f"<b>{a['confidence']:.0f}</b>/100 {meter(a['confidence'])}")

    if a["reasons"]:
        lines.append("")
        lines.append("<b>Why</b>")
        lines.extend(f"\u2022 {e(r)}" for r in a["reasons"])

    if a["exit_notes"]:
        lines.append("")
        lines.append("<b>Exit rules</b>")
        lines.extend(f"\u2022 {note}" for note in a["exit_notes"])
        lines.append(f"\u2022 Position size = (capital \u00d7 {spec['risk_pct']:.1f}%) "
                     "\u00f7 (entry \u2212 stop)")
        lines.append(f"\u2022 Horizon {a['hold_horizon']}")

    lines.append("")
    # 3C: /analyze is informational so it runs on delayed feeds too -- but
    # the quality note is printed from the same copy the buttons use, so it
    # can never drift from what the commands enforce.
    if a.get("quality_note"):
        lines.append(a["quality_note"])
    lines.append("<i>Educational confluence only, not financial advice. Demo data "
                 "can stand in when live feeds fail. Verify prices with your "
                 "broker before acting.</i>")
    return "\n".join(lines)


def _feed_lines(sig):
    """Provenance lines for a live message. Synthetic/demo data is flagged
    loudly so a signal can never look like real data; a delayed mid feed
    says so and names the spread it assumed."""
    e = escape
    src = sig.get("data_source")
    if src == "synthetic" or sig.get("data_mode") == "demo":
        return ["\U0001f6a8 <b>DEMO DATA</b> \u2014 prices may be simulated. "
                "Verify before acting."]
    if src == "yahoo":
        sp = sig.get("spread_estimate")
        if sp:
            return [f"Feed: Yahoo delayed \u00b7 spread assumed {sp:.0f} bps "
                    "\u00b7 verify with your broker"]
        return ["Feed: Yahoo delayed \u00b7 verify with your broker"]
    if src == "binance":
        return ["Feed: Binance, live"]
    if src == "ccxt":
        return ["Feed: exchange, live"]
    if src:
        return [f"Feed: {e(str(src))}"]
    return []


def _levels_block(entry_low, entry_high, sl, tp1, tp2):
    """Aligned entry / stop / target rows. Telegram renders <code> in a
    monospace face, so the figures line up as a column."""
    zone = f"{price(entry_low)} \u2013 {price(entry_high)}"
    return [f"<code>Entry     {zone}</code>",
            f"<code>Stop      {price(sl)}</code>",
            f"<code>Target 1  {price(tp1)}</code>",
            f"<code>Target 2  {price(tp2)}</code>"]


def _sr_lines(support, resistance):
    sup = " \u00b7 ".join(price(x) for x in (support or [])) or "\u2014"
    res = " \u00b7 ".join(price(x) for x in (resistance or [])) or "\u2014"
    return ["<b>Levels</b>", f"Support {sup}", f"Resistance {res}"]


def signal_message(sig, source="watch"):
    e = escape
    header = "\U0001f514 <b>LIVE TRADE SIGNAL</b>" if source == "watch" else \
        "\U0001f680 <b>AUTO SIGNAL</b>"
    style = constants.STYLE_PROFILE[sig["style"]]["label"]
    mode = constants.MODE_PROFILE[sig["mode"]]["label"]
    lines = [header,
             f"<b>{e(sig['pair'])}</b> \u00b7 {BADGE[sig['side']]} <b>{SIDE_LABEL[sig['side']]}</b>",
             "",
             f"{style} \u00b7 {mode} risk \u00b7 {sig['tf']} chart"]
    lines.extend(_feed_lines(sig))
    lines.append("")
    lines.extend(_levels_block(sig["entry_zone"][0], sig["entry_zone"][1],
                               sig["sl"], sig["tp1"], sig["tp2"]))
    lines.append("")
    lines.append(f"Risk {sig['risk_pct']:.1f}% of capital \u00b7 Reward {sig['rr']:.1f}R")
    lines.append(f"Setup score <b>{sig['confidence']:.0f}</b>/100 {meter(sig['confidence'])}")
    lines.append("")
    lines.extend(_sr_lines(sig.get("support"), sig.get("resistance")))
    if sig.get("reasons"):
        lines.append("")
        lines.append("<b>Why</b>")
        lines.extend(f"\u2022 {e(r)}" for r in sig["reasons"][:3])
    lines.append("")
    lines.append("<i>Not financial advice. Verify prices with your broker.</i>")
    return "\n".join(lines)


def quality_gate_text(pair, style, reason, now=None):
    """Human copy for a quality-gate rejection. `reason` is the raw exception
    string from strategy.analyze(); `now` is epoch seconds (default wall
    clock) so tests are deterministic."""
    e = escape
    head = f"{style} signal for <b>{e(pair)}</b>"
    if reason.startswith("quality gate: session:"):
        return (
            f"\U0001f4c8 {head} \u2014 not the right time.\n\n"
            f"Scalping {e(pair)} only runs during "
            f"{_scalp_label(pair)}. Outside that window the spread is too "
            f"wide to scalp profitably, so no alert is emitted.\n"
            f"\U0001f513 Next window opens {_next_open_text(pair, now)}.")
    if reason.startswith("quality gate: closed:"):
        # Inside the pair's own window, so the calendar says it should be
        # trading: what stopped is the feed, not necessarily the market.
        # Saying "closed until Tuesday" here would be a claim the bot has
        # not checked -- the window is open now and alerts resume the moment
        # quotes catch up.
        win = _regime.scalp_session(pair, now=now)
        if win is not None and win["in_window"]:
            return (
                f"\U0001f4c8 {head} \u2014 quotes have gone quiet.\n\n"
                f"{e(pair)} is inside {_scalp_label(pair)}, but its last "
                f"price is too old to scalp on \u2014 a thin holiday "
                f"session, a halt, or the feed lagging.\n"
                f"\U0001f504 Nothing to do: alerts resume by themselves as "
                f"soon as prices update.")
        reopen = _regime.next_session_open(pair, now=now)
        when = _regime.fmt_next_open(reopen) if reopen else "at the start "
        return (
            f"\U0001f4c8 {head} \u2014 markets look closed.\n\n"
            f"{e(pair)} quotes have stopped updating (weekend, holiday or a "
            f"halt). No alerts until the venue reopens.\n"
            f"\U0001f4c5 Next session {when or 'later this week'}.")
    if reason.startswith("quality gate: viability:"):
        return (
            f"\U0001f4c8 {head} \u2014 the live spread is too wide.\n\n"
            f"{e(_strip_prefix(reason))} The stop widening already leaves "
            f"no room, so this setup is dropped.\n"
            f"\U0001f6aa No alert until the spread/ATR ratio recovers.")
    detail = _strip_prefix(reason)
    return (
        f"\U0001f4c8 {head} \u2014 {e(style)} feed is quiet.\n\n"
        f"{e(detail)} Alerts resume automatically when the feed catches up.")


def _strip_prefix(reason):
    b = reason.split("quality gate:", 1)
    return b[1].strip() if len(b) == 2 else reason


def _scalp_label(pair):
    win = _regime.scalp_session(pair)
    return win["label"] if win else "trading hours"


def _next_open_text(pair, now):
    nxt = _regime.next_session_open(pair, now=now)
    if not nxt:
        return "later this week"
    import time as _t
    secs = int(nxt - (now or _t.time()))
    return f"in {_countdown(secs)} ({_regime.fmt_next_open(nxt)})"


def _countdown(secs):
    secs = max(0, secs)
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{max(1, m)}m"


def quote_report(pair, tick):
    e = escape
    bar = position_bar(tick.get("low"), tick.get("high"), tick.get("price"))
    lines = [f"\U0001f4b2 <b>{e(pair)}</b>",
             f"<b>{price(tick['price'])}</b> \u00b7 {tick['change_pct']:+.2f}% over 24h",
             ""]
    if bar:
        lines.append(f"Day range: {price(tick['low'])} {bar} {price(tick['high'])}")
    else:
        lines.append(f"High {price(tick['high'])} \u00b7 Low {price(tick['low'])}")
    lines.append(f"Volume {tick['volume']:,.0f} {e(str(tick['quote']))} \u00b7 "
                 f"Data {tick.get('mode', 'live')}")
    lines.append("")
    lines.append("<i>Indicative price, not financial advice.</i>")
    return "\n".join(lines)


DIV = "\u2500" * 20


def meter(value, maximum=100.0, width=8):
    if value is None or maximum <= 0:
        return "\u25b1" * width
    filled = max(0, min(width, round(value / maximum * width)))
    return "\u25b0" * filled + "\u25b1" * (width - filled)


_BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline(values, width=16):
    """Tiny text sparkline from a numeric series. '' when unusable."""
    try:
        vals = [float(v) for v in list(values)[-width:] if v is not None]
    except Exception:
        return ""
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return "\u2500" * len(vals)
    n = len(_BLOCKS) - 1
    return "".join(_BLOCKS[min(n, int((v - lo) / (hi - lo) * n))] for v in vals)


def position_bar(lo, hi, px, width=12):
    """Where px sits inside [lo, hi]: \u2500\u2500\u25cf\u2500\u2500 style bar. '' when unusable."""
    try:
        lo, hi, px = float(lo), float(hi), float(px)
    except Exception:
        return ""
    if not (hi > lo):
        return ""
    pos = max(0.0, min(1.0, (px - lo) / (hi - lo)))
    idx = min(width - 1, int(round(pos * (width - 1))))
    return "\u2500" * idx + "\u25cf" + "\u2500" * (width - 1 - idx)


def pillars_line(pillars, labels):
    bits = []
    for key, label in labels:
        v = pillars.get(key)
        if v is not None:
            bits.append(f"{label} {v:.0f}")
    return " \u00b7 ".join(bits)


def _trend_word(chg):
    if chg is None:
        return "flat"
    if chg >= 10:
        return f"strongly higher ({chg:+.0f}%)"
    if chg > 0.5:
        return f"higher ({chg:+.1f}%)"
    if chg <= -10:
        return f"sharply lower ({chg:+.0f}%)"
    if chg < -0.5:
        return f"lower ({chg:+.1f}%)"
    return f"flat ({chg:+.1f}%)"


def _watchlist_lines(kind, pair):
    from ..fundamentals import scoring as _sc
    events = _sc.upcoming_events(kind, pair)
    if not events:
        return []
    lines = ["", "\U0001f5d3 <b>Next watchlist</b>"]
    for date, label in events:
        lines.append(f"\u2022 {date} \u2014 {label}")
    return lines


def outlook_stock(symbol, data):
    from ..fundamentals import scoring as _sc
    lines = [""]
    if data.get("fscore") is not None:
        lines.append(f"\U0001f4cb Executive summary: {data.get('fgrade', '?')} fundamentals "
                     f"({data['fscore']:.0f}/100) \u00b7 price {_trend_word(data.get('chg_3m'))} over 3m.")
    lines.append(f"\U0001f52e Outlook \u2014 short term {_trend_word(data.get('chg_1w'))}; "
                 f"medium term {_trend_word(data.get('chg_1y'))}.")
    risks = []
    if (data.get("vol_pct") or 0) > 35:
        risks.append(f"high volatility ({data['vol_pct']:.0f}% annualized) \u2014 size down")
    dv = data.get("dcf_verdict") or {}
    if dv.get("label") == "overvalued" and dv.get("mos_pct", 0) < -50:
        risks.append(f"priced well above DCF value ({dv['mos_pct']:.0f}% margin)")
    pf = data.get("piotroski") or {}
    if pf.get("total", 0) >= 5 and pf.get("score", 9) < 4:
        risks.append(f"weak financial trend (Piotroski {pf['score']}/{pf['total']})")
    if (data.get("fpe") or 0) > 35:
        risks.append(f"demanding multiple (FY P/E {data['fpe']:.0f})")
    lines.append("\u26a0\ufe0f Risks: " + ("; ".join(risks) if risks
                 else "no elevated flags in this snapshot") + ".")
    if data.get("fscore") is not None:
        stance = "constructive" if data["fscore"] >= 60 else (
            "cautious" if data["fscore"] >= 45 else "defensive")
        lines.append(f"\u2705 Conclusion: {stance} \u2014 {data.get('fgrade', '?')}-grade "
                     f"business{((' · ' + dv['label'] + ' on value') if dv.get('label') else '')}.")
    lines.extend(_watchlist_lines("stock", symbol))
    return lines


def outlook_crypto(symbol, data):
    lines = [""]
    if data.get("cscore") is not None:
        lines.append(f"\U0001f4cb Executive summary: {data.get('cgrade', '?')} momentum gauge "
                     f"({data['cscore']:.0f}/100) \u00b7 {_trend_word(data.get('chg_30d'))} over 30d.")
    lines.append(f"\U0001f52e Outlook \u2014 short term {_trend_word(data.get('chg_7d'))}; "
                 f"medium term {_trend_word(data.get('chg_30d'))}.")
    risks = []
    if (data.get("ath_pct") or 0) < -50:
        risks.append("deep drawdown zone \u2014 bounce or breakdown territory")
    if (data.get("volume_mcap") or 1) < 0.01:
        risks.append("thin turnover vs size")
    lines.append("\u26a0\ufe0f Risks: " + ("; ".join(risks) if risks
                 else "no elevated flags in this snapshot") + ".")
    if data.get("cscore") is not None:
        lines.append(f"\u2705 Conclusion: {data.get('cgrade', '?')}-grade tape; "
                     f"trend {_trend_word(data.get('chg_30d'))}.")
    lines.extend(_watchlist_lines("crypto", symbol))
    return lines


def _trend_structure_line(data):
    """Price against its 50d and 200d averages, read as one structure."""
    a, b = data.get("vs_sma50"), data.get("vs_sma200")
    if a is None and b is None:
        return None
    bits = []
    if a is not None:
        bits.append(f"{'above' if a >= 0 else 'below'} 50d ({a:+.1f}%)")
    if b is not None:
        bits.append(f"{'above' if b >= 0 else 'below'} 200d ({b:+.1f}%)")
    read = _structure_word(a, b)
    return "Trend: " + " \u00b7 ".join(bits) + (f" \u2192 {read}" if read else "")


def _structure_word(a, b):
    if a is None or b is None:
        return None
    if a >= 0 and b >= 0:
        return "uptrend intact" if a < 8 else "uptrend, stretched above the 50d"
    if a < 0 and b >= 0:
        return "pullback inside an uptrend"
    if a >= 0 and b < 0:
        return "bounce inside a downtrend"
    return "downtrend intact"


def _cot_read(cot):
    """One-line reading of the CFTC positioning."""
    net, wow = cot.get("net_long") or 0, cot.get("wow")
    side = "long" if net > 0 else "short"
    if wow is None or net == 0:
        return f"Funds are net {side}."
    swing = abs(wow) / abs(net) * 100 if net else 0
    if (net > 0) == (wow > 0):
        pace = "adding" if swing >= 3 else "holding"
        return f"Funds are {pace} to the {side} side ({swing:.0f}% of the position this week)."
    pace = "trimming" if swing < 10 else "cutting"
    return f"Funds are {pace} the {side} side ({swing:.0f}% of the position this week)."


def _macro_lines(symbol, data, macro):
    """Dollar, yields and the gold/silver ratio for the metals, each with
    the direction that matters for the metal."""
    lines = []
    usd, tnx = macro.get("usd"), macro.get("us10y")
    bits = []
    if usd and usd.get("last") is not None:
        bits.append(f"USD index {usd['last']:.1f} ({pct(usd.get('chg_1m'), 1)} 1m)")
    if tnx and tnx.get("last") is not None:
        d = tnx.get("delta_1m")
        bp = f" ({d * 100:+.0f}bp 1m)" if d is not None else ""
        bits.append(f"US 10y {tnx['last']:.2f}%{bp}")
    gs = data.get("gold_silver")
    if gs:
        bits.append(f"gold/silver {gs:.0f}")
    if bits:
        lines.append("\U0001f310 Macro: " + " \u00b7 ".join(bits))
    reads = []
    if usd and usd.get("chg_1m") is not None:
        if usd["chg_1m"] <= -1:
            reads.append("a softer dollar is a tailwind")
        elif usd["chg_1m"] >= 1:
            reads.append("a firmer dollar is a headwind")
    if tnx and tnx.get("delta_1m") is not None:
        if tnx["delta_1m"] <= -0.15:
            reads.append("falling yields lower the cost of holding metal")
        elif tnx["delta_1m"] >= 0.15:
            reads.append("rising yields raise the cost of holding metal")
    if gs:
        if gs >= 85:
            reads.append("silver is cheap against gold by the ratio")
        elif gs <= 60:
            reads.append("silver has outrun gold by the ratio")
    if reads:
        lines.append("   " + "; ".join(reads).capitalize() + ".")
    return lines


def _cfd_summary(data):
    """One sentence that puts range, trend and momentum together."""
    rp, off = data.get("range_pos"), data.get("off_high_pct")
    struct = _structure_word(data.get("vs_sma50"), data.get("vs_sma200"))
    parts = [f"price {_trend_word(data.get('chg_1y'))} over 1y"]
    if struct:
        parts.append(struct)
    if rp is not None:
        if rp >= 90:
            parts.append("trading at the top of its 1y range")
        elif rp <= 10:
            parts.append("trading at the bottom of its 1y range")
        elif off is not None:
            parts.append(f"{abs(off):.0f}% off the 1y high")
    return " \u00b7 ".join(parts)


def outlook_cfd(symbol, data):
    lines = [""]
    lines.append(f"\U0001f4cb Executive summary: {_cfd_summary(data)}.")
    lines.append(f"\U0001f52e Outlook \u2014 short term {_trend_word(data.get('chg_1w'))}; "
                 f"medium term {_trend_word(data.get('chg_3m'))}.")
    risks = []
    if (data.get("vol_pct") or 0) > 35:
        risks.append(f"high volatility ({data['vol_pct']:.0f}% annualized)")
    v1m, v1y = data.get("vol_pct_1m"), data.get("vol_pct")
    if v1m is not None and v1y and v1m > v1y * 1.5:
        risks.append(f"volatility expanding ({v1m:.0f}% last month vs {v1y:.0f}% 1y)")
    cot = data.get("cot") or {}
    if cot.get("net_long") and cot.get("wow"):
        if cot["net_long"] > 0 and cot["wow"] < 0:
            risks.append(f"crowded long trimming ({cot['wow']:+,} WoW)")
        elif cot["net_long"] < 0 and cot["wow"] > 0:
            risks.append(f"crowded short covering ({cot['wow']:+,} WoW)")
    w, m = data.get("chg_1w") or 0, data.get("chg_3m") or 0
    if (w > 0.5) != (m > 0.5) and (w < -0.5) != (m < -0.5):
        risks.append("short vs medium trend conflict \u2014 chop risk")
    a = data.get("vs_sma50")
    if a is not None and a >= 8:
        risks.append(f"stretched {a:+.0f}% above the 50d \u2014 mean-reversion risk")
    macro = data.get("macro") or {}
    usd = macro.get("usd") or {}
    if (usd.get("chg_1m") or 0) >= 1 and (data.get("chg_1m") or 0) > 0:
        risks.append("rallying into a firmer dollar")
    lines.append("\u26a0\ufe0f Risks: " + ("; ".join(risks) if risks
                 else "no elevated flags in this snapshot") + ".")
    spec = ""
    if cot.get("net_long") is not None:
        side = "net long" if cot["net_long"] > 0 else "net short"
        spec = f" \u00b7 specs {side}"
    struct = _structure_word(data.get("vs_sma50"), data.get("vs_sma200"))
    tape = f"{struct}, " if struct else ""
    lines.append(f"\u2705 Conclusion: {tape}{_trend_word(data.get('chg_3m'))} medium-term tape{spec}.")
    lines.extend(_watchlist_lines("cfd", symbol))
    return lines


def outlook_fx(symbol, data):
    lines = [""]
    v = data.get("verdict") or {}
    if v.get("base"):
        lines.append(f"\U0001f4cb Executive summary: {v.get('direction', 'mixed')} \u00b7 "
                     f"{'trends agree' if v.get('agree') else 'trends conflict'} \u00b7 "
                     f"{v.get('risk', 'medium')} risk.")
    lines.append(f"\U0001f52e Outlook \u2014 short term {_trend_word(data.get('chg_1w'))}; "
                 f"medium term {_trend_word(data.get('chg_3m'))}.")
    risks = []
    if not v.get("agree", True):
        risks.append("timeframe conflict \u2014 chop risk")
    if v.get("carry_bp") is not None and v["carry_bp"] < -200:
        risks.append(f"negative carry bleed ({v['carry_bp']:.0f}bp)")
    if (data.get("vol_pct") or 0) > 20:
        risks.append(f"elevated volatility ({data['vol_pct']:.0f}%)")
    lines.append("\u26a0\ufe0f Risks: " + ("; ".join(risks) if risks
                 else "no elevated flags in this snapshot") + ".")
    if v.get("base"):
        lines.append(f"\u2705 Conclusion: {v['direction']} bias, {v.get('risk', 'medium')} risk "
                     f"into coming data.")
    lines.extend(_watchlist_lines("forex", symbol))
    return lines


def fundamentals_report(kind, symbol, data, hub_mode, pro=True):
    e = escape
    lines = [f"\U0001f4ca <b>FUNDAMENTALS</b> \u2014 {e(symbol.upper())}"]
    if data is None:
        lines.append("Fundamentals feed unavailable right now \u2014 showing cached/derived data.")
        idx = constants.base_asset(symbol)
        lines.append(f"Asset: {e(idx)}")
    elif kind == constants.KIND_CRYPTO:
        lines.append(f"Name: {e(data.get('name', '-'))} \u00b7 Rank #{data.get('rank', '-')} \u00b7 Data: {hub_mode}")
        if data.get("price_usd") is not None:
            lines.append(f"Price: <b>${data['price_usd']:,.8g}</b> \u00b7 24h change {data.get('change_24h', 0):+.2f}%")
        for lbl, key in (("Market cap", "mcap"), ("Volume 24h", "volume_24h"),
                         ("High 24h", "high_24h"), ("Low 24h", "low_24h"),
                         ("All-time high", "ath"), ("All-time low", "atl")):
            v = data.get(key)
            if v is not None:
                lines.append(f"{lbl}: ${v:,.0f}" if key in ("mcap", "volume_24h") else f"{lbl}: {price(v)}")
        if data.get("desc"):
            lines.append(f"\U0001f4dd {e(data['desc'])}")
        if pro and data.get("cscore") is not None:
            lines.append("")
            lines.append(f"\U0001f3af Momentum gauge: <b>{data['cscore']:.0f}/100 "
                         f"({data.get('cgrade', '?')})</b> {meter(data['cscore'])}")
            pl = pillars_line(data.get("cpillars", {}),
                              (("trend", "Trend"), ("drawdown_posture", "ATH posture"),
                               ("liquidity", "Liquidity"), ("scale", "Scale")))
            if pl:
                lines.append(pl)
            if data.get("ath_pct") is not None and data["ath_pct"] < 0:
                lines.append(f"{abs(data['ath_pct']):.0f}% below all-time high")
            if data.get("sent_up") is not None:
                lines.append(f"\U0001f465 Community vote: {data['sent_up']:.0f}% bullish")
            if data.get("dev_commits_4w"):
                lines.append(f"\U0001f6e0 {int(data['dev_commits_4w']):,} dev commits in 4 weeks")
            if data.get("supply_mined_pct") is not None:
                lines.append(f"\u26cf {data['supply_mined_pct']:.1f}% of max supply mined")
            if pro:
                lines.extend(outlook_crypto(symbol, data))
    elif kind == constants.KIND_STOCK:
        if data and data.get("price") is not None:
            if data.get("longName"):
                lines.append(f"Name: {e(data['longName'])} \u00b7 Data: {e(data.get('source','derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"52w range {price(data.get('low_52w'))} \u2013 {price(data.get('high_52w'))}")
            lines.append(f"Avg volume (20d): {data.get('avg_volume_20', 0):,.0f}")
            lines.append(f"Move: 1w {pct(data.get('chg_1w'))} \u00b7 1m {pct(data.get('chg_1m'))} \u00b7 "
                         f"3m {pct(data.get('chg_3m'))} \u00b7 1y {pct(data.get('chg_1y'))}")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
            if data.get("marketCap"):
                lines.append(f"Market cap: ${data['marketCap']/1e9:,.2f}B")
            if data.get("trailingPE"):
                lines.append(f"P/E (trailing): {data['trailingPE']:.1f}")
            if data.get("stat_note"):
                suffix = " (price momentum only)" if "ETF" in data["stat_note"] else \
                    " \u2014 statement scores hidden until it recovers"
                lines.append(f"\U0001f4ca {e(data['stat_note'])}{suffix}.")
            if pro and data.get("fscore") is not None:
                lines.append("")
                ent = f" \u00b7 {e(data['stat_entity'])}" if data.get("stat_entity") else ""
                lines.append(f"\U0001f3af Fundamental score: <b>{data['fscore']:.0f}/100 "
                             f"({data.get('fgrade', '?')})</b> {meter(data['fscore'])}{ent}")
                pl = pillars_line(data.get("fpillars", {}),
                                  (("valuation", "Value"), ("profitability", "Profit"),
                                   ("growth", "Growth"), ("health", "Health"),
                                   ("momentum", "Momentum")))
                if pl:
                    lines.append(pl)
            if pro and data.get("fpe"):
                lines.append(f"FY P/E {data['fpe']:.1f} (last reported year)")
            pf = data.get("piotroski") or {}
            if pro and pf.get("total"):
                strength = ("strong" if pf["score"] >= 7 else
                            "average" if pf["score"] >= 4 else "weak")
                lines.append(f"\U0001f9fe Piotroski F-score: <b>{pf['score']}/{pf['total']}</b> "
                             f"({strength} financial trend)")
                rename = {"Cash-backed earnings": "cash accrual test (OCF \u2264 profit)"}
                failed = [rename.get(f_, f_) for f_ in (pf.get("failed") or [])[:3]]
                if failed:
                    lines.append("Fails: " + ", ".join(e(f_) for f_ in failed))
            if pro and data.get("earn_quality"):
                lines.append(f"\U0001f4a7 Cash conversion: {e(data['earn_quality'])}")
            for note in (data.get("fnotes") or [])[:2]:
                lines.append(f"\u2139 {e(note)}")
            dv = data.get("dcf_verdict")
            if pro and dv:
                dcf = data.get("dcf", {})
                lines.append(f"\U0001f4b0 Fair value: <b>${dv['intrinsic']:,.2f}</b> "
                             f"vs ${data['price']:,.2f} \u2014 "
                             f"{dv['label']} ({dv['mos_pct']:+.0f}% margin)")
                if dcf.get("assumptions"):
                    lines.append(f"\U0001f9ee DCF: {e(dcf['assumptions'])}")
            if pro:
                lines.extend(outlook_stock(symbol, data))
        else:
            lines.append(e(symbol) + " fundamentals feed unavailable; see links below.")
    elif kind == constants.KIND_CFD:
        if data and data.get("price") is not None:
            lines.append(f"Market {e(symbol)} \u00b7 Data: {e(data.get('source', 'derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"1y range {price(data.get('low_1y'))} \u2013 {price(data.get('high_1y'))}")
            rp = data.get("range_pos")
            if rp is not None:
                off = data.get("off_high_pct")
                where = (f" \u00b7 {abs(off):.1f}% below the 1y high" if off is not None and off < -0.05
                         else " \u00b7 at the 1y high" if off is not None else "")
                lines.append(f"Range: {meter(rp)} {rp:.0f}% of 1y range{where}")
            lines.append(f"Move: 1w {pct(data.get('chg_1w'), 2)} \u00b7 1m {pct(data.get('chg_1m'), 2)} \u00b7 "
                         f"3m {pct(data.get('chg_3m'), 2)} \u00b7 1y {pct(data.get('chg_1y'), 2)}")
            tl = _trend_structure_line(data)
            if tl:
                lines.append(tl)
            v1m = data.get("vol_pct_1m")
            vol_note = (f" \u00b7 last month {v1m:.0f}%" if v1m is not None else "")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%{vol_note}")
            cot = data.get("cot")
            macro = data.get("macro") or {}
            if pro and (cot or macro):
                lines.append("")
            if pro and cot:
                arrow = "\U0001f7e2" if (cot.get("net_long") or 0) > 0 else "\U0001f534"
                wow = f" ({cot['wow']:+,} WoW)" if cot.get("wow") is not None else ""
                lines.append(f"{arrow} Large speculators net "
                             f"{cot['net_long']:+,} contracts{wow} \u00b7 w/e {cot['date']} (CFTC)")
                lines.append(f"   {_cot_read(cot)}")
            if pro and macro:
                lines.extend(_macro_lines(symbol, data, macro))
            if pro:
                lines.extend(outlook_cfd(symbol, data))
        else:
            lines.append(f"Market {e(symbol)} \u2014 fundamentals feed unavailable; see links below.")
    elif kind == constants.KIND_FOREX:
        if data and data.get("price") is not None:
            lines.append(f"Pair {e(symbol)} \u00b7 Data: {e(data.get('source','derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"1y range {price(data.get('low_1y'))} \u2013 {price(data.get('high_1y'))}")
            lines.append(f"Move: 1w {pct(data.get('chg_1w'), 2)} \u00b7 1m {pct(data.get('chg_1m'), 2)} \u00b7 "
                         f"3m {pct(data.get('chg_3m'), 2)} \u00b7 1y {pct(data.get('chg_1y'), 2)}")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
            v = data.get("verdict")
            if pro and v and v.get("base"):
                rb = constants.POLICY_RATES.get(v["base"], (None, "?"))
                rq = constants.POLICY_RATES.get(v["quote"], (None, "?"))
                carry = f"{v['carry_bp']:+.0f}bp" if v.get("carry_bp") is not None else "n/a"
                lines.append(f"\U0001f3db Carry: {v['base']} {rb[0]:.2f}% vs "
                             f"{v['quote']} {rq[0]:.2f}% \u2192 {carry} "
                             f"(rates {v.get('rates_asof', '')})")
                trend = ("trends agree" if v["agree"] else "trends conflict")
                lines.append(f"\U0001f4c8 {v['direction'].capitalize()} \u00b7 {trend} "
                             f"\u00b7 risk: {v['risk']}")
                st_b = constants.POLICY_STANCE.get(v["base"], "n/a")
                st_q = constants.POLICY_STANCE.get(v["quote"], "n/a")
                lines.append(f"\U0001f3db Policy stance: {v['base']} {st_b} \u00b7 "
                             f"{v['quote']} {st_q}")
            if pro:
                lines.extend(outlook_fx(symbol, data))
        else:
            lines.append(f"Currency pair {e(symbol)} \u2014 derived from recent candles (Data: {hub_mode}).")
        lines.append("Watch the economic calendar for rate, inflation and labour surprises.")
    return "\n".join(lines)


def links_block(links):
    lines = []
    for label, url in links:
        lines.append(f"\u2022 <a href=\"{escape(url)}\">{escape(label)}</a>")
    return "\n".join(lines)


def _linked(url, label):
    return f"<a href=\"{escape(url)}\">{escape(label)}</a>"


def related_reading(kind, symbol, links, news):
    """Links rewoven as one-line stories + hyperlinked headlines."""
    by_label = {label: url for label, url in links}
    s = symbol.upper()
    asset = s
    if kind == constants.KIND_CRYPTO:
        asset = constants.base_asset(s)
    lines = ["", DIV, "\U0001f4d6 <b>Go deeper</b>"]
    if kind == constants.KIND_CRYPTO:
        if "CoinGecko" in by_label:
            lines.append(f"Track {asset} live on {_linked(by_label['CoinGecko'], 'CoinGecko')} "
                         f"and {_linked(by_label.get('CoinMarketCap', by_label['CoinGecko']), 'CoinMarketCap')}.")
        if "Binance" in by_label:
            lines.append(f"Trade it on {_linked(by_label['Binance'], 'Binance')} or chart every "
                         f"tick on {_linked(by_label.get('TradingView', by_label['Binance']), 'TradingView')}.")
        if "Block Explorer" in by_label:
            lines.append(f"Verify supply and flows yourself on the {_linked(by_label['Block Explorer'], 'block explorer')}.")
    elif kind == constants.KIND_STOCK:
        if "Yahoo Finance" in by_label:
            lines.append(f"Read the full quote and company profile on {_linked(by_label['Yahoo Finance'], 'Yahoo Finance')}.")
        if "TradingView" in by_label:
            lines.append(f"Chart {s} against the market on {_linked(by_label['TradingView'], 'TradingView')}.")
        extra = [l for l in ("StockAnalysis", "Macrotrends") if l in by_label]
        if extra:
            lines.append("For filings-grade history: " + " and ".join(
                _linked(by_label[l], l) for l in extra) + ".")
    elif kind == constants.KIND_FOREX:
        if "Yahoo Finance" in by_label:
            lines.append(f"Follow {s} tick-by-tick on {_linked(by_label['Yahoo Finance'], 'Yahoo Finance')}.")
        if "Forex Calendar" in by_label:
            lines.append(f"Rates move on surprises \u2014 watch {_linked(by_label['Forex Calendar'], 'the economic calendar')}.")
        chatter = [l for l in ("FXStreet", "Investing.com") if l in by_label]
        if chatter:
            lines.append("Desk chatter: " + " and ".join(
                _linked(by_label[l], l) for l in chatter) + ".")
    elif kind == constants.KIND_CFD:
        if "TradingView" in by_label:
            lines.append(f"Chart {s} with futures overlays on {_linked(by_label['TradingView'], 'TradingView')}.")
        rest = [l for l in ("Investing.com", "FXStreet", "TradingView ideas", "Yahoo Finance")
                if l in by_label]
        if rest:
            parts = [_linked(by_label[l], l) for l in rest]
            lines.append("Wider reading: " + (parts[0] if len(parts) == 1
                         else ", ".join(parts[:-1]) + " and " + parts[-1]) + ".")
    if news:
        lines.append("")
        lines.append("\U0001f4f0 <b>Related headlines</b>")
        try:
            from ..analysis import sentiment as _sent
            mood = _sent.score_headlines(news)
        except Exception:
            mood = None
        if mood is not None:
            tone = "bullish" if mood >= 0.15 else ("bearish" if mood <= -0.15 else "mixed")
            lines.append(f"\U0001f9ed Headline mood: {tone} ({mood:+.2f}, {len(news)} stories)")
        for n in news[:4]:
            title = n["title"]
            if len(title) > 75:
                title = title[:74] + "\u2026"
            lines.append(f"\u2022 {_linked(n['url'], title)}")
    lines.append("")
    lines.append("\u26a0\ufe0f Educational research only \u2014 not financial advice. "
                 "Verify prices with your broker before acting.")
    return "\n".join(lines)


def news_block(news):
    lines = []
    for n in news:
        lines.append(f"\u2022 <a href=\"{escape(n['url'])}\">{escape(n['title'])}</a>")
    return "\n".join(lines)


def stats_report(s):
    """Admin /stats: what the tracked signals table has learned."""
    if not s.get("total"):
        return ("\U0001f4ca <b>SIGNAL STOCKS</b>\n"
                "No delivered signals tracked yet. Outcomes begin counting "
                "with the first watch/autopilot alert on this instance.")
    lines = ["\U0001f4ca <b>SIGNAL STOCKS</b>",
             f"{s.get('total', 0):,} signals \u00b7 "
             f"{s.get('open', 0):,} open \u00b7 {s.get('resolved', 0):,} resolved"]
    wr = s.get("win_rate_pct")
    ar = s.get("avg_r")
    tr = s.get("total_r")
    if wr is not None:
        lines.append(f"Hit rate <b>{wr:.1f}%</b> \u00b7 avg <b>{ar:+.2f}R</b> "
                     f"\u00b7 total <b>{tr:+.1f}R</b> "
                     f"\u00b7 worst streak <b>{s.get('worst_streak', 0)}</b>")
    for label, key in (("By style", "by_style"), ("By mode", "by_mode")):
        rows = s.get(key) or []
        if rows:
            lines.append("")
            lines.append(f"<b>{label}</b>")
            for r in rows:
                w = f"{r['win_pct']:.0f}%" if r["win_pct"] is not None else "-"
                a = f"{r['avg_r']:+.2f}R" if r["avg_r"] is not None else "-"
                lines.append(f"\u2022 {r['k']} \u00b7 n={r['n']} \u00b7 "
                             f"win {w} \u00b7 avg {a}")
    pairs = s.get("by_pair") or []
    if pairs:
        lines.append("")
        lines.append("<b>Most active pairs</b>")
        for r in pairs:
            w = f"{r['win_pct']:.0f}%" if r["win_pct"] is not None else "-"
            lines.append(f"\u2022 {r['k']} \u00b7 n={r['n']} \u00b7 win {w}")
    buckets = [b for b in (s.get("conf_buckets") or []) if b.get("n")]
    if buckets and any(b.get("resolved") for b in buckets):
        lines.append("")
        lines.append("<b>By confidence score</b>")
        for b in buckets:
            w = f"{b['win_pct']:.0f}%" if b["win_pct"] is not None else "-"
            lines.append(f"\u2022 {b['lo']:3d}\u2013{b['lo'] + 19:3d} \u00b7 "
                         f"n={b['n']} \u00b7 resolved={b['resolved']} \u00b7 win {w}")
    lines.append("")
    lines.append("\u2139 First-touch resolution: SL < TP1 < TP2 (same-bar "
                 "SL+TP counts as SL), no-touch expires by style window.")
    return "\n".join(lines)


def calibration_report(cal):
    """Phase-4 /calibration: realised hit rate & expectancy per bucket."""
    curve = cal.curve()
    overall = cal.overall()
    lines = ["\U0001f4c8 <b>CALIBRATION CURVE</b> "
             "(live data, resolved signals)"]
    if not overall["resolved"]:
        lines.append("\nNo resolved live signals yet. Outcomes accumulate "
                     "with each watch/autopilot alert \u2014 calibration "
                     "kicks in once a confidence tier clears 30 resolutions.")
        return "\n".join(lines)
    lines.append(f"{overall['resolved']:,} resolved \u00b7 hit rate "
                 f"<b>{overall['win_pct']:.1f}%</b> \u00b7 avg "
                 f"<b>{overall['avg_r']:+.2f}R</b>")
    groups = {}
    for row in curve:
        groups.setdefault((row["style"], row["mode"]), []).append(row)
    for (style, mode), buckets in sorted(groups.items()):
        lines.append("")
        label_s = constants.STYLE_PROFILE[style]["label"]
        label_m = constants.MODE_PROFILE[mode]["label"]
        lines.append(f"<b>{label_s} \u00b7 {label_m}</b> (emit above "
                     f"{constants.SIGNAL_THRESHOLDS[style][mode]:.0f})")
        for b in buckets:
            exp = f"{b['expectancy']:+.2f}R" if b["expectancy"] is not None else "-"
            avg = f"{b['avg_r']:+.2f}R" if b["avg_r"] is not None else "-"
            win = f"{b['win_pct']:.0f}%" if b["win_pct"] is not None else "-"
            mark = "\u2713" if b["credible"] else "\u2026"
            lines.append(f"\u2022 {b['lo']:3d}\u2013{b['lo'] + 9:3d} \u00b7 "
                         f"n={b['resolved']} \u00b7 win {win} \u00b7 avg {avg} "
                         f"\u00b7 exp {exp} {mark}")
    recs = cal.recommend()
    lines.append("")
    if recs:
        lines.append("<b>Suggested gate changes</b> (no auto-change \u2014 "
                     "for review)")
        for r in recs:
            label_s = constants.STYLE_PROFILE[r["style"]]["label"]
            label_m = constants.MODE_PROFILE[r["mode"]]["label"]
            lines.append(f"\u2022 {label_s}/{label_m}: emit gate "
                         f"{r['current_gate']:.0f} \u2192 "
                         f"{r['proposed_gate']:.0f} \u00b7 a credible tier is "
                         f"paying {r['expectancy']:+.2f}R avg "
                         f"({r['n']} outcomes)")
    else:
        if all(b["credible"] for (_, bs) in groups.items() for b in bs):
            lines.append("All emitted tiers are paying \u2265 "
                         f"{_calibration.MIN_EXPECTANCY_R:.2f}R and every "
                         "bucket is credible \u2014 no gate change suggested.")
        else:
            lines.append("No gate change yet: credible tiers need "
                         f"\u2265 {_calibration.MIN_RESOLVED} resolutions "
                         "each \u2014 credits above the emit line determine.")
    lines.append("\n\u2139 Live signals only \u2014 demo/synthetic rows "
                 "never tune thresholds. Re-run after each batch of "
                 "resolutions.")
    return "\n".join(lines)


def _age(seconds):
    if seconds is None:
        return "-"
    secs = int(seconds)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


SOURCE_LABEL = {
    "binance": "Binance",
    "ccxt": "ccxt",
    "yahoo": "Yahoo (delayed)",
    "synthetic": "synthetic (demo)",
}


def verify_feed_report(p):
    """Admin /verifyfeed: how one pair is actually served right now."""
    e = escape
    lines = [f"\U0001f4e1 <b>FEED CHECK</b> {e(p['pair'])} \u00b7 {e(p['tf'])}"]
    lines.append(f"configured: <b>{p['static_tier']}</b> tier \u00b7 spread "
                 f"estimate {p['spread_bps']} bps")
    if p.get("venue_symbol"):
        lines.append(f"venue symbol: <code>{e(p['venue_symbol'])}</code>")

    if p.get("window_label"):
        if p.get("in_window"):
            lines.append(f"session: \u2705 inside {e(p['window_label'])}")
        else:
            nxt = p.get("next_open")
            tail = f" \u00b7 next open {e(nxt)}" if nxt else ""
            lines.append(f"session: \u23f8 outside {e(p['window_label'])}{tail}")
    elif p.get("scalp_class") == "crypto":
        lines.append("session: 24/7 \u00b7 crypto never closes")
    else:
        lines.append("session: n/a \u00b7 this instrument is not scalped")

    if p.get("probe_error"):
        lines.append(f"feed probe failed: {e(p['probe_error'])}")
        lines.append("\U0001f6a9 Diagnostics only \u00b7 nothing was changed.")
        return "\n".join(lines)

    label = SOURCE_LABEL.get(p["served_by"], p["served_by"])
    lines.append(f"serving: <b>{label}</b> \u00b7 {p['tier']} tier")
    if p["fresh_ok"]:
        lines.append(f"freshness: \u2705 PASS \u00b7 last bar "
                     f"{_age(p['last_age_s'])} old")
    else:
        lines.append(f"freshness: \u274c {e(p['fresh_reason'] or 'FAIL')}")
    if p["scalp_ok"]:
        lines.append("scalping: allowed \u2705")
    else:
        lines.append(f"scalping: blocked \u00b7 {e(p['scalp_reason'] or '')}")
    lines.append(f"price: <b>{price(p['last_price'])}</b>")
    lines.append("\U0001f6a9 Diagnostics only \u00b7 nothing was changed.")
    return "\n".join(lines)


def watch_list(rows):
    if not rows:
        return ("\U0001f440 <b>Watch list</b>\n\nNothing here yet. Add your first "
                "alert with the button below or /watch PAIR STYLE MODE.")
    lines = ["\U0001f440 <b>Watch list</b>", ""]
    for w in rows:
        state = "\u2705 alerted" if w["last_signal_ts"] else "\u23f3 listening"
        lines.append(f"<b>{w['pair']}</b> \u00b7 {w['style']} \u00b7 {w['mode']} \u00b7 {state}")
    lines.append("")
    lines.append("Tap a row to remove it.")
    return "\n".join(lines)


def pro_gate(feature, can_trial, trial_days=None):
    from .. import constants as _c
    days = trial_days or _c.TRIAL_DAYS
    lines = [f"\U0001f512 <b>{feature} is a PRO feature</b>",
             "Live alerts, autopilot signals and deep research are what PRO pays for. "
             "Analyze stays free forever."]
    if can_trial:
        lines.append(f"\U0001f381 Start with a <b>{days}-day free trial</b> \u2014 "
                     "full PRO, no payment needed.")
    return "\n".join(lines)


def site_pro_activated_text(row, until):
    """Confirmation for PRO bought by card on the website."""
    import datetime
    date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
    months = int(row.get("months", 0))
    span = "1 month" if months == 1 else f"{months} months"
    return (f"✅ <b>PRO activated</b> · {span} until {date}.\n"
            "Thanks for your purchase on printezy.money — "
            "your watches resume automatically. Enjoy!")


REDEEM_TEXT = {
    "disabled": "Website codes are not enabled on this bot yet.",
    "bad_format": ("That doesn't look like a PRO code. Codes look like "
                   "<code>EZY-AB12-CD34</code> \u2014 copy it from the printezy.money "
                   "success page or your receipt email."),
    "not_found": ("Code not found or already used. Check for typos, or contact "
                  "support with your receipt if you're sure it's right."),
    "already": "That code was already redeemed on this account \u2014 PRO is active.",
    "expired": "That code has expired. Ask whoever gave it to you for a fresh one.",
    "discount": ("\U0001f3f7 <b>{percent}% off</b> applied to your next PRO purchase. "
                 "Open /plans \u2014 Stars, card and USDT prices all show the discount."),
    "used": "That code has already been used up.",
    "error": "Could not reach printezy.money right now \u2014 try again in a minute.",
    "prompt": ("\U0001f39f Paste your PRO code from printezy.money "
               "(looks like <code>EZY-AB12-CD34</code>):"),
}


def gift_code_activated_text(rec, until):
    import datetime
    date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
    if rec.get("kind") == "trial":
        d = int(rec.get("days", 0))
        span = "1 day" if d == 1 else f"{d} days"
        return (f"\U0001f381 <b>PRO trial activated</b> \u00b7 {span}, until {date}.\n"
                "Try watches, autopilot and deep fundamentals \u2014 on the house.")
    months = int(rec.get("months", 0))
    span = "1 month" if months == 1 else f"{months} months"
    return (f"\U0001f381 <b>PRO activated</b> \u00b7 {span} until {date}.\n"
            "Gift code accepted \u2014 your watches resume automatically. Enjoy!")


def code_kind_label(rec):
    from .. import constants as _c
    kind = rec.get("kind", "gift")
    if kind == "trial":
        return f"{rec.get('days')}-day trial"
    if kind == "discount":
        return f"{rec.get('percent')}% off"
    plan = _c.PLANS.get(rec.get("tier"), {})
    return f"gift {plan.get('label', rec.get('tier'))}"


def codes_minted_text(codes, rec, uses, days):
    lines = [f"\U0001f39f <b>{len(codes)} code(s)</b> \u00b7 {code_kind_label(rec)} \u00b7 "
             f"{uses} use(s) each \u00b7 valid {days} days"]
    lines += [f"<code>{c}</code>" for c in codes]
    lines.append("Customers activate with /redeem CODE.")
    return "\n".join(lines)


def codes_list_text(rows):
    import datetime
    if not rows:
        return "No live gift codes. Mint with /mkcode TIER [COUNT] [USES]."
    lines = ["<b>Live gift codes</b>"]
    for code, rec, _live in rows[:40]:
        exp = datetime.datetime.fromtimestamp(rec.get("expires_at", 0),
                                              datetime.timezone.utc).strftime("%d %b")
        lines.append(f"<code>{code}</code> \u00b7 {code_kind_label(rec)} \u00b7 "
                     f"{rec.get('uses_left', 0)} left \u00b7 exp {exp}")
    if len(rows) > 40:
        lines.append(f"\u2026 and {len(rows) - 40} more")
    return "\n".join(lines)


def plans_text(trial_eligible, trial_days=None, discount=None):
    from .. import billing as _b
    from .. import constants as _c
    days = trial_days or _c.TRIAL_DAYS
    pct = int((discount or {}).get("percent", 0) or 0)
    lines = ["\U0001f48e <b>EzyAi PRO</b> \u2014 alerts, autopilot, deep research.",
             "Analyze stays free on every plan.", DIV]
    for tid in _c.PLAN_ORDER:
        line = "\u25b8 " + _b.tier_line(tid)
        if pct:
            p = _c.PLANS[tid]
            line = (f"\u25b8 {p['label']} \u2014 <s>${p['usd']:.2f}</s> "
                    f"<b>${_b.discounted_usd(p['usd'], pct):.2f}</b>")
            if p["badge"]:
                line += f" \u2b50 {p['badge']}"
        lines.append(line)
    lines.append("")
    if pct:
        lines.append(f"\U0001f3f7 <b>{pct}% off</b> applied \u2014 code "
                     f"<code>{escape(str(discount.get('code', '')))}</code>, one purchase.")
    if trial_eligible:
        lines.append(f"\U0001f381 New here? Take the <b>{days}-day free trial</b> first \u2014 "
                     "full PRO, no card.")
    else:
        lines.append("Trial already used on this account.")
    lines.append("Pay with Stars \u26a1, card \U0001f4b3, or USDT \u20ae.")
    lines.append("One-off payments \u2014 nothing auto-renews. Stars purchases "
                 "follow Telegram's refund rules; card payments are handled by "
                 "Stripe. Questions: /account.")
    return "\n".join(lines)


def account_text(status, watches_n, autopilot_on, comped=False, trial_days=None):
    import datetime
    import time as _t
    from .. import constants as _c
    days = trial_days or _c.TRIAL_DAYS
    plan, until = status["plan"], status.get("until", 0.0)
    if comped:
        state = "<b>PRO</b> \u00b7 team access"
    elif plan == "pro":
        date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
        state = f"<b>PRO</b> until {date}"
    elif plan == "trial":
        left = max(0, int((until - _t.time()) / 86400) + 1)
        state = (f"<b>Trial</b> \u00b7 {left} day(s) left "
                 f"{meter(left, days, width=3)}")
    else:
        state = "<b>Free</b> (Analyze only)"
    if isinstance(autopilot_on, (list, tuple)):
        auto = ("on (" + ", ".join(p.style for p in autopilot_on) + ")"
                if autopilot_on else "off")
    else:
        auto = "on" if autopilot_on else "off"
    lines = ["\U0001f464 <b>Your account</b>", f"Plan: {state}",
             f"Watching: {watches_n} pair(s) \u00b7 Autopilot: {auto}"]
    if plan == "free" and not comped and not status.get("trial_used"):
        lines.append(f"\U0001f381 You still have your {days}-day free trial \u2014 see /plans.")
    return "\n".join(lines)


def pro_upsell_note():
    return ("\n\U0001f512 <i>PRO unlocks scores, DCF fair value, COT positioning "
            "and the macro verdict \u2014 /plans</i>")


def expiry_nudge_text():
    return ("\u23f8 Your watches and autopilot are <b>paused</b> \u2014 live alerts "
            "are now a PRO feature.\nYour setup is saved and resumes the moment "
            "you upgrade. See /plans (3-day free trial included).")


def dashboard_view(watches, pilots, data_mode):
    """`pilots` is the chat's running autopilots (a list); a single pilot
    or None is accepted for older callers."""
    if pilots is None:
        pilots = []
    elif not isinstance(pilots, (list, tuple)):
        pilots = [pilots]
    lines = ["\U0001f4cb <b>EzyAi dashboard</b>", ""]
    lines.append(f"\U0001f440 <b>Watching</b> \u00b7 {len(watches)}")
    if watches:
        for w in watches[:4]:
            dot = "\U0001f7e2" if w.get("last_signal_ts") else "\U0001f7e1"
            lines.append(f"{dot} <b>{w['pair']}</b> \u00b7 {w['style']} \u00b7 {w['mode']}")
        if len(watches) > 4:
            lines.append(f"and {len(watches) - 4} more")
    else:
        lines.append("Tap Watchlist to add your first alert.")
    lines.append("")
    lines.append(f"\U0001f916 <b>Autopilot</b> \u00b7 {'ON' if pilots else 'off'}")
    for p in pilots:
        scope = scope_label(getattr(p, "exclude", None))
        tail = f" \u00b7 {scope}" if scope else ""
        lines.append(f"\u2022 {p.style} \u00b7 {p.mode}{tail}")
    lines.append("")
    lines.append(f"Feed {data_mode} \u00b7 /start for the menu")
    return "\n".join(lines)


def confirm_watch_text(pair, style, mode, check_s, rr, risk_pct):
    return (
        f"\U0001f514 <b>Confirm watch</b>\n"
        f"<b>{pair}</b> \u00b7 {style}/{mode}\n\n"
        f"Checks every {check_s}s \u00b7 target {rr}R \u00b7 risk {risk_pct:.1f}% per trade\n\n"
        "You get a signal the moment a setup passes your risk rules."
    )


def confirm_auto_text(style, mode, daily_limit, scope=None):
    scope_line = f"\nScans {scope}" if scope else ""
    return (
        f"\U0001f916 <b>Confirm autopilot</b>\n"
        f"{style}/{mode} \u00b7 up to {daily_limit} signals a day{scope_line}\n\n"
        "Stop it any time from the Autopilot button."
    )


def watch_cap_text(limit):
    return (f"\U0001f6d1 You already watch {limit} pairs \u2014 that's the cap per "
            "account so alerts stay useful. Remove one below to add another.")


def watch_added_text(pair, style, mode):
    return (f"\u2705 <b>Watch live</b>\n<b>{pair}</b> \u00b7 {style}/{mode}\n\n"
            "You get a signal the moment a setup passes your risk rules.")


def auto_started_text(style, mode, scope=None):
    scope_line = f"\nScans {scope}" if scope else ""
    return (f"\u2705 <b>Autopilot live</b>\n{style}/{mode}{scope_line}\n\n"
            "Scanning the market for you. Sit back.")


def autopilot_status_text(pilots):
    """Status card for the Autopilot button when scanners are running."""
    lines = ["\U0001f916 <b>Autopilot</b> \u00b7 ON", ""]
    for p in pilots:
        scope = scope_label(getattr(p, "exclude", None))
        tail = f" \u00b7 {scope}" if scope else ""
        lines.append(f"\u2022 {p.style} \u00b7 {p.mode}{tail}")
    lines.append("")
    lines.append("Each style scans within its own daily limit.")
    return "\n".join(lines)


def scope_label(exclude):
    """Human label for an autopilot's asset-class scope, or None when it
    scans everything."""
    ex = sorted(set(exclude or ()))
    if not ex:
        return None
    names = {k: v for k, v in constants.ASSET_CLASSES}
    kept = [v for k, v in constants.ASSET_CLASSES if k not in ex]
    if len(kept) <= 2:
        return " and ".join(kept) + " only"
    return "without " + " or ".join(names[k] for k in ex if k in names)


def auto_universe_note(style, universe_size):
    """Line appended to the autopilot start message when the style restricts
    the scanned universe (scalping = real-time feeds only)."""
    if style not in ("scalping",):
        return ""
    live = universe_size
    total = len(constants.ALL_UNIVERSE)
    return (f"\n\n\U0001f30d Scalping scans pairs on live feeds: {live} of "
            f"{total} in the universe are real-time right now.")


def autopilot_view(pilots):
    if not pilots:
        return "No autopilot running. Start one with /autopilot STYLE MODE"
    lines = ["<b>Active autopilots</b>"]
    for p in pilots:
        lines.append(f"\u2022 chat {p['chat_id']} \u2014 {p['style']}/{p['mode']}")
    return "\n".join(lines)


def help_text():
    return (
        "\U0001f4d6 <b>EzyAi commands</b>\n\n"
        "\U0001f50d <b>Research (free)</b>\n"
        "/analyze \u2014 market analysis with entry, stop and targets\n"
        "/quote PAIR \u2014 quick live price\n"
        "/fundamentals PAIR \u2014 stocks, crypto, forex and CFD research\n\n"
        "\U0001f512 <b>PRO \u2014 alerts & automation</b>\n"
        "/watch PAIR STYLE MODE \u2014 live alerts for a pair\n"
        "    STYLE: scalping | intraday | swing\n"
        "    MODE:  safe | normal | aggressive\n"
        "/watches \u2014 list your active watches\n"
        "/unwatch PAIR [STYLE] \u2014 stop alerts for a pair (one style, or all)\n"
        "/autopilot STYLE MODE [-crypto -stocks ...] \u2014 auto signals across the market\n"
        "/stopautopilot [STYLE] \u2014 stop random signals (one style, or all)\n\n"
        "\U0001f464 <b>Account</b>\n"
        "/plans \u2014 trial and PRO plans\n"
        "/account \u2014 plan, watches and autopilot status\n"
        "/redeem CODE \u2014 activate PRO bought on printezy.money\n"
        "/dashboard \u2014 everything at a glance\n"
        "/help \u2014 this message\n\n"
        "Examples: crypto BTCUSD \u00b7 forex EURUSD \u00b7 stock AAPL \u00b7 cfd XAUUSD\n"
        "Modes affect frequency and risk: safe (fewer, tighter), aggressive (more, wider).\n\n"
        "\u26a0\ufe0f Educational research only \u2014 not financial advice. "
        "Autopilot daily limits reset at midnight UTC."
    )
from html import escape

from .. import constants

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
    lines = []
    trend = a["trend"]
    spec = a["spec"]
    ind = a["ind"]
    style_label = constants.STYLE_PROFILE[a["style"]]["label"]
    mode_label = constants.MODE_PROFILE[a["mode"]]["label"]

    lines.append(f"\U0001f4c8 <b>{e(a['pair'])}</b> \u00b7 {BADGE[a['side']]} "
                 f"{SIDE_LABEL[a['side']]} idea")
    lines.append(f"{style_label} \u00b7 {mode_label} \u00b7 TF {a['base_tf']} (trend: {a['direction_tf']})")
    lines.append(f"Price: <b>{price(a['price'])}</b> \u00b7 Data: {a['data_mode']}")
    bb = ind["bb"]
    bb_bar = position_bar(bb["lower"], bb["upper"], a["price"])
    if bb_bar:
        lines.append(f"Bollinger position: {bb_bar} [{price(bb['lower'])}, {price(bb['upper'])}]")
    lines.append("")
    adx_text = f"ADX {trend['adx']:.0f}" if trend["adx"] is not None else "ADX -"
    lines.append(f"<b>Trend</b>: {_arrow(trend['direction'])} {e(trend['direction'].upper())} ({trend['strength']}) \u00b7 "
                 f"EMA align {trend['align']} \u00b7 {adx_text}")
    lines.append(f"RSI {ind['rsi'] if ind['rsi'] is not None else '-'} \u00b7 "
                 f"MACD hist {ind['macd_hist'] if ind['macd_hist'] is not None else '-'} \u00b7 "
                 f"ATR {price(ind['atr'])}")
    if a["levels"]["support"] or a["levels"]["resistance"]:
        sup = " / ".join(price(s) for s in a["levels"]["support"]) or "-"
        res = " / ".join(price(r) for r in a["levels"]["resistance"]) or "-"
        lines.append(f"Support: {sup}  \u00b7  Resistance: {res}")
    lines.append("")

    if spec:
        lines.append(f"<b>Signal</b>: {BADGE[a['side']]} {SIDE_LABEL[a['side']]}")
        lines.append(f"Entry zone: <b>{price(spec['zone_low'])}</b> \u2013 <b>{price(spec['zone_high'])}</b> "
                     f"(market {price(spec['market'])}, limit {price(spec['limit'])})")
        lines.append(f"Stop loss : <b>{price(spec['sl'])}</b>")
        lines.append(f"Take profit 1: <b>{price(spec['tp1'])}</b> \u00b7 Take profit 2: <b>{price(spec['tp2'])}</b>")
        lines.append(f"Risk/reward {spec['rr']:.1f} \u00b7 Risk/trade {spec['risk_pct']:.1f}% of capital")
        lines.append(f"Setup score: <b>{a['confidence']:.0f}/100</b> {meter(a['confidence'])} "
                     "(aligned indicators, not a win probability)")
    else:
        lines.append(f"<b>Signal</b>: {BADGE[a['side']]} No trade setup \u2014 trend {trend['direction']}, "
                     f"setup score {a['confidence']:.0f}/100 {meter(a['confidence'])}")
    lines.append("")

    if a["reasons"]:
        lines.append("<b>Why</b>")
        for r in a["reasons"]:
            lines.append(f"\u2022 {e(r)}")
    lines.append("")

    if a["exit_notes"]:
        lines.append("<b>Exit rules</b>")
        for note in a["exit_notes"]:
            lines.append(f"\u2022 {note}")
        lines.append(f"\u2022 Position sizing: size = (capital \u00d7 {spec['risk_pct']:.1f}%) / (entry \u2212 stop)")
        lines.append(f"\u2022 Horizon: {a['hold_horizon']}")
    lines.append("")
    lines.append("\U000026a0\ufe0f Educational confluence only. Not financial advice. Demo data can be used "
                 "when live feeds fail \u2014 verify prices with your broker before acting.")
    return "\n".join(lines)


def signal_message(sig, source="watch"):
    e = escape
    header = "\U0001f514 <b>LIVE TRADE SIGNAL</b>" if source == "watch" else \
        "\U0001f680 <b>AUTO SIGNAL</b>"
    lines = [f"{header} \u2014 {e(sig['pair'])}"]
    lines.append(f"{BADGE[sig['side']]} <b>{SIDE_LABEL[sig['side']]}</b> \u00b7 "
                 f"{constants.STYLE_PROFILE[sig['style']]['label']} \u00b7 "
                 f"{constants.MODE_PROFILE[sig['mode']]['label']} \u00b7 TF {sig['tf']}")
    lines.append(f"Entry zone: <b>{price(sig['entry_zone'][0])}</b> \u2013 <b>{price(sig['entry_zone'][1])}</b>")
    lines.append(f"Stop loss : <b>{price(sig['sl'])}</b> \u00b7 RR target {sig['rr']:.1f}")
    lines.append(f"TP1: <b>{price(sig['tp1'])}</b> \u00b7 TP2: <b>{price(sig['tp2'])}</b> \u00b7 "
                 f"Risk {sig['risk_pct']:.1f}% \u00b7 Setup score {sig['confidence']:.0f}/100 {meter(sig['confidence'])}")
    sup = " / ".join(price(s) for s in (sig.get("support") or [])) or "-"
    res = " / ".join(price(r) for r in (sig.get("resistance") or [])) or "-"
    lines.append(f"Levels \u2014 support: {sup} \u00b7 resistance: {res}")
    if sig["reasons"]:
        lines.append("Why: " + "; ".join(e(r) for r in sig["reasons"][:3]))
    lines.append("\U000026a0\ufe0f Not financial advice.")
    return "\n".join(lines)


def quote_report(pair, tick):
    e = escape
    bar = position_bar(tick.get("low"), tick.get("high"), tick.get("price"))
    lines = [
        f"\U0001f4b2 <b>{e(pair)}</b> \u2014 <b>{price(tick['price'])}</b>",
        f"24h change: {tick['change_pct']:+.2f}% \u00b7 High {price(tick['high'])} \u00b7 Low {price(tick['low'])}",
    ]
    if bar:
        lines.append(f"Day range: {price(tick['low'])} {bar} {price(tick['high'])}")
    lines.append(f"Volume: {tick['volume']:,.0f} ({tick['quote']}) \u00b7 Data: {tick.get('mode', 'live')}")
    lines.append("\u26a0\ufe0f Indicative price, not financial advice.")
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


def outlook_cfd(symbol, data):
    lines = [""]
    lines.append(f"\U0001f4cb Executive summary: price {_trend_word(data.get('chg_1y'))} over 1y.")
    lines.append(f"\U0001f52e Outlook \u2014 short term {_trend_word(data.get('chg_1w'))}; "
                 f"medium term {_trend_word(data.get('chg_3m'))}.")
    risks = []
    if (data.get("vol_pct") or 0) > 35:
        risks.append(f"high volatility ({data['vol_pct']:.0f}% annualized)")
    cot = data.get("cot") or {}
    if cot.get("net_long") and cot.get("wow"):
        if cot["net_long"] > 0 and cot["wow"] < 0:
            risks.append(f"crowded long trimming ({cot['wow']:+,} WoW)")
        elif cot["net_long"] < 0 and cot["wow"] > 0:
            risks.append(f"crowded short covering ({cot['wow']:+,} WoW)")
    w, m = data.get("chg_1w") or 0, data.get("chg_3m") or 0
    if (w > 0.5) != (m > 0.5) and (w < -0.5) != (m < -0.5):
        risks.append("short vs medium trend conflict \u2014 chop risk")
    lines.append("\u26a0\ufe0f Risks: " + ("; ".join(risks) if risks
                 else "no elevated flags in this snapshot") + ".")
    spec = ""
    if cot.get("net_long") is not None:
        side = "net long" if cot["net_long"] > 0 else "net short"
        spec = f" \u00b7 specs {side}"
    lines.append(f"\u2705 Conclusion: {_trend_word(data.get('chg_3m'))} medium-term tape{spec}.")
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
            lines.append(f"Move: 1w {pct(data.get('chg_1w'), 2)} \u00b7 1m {pct(data.get('chg_1m'), 2)} \u00b7 "
                         f"3m {pct(data.get('chg_3m'), 2)} \u00b7 1y {pct(data.get('chg_1y'), 2)}")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
            cot = data.get("cot")
            if pro and cot:
                lines.append("")
                arrow = "\U0001f7e2" if (cot.get("net_long") or 0) > 0 else "\U0001f534"
                wow = f" ({cot['wow']:+,} WoW)" if cot.get("wow") is not None else ""
                lines.append(f"{arrow} Large speculators net "
                             f"{cot['net_long']:+,} contracts{wow} \u00b7 w/e {cot['date']} (CFTC)")
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


def watch_list(rows):
    if not rows:
        return ("No active watches yet \u2014 add your first alert with "
                "/watch PAIR STYLE MODE")
    lines = ["\U0001f440 <b>Active watch list</b>"]
    for w in rows:
        state = "\u2705 alerted" if w["last_signal_ts"] else "\u23f3 listening"
        lines.append(f"\u2022 <b>{w['pair']}</b> \u00b7 {w['style']}/{w['mode']} \u00b7 {state}")
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
    lines = ["\U0001f464 <b>Your account</b>", f"Plan: {state}",
             f"Watching: {watches_n} pair(s) \u00b7 Autopilot: {'on' if autopilot_on else 'off'}"]
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


def dashboard_view(watches, pilot, data_mode):
    lines = ["\U0001f4cb <b>EzyAi dashboard</b>"]
    if watches:
        parts = []
        for w in watches[:4]:
            dot = "\U0001f7e2" if w.get("last_signal_ts") else "\U0001f7e1"
            parts.append(f"{dot} <b>{w['pair']}</b> ({w['style']}/{w['mode']})")
        extra = f" +{len(watches) - 4} more" if len(watches) > 4 else ""
        lines.append(f"\U0001f440 Watching ({len(watches)}): " + ", ".join(parts) + extra)
    else:
        lines.append("\U0001f440 Watching (0): tap Watchlist to add your first alert.")
    if pilot is not None:
        lines.append(f"\U0001f916 Autopilot: <b>ON</b> \u00b7 {pilot.style}/{pilot.mode}")
    else:
        lines.append("\U0001f916 Autopilot: off")
    lines.append(f"Feed: {data_mode}")
    lines.append("Send /start anytime for the shortcut menu.")
    return "\n".join(lines)


def confirm_watch_text(pair, style, mode, check_s, rr, risk_pct):
    return (
        f"\U0001f514 <b>Confirm watch</b>\n<b>{pair}</b> \u00b7 {style}/{mode}\n"
        f"Checks every {check_s}s \u00b7 target RR {rr} \u00b7 risk {risk_pct:.1f}%/trade.\n"
        "You will get a signal the moment a setup passes your risk rules."
    )


def confirm_auto_text(style, mode, daily_limit):
    return (
        f"\U0001f916 <b>Confirm autopilot</b>\n{style}/{mode} \u00b7 random pairs \u00b7 "
        f"up to {daily_limit} signals/day.\n"
        "You can stop it anytime from the dashboard."
    )


def watch_cap_text(limit):
    return (f"\U0001f6d1 You already watch {limit} pairs \u2014 that's the cap per "
            "account so alerts stay useful. Remove one below to add another.")


def watch_added_text(pair, style, mode):
    return (f"\u2705 <b>Watch live:</b> {pair} \u00b7 {style}/{mode}\n"
            "You will get a signal the moment a setup passes your risk rules.")


def auto_started_text(style, mode):
    return (f"\u2705 Autopilot live: {style}/{mode}\n"
            "Scanning random pairs for you. Sit back.")


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
        "/unwatch PAIR \u2014 stop alerts for a pair\n"
        "/autopilot STYLE MODE \u2014 random-pair auto signals\n"
        "/stopautopilot \u2014 stop random signals\n\n"
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
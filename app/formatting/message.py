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


def analysis_report(a):
    e = escape
    lines = []
    trend = a["trend"]
    spec = a["spec"]
    ind = a["ind"]
    style_label = constants.STYLE_PROFILE[a["style"]]["label"]
    mode_label = constants.MODE_PROFILE[a["mode"]]["label"]

    lines.append(f"\U0001f4c8 <b>MARKET ANALYSIS</b> \u2014 {e(a['pair'])}")
    lines.append(f"Style: {style_label} \u00b7 Mode: {mode_label} \u00b7 TF {a['base_tf']} (trend: {a['direction_tf']})")
    lines.append(f"Price: <b>{price(a['price'])}</b> \u00b7 Data: {a['data_mode']}")
    lines.append("")
    adx_text = f"ADX {trend['adx']:.0f}" if trend["adx"] is not None else "ADX -"
    lines.append(f"<b>Trend</b>: {_arrow(trend['direction'])} {e(trend['direction'].upper())} ({trend['strength']}) \u00b7 "
                 f"EMA align {trend['align']} \u00b7 {adx_text}")
    lines.append(f"RSI {ind['rsi'] if ind['rsi'] is not None else '-'} \u00b7 "
                 f"MACD hist {ind['macd_hist'] if ind['macd_hist'] is not None else '-'} \u00b7 "
                 f"ATR {price(ind['atr'])}")
    lines.append(f"Bollinger: [{price(ind['bb']['lower'])}, {price(ind['bb']['mid'])}, {price(ind['bb']['upper'])}]")
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
        lines.append(f"Confidence: <b>{a['confidence']:.0f}/100</b>")
    else:
        lines.append(f"<b>Signal</b>: {BADGE[a['side']]} No trade setup \u2014 trend {trend['direction']}, "
                     f"confidence {a['confidence']:.0f}/100")
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
                 f"Risk {sig['risk_pct']:.1f}% \u00b7 Confidence {sig['confidence']:.0f}%")
    if sig["reasons"]:
        lines.append("Why: " + "; ".join(e(r) for r in sig["reasons"][:3]))
    lines.append("\U000026a0\ufe0f Not financial advice.")
    return "\n".join(lines)


def quote_report(pair, tick):
    e = escape
    return (
        f"{e(pair)} \u2014 <b>{price(tick['price'])}</b>\n"
        f"24h change: {tick['change_pct']:+.2f}% \u00b7 High {price(tick['high'])} \u00b7 Low {price(tick['low'])}\n"
        f"Volume: {tick['volume']:,.0f} ({tick['quote']}) \u00b7 Data: {tick.get('mode', 'live')}"
    )


def fundamentals_report(kind, symbol, data, hub_mode):
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
        links = []
        if data.get("website"):
            links.append(f"\u2022 Website: {e(data['website'])}")
        if data.get("explorer"):
            links.append(f"\u2022 Explorer: {e(data['explorer'])}")
        if data.get("whitepaper"):
            links.append(f"\u2022 Whitepaper: {e(data['whitepaper'])}")
        if links:
            lines.append("<b>Official links</b>")
            lines.extend(links)
    elif kind == constants.KIND_STOCK:
        if data and data.get("price") is not None:
            if data.get("longName"):
                lines.append(f"Name: {e(data['longName'])} \u00b7 Data: {e(data.get('source','derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"52w range {price(data.get('low_52w'))} \u2013 {price(data.get('high_52w'))}")
            lines.append(f"Avg volume (20d): {data.get('avg_volume_20', 0):,.0f}")
            lines.append(f"Move: 1w {data.get('chg_1w', 0):+.1f}% \u00b7 1m {data.get('chg_1m', 0):+.1f}% \u00b7 "
                         f"3m {data.get('chg_3m', 0):+.1f}% \u00b7 1y {data.get('chg_1y', 0):+.1f}%")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
            if data.get("marketCap"):
                lines.append(f"Market cap: ${data['marketCap']/1e9:,.2f}B")
            if data.get("trailingPE"):
                lines.append(f"P/E (trailing): {data['trailingPE']:.1f}")
        else:
            lines.append(e(symbol) + " fundamentals feed unavailable; see links below.")
    elif kind == constants.KIND_CFD:
        if data and data.get("price") is not None:
            lines.append(f"Market {e(symbol)} \u00b7 Data: {e(data.get('source', 'derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"1y range {price(data.get('low_1y'))} \u2013 {price(data.get('high_1y'))}")
            lines.append(f"Move: 1w {data.get('chg_1w', 0):+.2f}% \u00b7 1m {data.get('chg_1m', 0):+.2f}% \u00b7 "
                         f"3m {data.get('chg_3m', 0):+.2f}% \u00b7 1y {data.get('chg_1y', 0):+.2f}%")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
        else:
            lines.append(f"Market {e(symbol)} \u2014 fundamentals feed unavailable; see links below.")
    elif kind == constants.KIND_FOREX:
        if data and data.get("price") is not None:
            lines.append(f"Pair {e(symbol)} \u00b7 Data: {e(data.get('source','derived'))}")
            lines.append(f"Price: <b>{price(data['price'])}</b> \u00b7 "
                         f"1y range {price(data.get('low_1y'))} \u2013 {price(data.get('high_1y'))}")
            lines.append(f"Move: 1w {data.get('chg_1w', 0):+.2f}% \u00b7 1m {data.get('chg_1m', 0):+.2f}% \u00b7 "
                         f"3m {data.get('chg_3m', 0):+.2f}% \u00b7 1y {data.get('chg_1y', 0):+.2f}%")
            lines.append(f"Realized volatility (annualized): {data.get('vol_pct', 0):.0f}%")
        else:
            lines.append(f"Currency pair {e(symbol)} \u2014 derived from recent candles (Data: {hub_mode}).")
        lines.append("Watch the economic calendar for rate, inflation and labour surprises.")
    lines.append("")
    lines.append("<b>Links</b>")
    return "\n".join(lines)


def links_block(links):
    lines = []
    for label, url in links:
        lines.append(f"\u2022 <a href=\"{escape(url)}\">{escape(label)}</a>")
    return "\n".join(lines)


def news_block(news):
    lines = []
    for n in news:
        lines.append(f"\u2022 <a href=\"{escape(n['url'])}\">{escape(n['title'])}</a>")
    return "\n".join(lines)


def watch_list(rows):
    if not rows:
        return "No active watches. Add one with /watch PAIR STYLE MODE"
    lines = ["<b>Active watch list</b>"]
    for w in rows:
        lines.append(f"\u2022 {w['pair']} \u2014 {w['style']}/{w['mode']} "
                      f"(last alert {('yes' if w['last_signal_ts'] else 'no')})")
    return "\n".join(lines)


def dashboard_view(watches, pilot, data_mode):
    lines = ["\U0001f4cb <b>EzyAi dashboard</b>"]
    if watches:
        shown = ", ".join(f"{w['pair']} ({w['style']}/{w['mode']})" for w in watches[:4])
        extra = f" +{len(watches) - 4} more" if len(watches) > 4 else ""
        lines.append(f"\U0001f440 Watching ({len(watches)}): {shown}{extra}")
    else:
        lines.append("\U0001f440 Watching (0): tap Watchlist to add your first alert.")
    if pilot is not None:
        lines.append(f"\U0001f916 Autopilot: <b>ON</b> \u00b7 {pilot.style}/{pilot.mode}")
    else:
        lines.append("\U0001f916 Autopilot: off")
    lines.append(f"Feed: {data_mode}")
    lines.append("Use the buttons below \U0001f447 \u2014 everything is one tap away.")
    return "\n".join(lines)


def confirm_watch_text(pair, style, mode, check_s, rr, risk_pct):
    return (
        f"\U0001f514 Confirm watch\n<b>{pair}</b> \u00b7 {style}/{mode}\n"
        f"Checks every {check_s}s \u00b7 target RR {rr} \u00b7 risk {risk_pct:.1f}%/trade."
    )


def confirm_auto_text(style, mode, daily_limit):
    return (
        f"\U0001f916 Confirm autopilot\n{style}/{mode} \u00b7 random pairs \u00b7 "
        f"up to {daily_limit} signals/day.\n"
        "You can stop it anytime from the dashboard."
    )


def watch_added_text(pair, style, mode):
    return (f"\u2705 Watch live: <b>{pair}</b> \u00b7 {style}/{mode}\n"
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
        "<b>EzyAi commands</b>\n\n"
        "/analyze \u2014 on-demand market analysis with entry/exit, level-based flow\n"
        "/watch PAIR STYLE MODE \u2014 live alerts for a pair (crypto, forex, stock, cfd)\n"
        "    STYLE: scalping | intraday | swing\n"
        "    MODE:  safe | normal | aggressive\n"
        "/watches \u2014 list your active watches\n"
        "/unwatch PAIR \u2014 stop alerts for a pair\n"
        "/fundamentals PAIR \u2014 fundamentals + links for a pair\n"
        "/autopilot STYLE MODE \u2014 random-pair auto signals (2 settings only)\n"
        "/stopautopilot \u2014 stop random signals\n"
        "/quote PAIR \u2014 quick live price\n"
        "/help \u2014 this message\n\n"
        "Examples: crypto BTCUSD \u00b7 forex EURUSD \u00b7 stock AAPL \u00b7 cfd XAUUSD\n"
        "Modes affect frequency and risk: safe (fewer, tighter), aggressive (more, wider)."
    )
"""Fundamental scoring (all free, no-key inputs).

Stock 0-100 score (faizancodes concept: valuation / profitability / growth /
health / momentum pillars) plus a simplified DCF intrinsic value with margin
of safety (hjones20 concept, assumptions always disclosed). Crypto momentum /
liquidity gauge off CoinGecko market data. FX macro verdict off rate
differentials + trend agreement (forex-skill concept).
"""
import calendar
import datetime

from .. import constants as _c

GRADES = ((85, "A+"), (75, "A"), (60, "B"), (45, "C"), (30, "D"), (0, "F"))


def grade(score):
    for cut, letter in GRADES:
        if score >= cut:
            return letter
    return "F"


def _band(value, breaks, default=0.0):
    """breaks: [(min_value, points)] checked high-to-low."""
    if value is None:
        return default
    for floor, pts in breaks:
        if value >= floor:
            return pts
    return 0.0


def stock_score(m, price, chg_3m=None, pos_52w=None):
    """m: edgar.statement_metrics() dict. Returns score dict (0-100)."""
    notes = []
    eps = m.get("eps")
    pe = (price / eps) if (eps and eps > 0 and price) else None
    valuation = _valuation(pe)
    profitability = (_band(m.get("roe"), [(0.20, 15), (0.10, 10), (0, 5)])
                     + _band(m.get("net_margin"), [(0.15, 10), (0.05, 6), (0, 3)]))
    growth = (_band(m.get("rev_cagr_3y"), [(0.15, 10), (0.05, 7), (0, 4)])
              + _band(m.get("eps_cagr_3y"), [(0.15, 10), (0.05, 7), (0, 4)]))
    health = _de_score(m.get("de_ratio"))
    health += _band(m.get("current_ratio"), [(1.5, 7), (1.0, 4), (0, 1)],
                    default=3.0)
    momentum = (_band(pos_52w, [(0.8, 8), (0.5, 5), (0.2, 3), (0, 1)], default=0.0)
                + _band(chg_3m, [(15, 7), (0, 5), (-15, 2)], default=0.0))
    if pe is None:
        notes.append("no positive FY earnings: valuation unscored")
    if m.get("rev_cagr_3y") is None:
        notes.append("short statement history: growth understated")
    total = round(valuation + profitability + growth + health + momentum, 1)
    return {
        "score": total, "grade": grade(total),
        "pillars": {
            "valuation": valuation, "profitability": profitability,
            "growth": growth, "health": health, "momentum": momentum,
        },
        "pe": pe, "notes": notes,
    }


def _valuation(pe):
    if pe is None or pe <= 0:
        return 0.0
    if pe < 15:
        return 25.0
    if pe < 20:
        return 20.0
    if pe < 25:
        return 15.0
    if pe < 35:
        return 10.0
    return 5.0


def _de_score(de):
    if de is None:
        return 3.0
    if de <= 0.5:
        return 8.0
    if de <= 1.5:
        return 5.0
    return 2.0


def dcf_intrinsic(fcf, growth, shares, net_debt=0.0,
                  discount=0.09, years=5, terminal=0.025):
    """Simplified DCF. growth clamped to [0, 15%]. Returns dict or None."""
    if not fcf or fcf <= 0 or not shares or shares <= 0:
        return None
    g = min(max(growth if growth is not None else 0.05, 0.0), 0.15)
    pv = sum(fcf * (1 + g) ** t / (1 + discount) ** t
             for t in range(1, years + 1))
    fcf_n = fcf * (1 + g) ** years
    tv = fcf_n * (1 + terminal) / (discount - terminal)
    ev = pv + tv / (1 + discount) ** years
    intrinsic = (ev - (net_debt or 0.0)) / shares
    return {
        "intrinsic": intrinsic, "growth_used": g, "discount": discount,
        "terminal": terminal, "years": years,
        "assumptions": (f"FCF ${fcf/1e9:.1f}B growing {g*100:.0f}% for {years}y, "
                        f"{terminal*100:.1f}% terminal, {discount*100:.0f}% discount"),
    }


def dcf_verdict(dcf, price):
    if not dcf or not price:
        return None
    iv = dcf["intrinsic"]
    if iv <= 0:
        return None
    mos = (iv - price) / iv * 100.0
    label = "undervalued" if mos >= 20 else ("fair value" if mos >= -20 else "overvalued")
    return {"intrinsic": iv, "mos_pct": mos, "label": label}


def piotroski(series):
    """Piotroski F-score from annual statement series.

    series: {key: {fy: value}} with keys revenue, net_income, assets,
    ocf, debt, equity, assets_current, liab_current, shares,
    gross_profit. Each of the 9 binary criteria needs current + prior
    FY; missing inputs abstain and shrink the denominator, reported
    as "score/total".
    """
    def last2(key):
        s = series.get(key, {})
        fys = sorted(s)
        if len(fys) < 2:
            return None, None
        return s[fys[-1]], s[fys[-2]]

    # align every criterion on FYs shared by ALL inputs (taxonomy drift
    # can leave different tags covering different year ranges)
    common = None
    for key in ("net_income", "assets", "ocf", "revenue", "debt", "equity",
                "assets_current", "liab_current", "shares", "gross_profit"):
        fys = set(series.get(key, {}))
        common = fys if common is None else (common & fys)
    aligned = {}
    if common and len(common) >= 2:
        top = sorted(common)[-2:]
        for key in ("net_income", "assets", "ocf", "revenue", "debt",
                    "equity", "assets_current", "liab_current", "shares",
                    "gross_profit"):
            s = series.get(key, {})
            aligned[key] = (s[top[1]], s[top[0]])
    else:
        aligned = {key: last2(key) for key in (
            "net_income", "assets", "ocf", "revenue", "debt", "equity",
            "assets_current", "liab_current", "shares", "gross_profit")}

    checks = []
    ni, ni_p = aligned["net_income"]
    assets, assets_p = aligned["assets"]
    ocf, _ = aligned["ocf"]
    rev, rev_p = aligned["revenue"]
    debt, debt_p = aligned["debt"]
    eq, eq_p = aligned["equity"]
    ca, ca_p = aligned["assets_current"]
    cl, cl_p = aligned["liab_current"]
    sh, sh_p = aligned["shares"]
    gp, gp_p = aligned["gross_profit"]

    def roa(ni_v, a_v):
        return (ni_v / a_v) if (ni_v is not None and a_v) else None

    r, r_p = roa(ni, assets), roa(ni_p, assets_p)
    if r is not None:
        checks.append(("ROA positive", r > 0))
    if ocf is not None:
        checks.append(("Cash flow positive", ocf > 0))
    if r is not None and r_p is not None:
        checks.append(("ROA improving", r > r_p))
    if ocf is not None and ni is not None:
        checks.append(("Cash-backed earnings", ocf > ni))

    def lev(debt_v, eq_v):
        tot = (debt_v or 0.0) + (eq_v or 0.0)
        return ((debt_v or 0.0) / tot) if tot > 0 else None

    l, l_p = lev(debt, eq), lev(debt_p, eq_p)
    if l is not None and l_p is not None:
        checks.append(("Leverage falling", l < l_p))

    def cr(ca_v, cl_v):
        return (ca_v / cl_v) if (ca_v is not None and cl_v) else None

    c, c_p = cr(ca, cl), cr(ca_p, cl_p)
    if c is not None and c_p is not None:
        checks.append(("Liquidity improving", c > c_p))
    if sh is not None and sh_p is not None and sh_p > 0:
        checks.append(("No dilution", sh <= sh_p))

    def gm(gp_v, rev_v):
        return (gp_v / rev_v) if (gp_v is not None and rev_v) else None

    g, g_p = gm(gp, rev), gm(gp_p, rev_p)
    if g is not None and g_p is not None:
        checks.append(("Margins expanding", g > g_p))

    def ato(rev_v, a_v):
        return (rev_v / a_v) if (rev_v is not None and a_v) else None

    a, a_p = ato(rev, assets), ato(rev_p, assets_p)
    if a is not None and a_p is not None:
        checks.append(("Turnover improving", a > a_p))

    score = sum(1 for _, ok in checks if ok)
    return {"score": score, "total": len(checks),
            "passed": [name for name, ok in checks if ok],
            "failed": [name for name, ok in checks if not ok]}


def earnings_quality(fcf, net_income):
    """FCF-vs-profit cash conversion verdict. Worded to never collide
    with the Piotroski 'Cash-backed earnings' (OCF-vs-NI) criterion."""
    if fcf is None or net_income is None:
        return None
    if net_income <= 0:
        return ("loss-making: cash burn" if fcf < 0
                else "loss-making on paper, cash-positive")
    if fcf < 0:
        return "negative cash conversion"
    if fcf / net_income >= 0.8:
        return "strong cash conversion"
    return "partial cash conversion"


def crypto_score(chg_7d=None, chg_30d=None, chg_1y=None,
                 ath_pct=None, volume_mcap=None, rank=None):
    """Momentum/liquidity gauge (no statements in crypto). 0-100."""
    signs = [c for c in (chg_7d, chg_30d, chg_1y) if c is not None]
    pos = sum(1 for c in signs if c > 0)
    trend = {3: 40.0, 2: 28.0, 1: 14.0, 0: 5.0}.get(pos, 0.0) if signs else 0.0
    drawdown = _band(ath_pct, [(-10, 30), (-25, 22), (-50, 14)], default=0.0) \
        if ath_pct is not None else 0.0
    if ath_pct is not None and ath_pct >= 0:
        drawdown = 30.0
    liquidity = _band(volume_mcap, [(0.10, 15), (0.03, 10), (0.01, 6), (0, 3)],
                      default=0.0)
    scale = _band(-(rank or 10 ** 9),
                  [(-10, 15), (-50, 11), (-200, 7), (-10 ** 9, 4)], default=0.0) \
        if rank else 0.0
    total = round(trend + drawdown + liquidity + scale, 1)
    return {
        "score": total, "grade": grade(total),
        "pillars": {"trend": trend, "drawdown_posture": drawdown,
                    "liquidity": liquidity, "scale": scale},
    }


def fx_verdict(pair, chg_1w=None, chg_1m=None, chg_3m=None, vol_pct=None,
               rates=None, asof=None):
    rates = rates or _c.POLICY_RATES
    s = (pair or "").upper()
    base, quote = (s[:3], s[3:6]) if len(s) == 6 else (None, None)
    rb = rates.get(base, (None, "?"))[0] if base else None
    rq = rates.get(quote, (None, "?"))[0] if quote else None
    carry_bp = (rb - rq) * 100 if rb is not None and rq is not None else None

    def sign(x):
        return 1 if (x or 0) > 0.5 else (-1 if (x or 0) < -0.5 else 0)

    t_short = sign(chg_1w)
    med = [c for c in (chg_1m, chg_3m) if c is not None]
    t_med = sign(sum(med) / len(med)) if med else 0
    agree = (t_short == t_med) and t_short != 0
    if vol_pct is None:
        risk = "medium"
    elif vol_pct > 25:
        risk = "high"
    elif vol_pct > 12:
        risk = "medium"
    else:
        risk = "low"
    if not agree and risk == "low":
        risk = "medium"
    direction = ("bullish " + base) if (agree and t_short > 0) else \
                ("bearish " + base if (agree and t_short < 0) else "mixed")
    return {
        "base": base, "quote": quote, "carry_bp": carry_bp,
        "rates_asof": asof or _c.POLICY_RATES_ASOF,
        "trend_short": t_short, "trend_med": t_med, "agree": agree,
        "direction": direction, "risk": risk,
    }


def upcoming_events(kind, pair=None, today=None):
    """Dated, always-computable macro events (no calendar feed needed).

    - US Nonfarm Payrolls: first Friday of the month (moves USD pairs,
      gold, US stocks/indices, oil).
    - CFTC COT release: Fridays for positioning followers (gold/oil).
    Returns [(date_str, label)] with date_str like "Fri 12 Sep".
    """
    today = today or datetime.date.today()
    events = []
    nfp = _next_nfp(today)
    if nfp:
        events.append((nfp.strftime("%a %d %b"), "US Nonfarm Payrolls"))
    s = (pair or "").upper()
    wants_cot = kind in ("cfd",) or s in ("XAUUSD", "XAGUSD", "WTI", "UKOIL")
    if wants_cot:
        events.append((_next_weekday(today, 4).strftime("%a %d %b"),
                       "CFTC positioning (COT)"))
    return events


def _next_nfp(today):
    y, m = today.year, today.month
    for _ in range(3):
        first_friday = _first_weekday(y, m, 4)
        if first_friday >= today:
            return first_friday
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def _first_weekday(year, month, weekday):
    first = datetime.date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=shift)


def _next_weekday(today, weekday):
    shift = (weekday - today.weekday()) % 7
    return today + datetime.timedelta(days=shift)

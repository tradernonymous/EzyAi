"""Fundamental scoring (all free, no-key inputs).

Stock 0-100 score (faizancodes concept: valuation / profitability / growth /
health / momentum pillars) plus a simplified DCF intrinsic value with margin
of safety (hjones20 concept, assumptions always disclosed). Crypto momentum /
liquidity gauge off CoinGecko market data. FX macro verdict off rate
differentials + trend agreement (forex-skill concept).
"""

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
    """Macro verdict: carry bias + trend agreement + risk rating."""
    from .. import constants as _c  # local import: scoring stays import-light
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

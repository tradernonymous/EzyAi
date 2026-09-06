"""SEC EDGAR company-facts client (free, no API key).

Builds annual statement series from 10-K filings for scoring and DCF:
revenue, net income, equity, debt, cash, operating cash flow, capex,
shares outstanding, diluted EPS. Pure parsing + math, fully unit-testable.
"""

EDGAR_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# tag -> candidate us-gaap concepts, first hit wins
TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liab_current": ["LiabilitiesCurrent"],
    "debt_lt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "debt_current": ["DebtCurrent", "ShortTermBorrowings"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "shares": ["CommonStockSharesOutstanding"],
    "eps": ["EarningsPerShareDiluted"],
    "op_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}

MONEY_UNITS = ("USD",)
SHARE_UNITS = ("shares", "USD/shares")


def annual_series(facts, tag, unit="USD", forms=("10-K", "10-K/A")):
    """{fy_year: value} from annual filings, latest filing wins per FY."""
    concepts = facts.get("facts", {}).get("us-gaap", {})
    if tag not in concepts:
        return {}
    entries = concepts[tag].get("units", {}).get(unit, [])
    best = {}
    for e in entries:
        if e.get("form") not in forms:
            continue
        try:
            fy = int(e.get("fy"))
            val = float(e.get("val"))
        except (TypeError, ValueError):
            continue
        filed = str(e.get("filed", ""))
        prev = best.get(fy)
        if prev is None or filed >= prev[1]:
            best[fy] = (val, filed)
    return {fy: val for fy, (val, _) in sorted(best.items())}


def pick_series(facts, candidates, unit="USD"):
    best = (None, {})
    for tag in candidates:
        s = annual_series(facts, tag, unit)
        if not s:
            continue
        # taxonomy drift: same line item retagged across years, so prefer
        # the tag with the most recent FY, then the most data points
        key = (max(s), len(s))
        if best[0] is None or key > (max(best[1]), len(best[1])):
            best = (tag, s)
    return best


def cagr(values):
    """Compound annual growth over a series of annual values (oldest->newest)."""
    if len(values) < 2:
        return None
    first, last = values[0], values[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    n = len(values) - 1
    try:
        return (last / first) ** (1.0 / n) - 1.0
    except (ZeroDivisionError, ValueError):
        return None


def statement_metrics(facts):
    """Extract latest values + multi-year growth/ratios. Missing -> None."""
    out = {"entity": facts.get("entityName")}
    series = {}
    for key, tags in TAGS.items():
        unit = "shares" if key == "shares" else (
            "USD/shares" if key == "eps" else "USD")
        tag, s = pick_series(facts, tags, unit)
        if not s and key in ("shares", "eps"):
            tag, s = pick_series(facts, tags, "USD")
        series[key] = s
        out[key + "_tag"] = tag
    # combined debt series (long-term + current) for leverage checks
    lt, cur = series.get("debt_lt", {}), series.get("debt_current", {})
    debt = {fy: lt.get(fy, 0.0) + cur.get(fy, 0.0)
            for fy in set(lt) | set(cur)}
    series["debt"] = dict(sorted(debt.items()))
    out["series"] = series

    def last(key, years_back=0):
        s = series.get(key, {})
        if not s:
            return None
        fys = sorted(s)
        if years_back >= len(fys):
            return None
        return s[fys[-1 - years_back]]

    def window(key, n=4):
        s = series.get(key, {})
        fys = sorted(s)
        return [s[fy] for fy in fys[-n:]] if len(fys) >= 2 else []

    rev, ni, eq = last("revenue"), last("net_income"), last("equity")
    ocf, capex = last("ocf"), last("capex")
    fcf = (ocf - capex) if ocf is not None and capex is not None else None
    debt = (last("debt_lt") or 0.0) + (last("debt_current") or 0.0)
    cash = last("cash") or 0.0
    ca, cl = last("assets_current"), last("liab_current")
    shares, eps = last("shares"), last("eps")
    opinc = last("op_income")

    out.update({
        "fy": max(series.get("revenue", {0: 0}), default=0),
        "revenue": rev, "net_income": ni, "equity": eq,
        "fcf": fcf, "ocf": ocf, "debt": debt, "cash": cash,
        "shares": shares, "eps": eps,
        "rev_cagr_3y": cagr(window("revenue", 4)),
        "eps_cagr_3y": cagr([v for v in window("eps", 4)]),
        "ocf_cagr_3y": cagr([v for v in window("ocf", 4)]),
        "net_margin": (ni / rev) if (ni is not None and rev) else None,
        "op_margin": (opinc / rev) if (opinc is not None and rev) else None,
        "roe": (ni / eq) if (ni is not None and eq) else None,
        "de_ratio": (debt / eq) if (debt is not None and eq) else None,
        "current_ratio": (ca / cl) if (ca is not None and cl) else None,
        "net_debt": (debt or 0.0) - (cash or 0.0),
    })
    return out

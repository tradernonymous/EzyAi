"""Market-context adjustments: volatility regime + trading sessions.

All adjustments are small, bounded, multiplicative haircuts/boosts that are
always disclosed via the reasons list. The same helpers run live in
strategy.analyze() and in scripts/backtest.py (single source of truth).
"""
from datetime import datetime, timedelta, timezone

VOL_CHAOS_RATIO = 2.0    # current 20-bar vol >= 2x recent median -> trim
VOL_DEAD_RATIO = 0.5     # current 20-bar vol <= 0.5x recent median -> trim
VOL_CHAOS_MULT = 0.90
VOL_DEAD_MULT = 0.95
SESSION_THIN_MULT = 0.95
SESSION_CLOSED_MULT = 0.90


def vol_ratio(roll, t, window=20, priors=4):
    """Current realized vol vs median of prior non-overlapping windows.

    roll is a realized-vol array as returned by indicators.realized_vol.
    Returns None when there is not enough history (harness and live agree).
    """
    if t < 0 or t >= len(roll):
        return None
    cur = roll[t]
    if cur is None:
        return None
    past = []
    for k in range(1, priors + 1):
        j = t - window * k
        if j < 0:
            break
        if roll[j] is not None:
            past.append(roll[j])
    if not past:
        return None
    med = sorted(past)[len(past) // 2]
    if med <= 0:
        return None
    return cur / med


def apply_vol_regime(confidence, ratio, reasons):
    if ratio is None:
        return confidence
    if ratio >= VOL_CHAOS_RATIO:
        reasons.append(
            f"High-volatility regime (vol x{ratio:.1f}): confidence trimmed")
        return confidence * VOL_CHAOS_MULT
    if ratio <= VOL_DEAD_RATIO:
        reasons.append(
            f"Dead market (vol x{ratio:.1f}): confidence trimmed")
        return confidence * VOL_DEAD_MULT
    return confidence


def session_state(kind, ts_ms):
    """Return 'closed', 'thin' or 'open' for a bar timestamp (ms epoch, UTC)."""
    if kind in (None, "crypto"):
        return "open"
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if dt.weekday() >= 5:
        return "closed"  # stocks/FX/CFD venues shut or stale on weekends
    if kind == "stock":
        # US cash session 09:30-16:05 New York time; the fixed UTC window
        # used before was an hour off for the five months of standard time.
        try:
            from zoneinfo import ZoneInfo
            ny = dt.astimezone(ZoneInfo("America/New_York"))
        except Exception:  # no tz database: fall back to summer-time UTC
            mins = dt.hour * 60 + dt.minute
            return "open" if 13 * 60 + 30 <= mins <= 20 * 60 + 5 else "thin"
        mins = ny.hour * 60 + ny.minute
        if not (9 * 60 + 30 <= mins <= 16 * 60 + 5):
            return "thin"  # outside US cash session
        return "open"
    if kind in ("forex", "cfd"):
        if dt.hour < 7:
            return "thin"  # Asian hours, low liquidity
        return "open"
    return "open"


def apply_session(confidence, kind, ts_ms, reasons):
    state = session_state(kind, ts_ms)
    if state == "closed":
        reasons.append("Weekend market (thin/stale quotes): confidence trimmed")
        return confidence * SESSION_CLOSED_MULT
    if state == "thin":
        label = "Outside US cash session" if kind == "stock" else "Thin Asian session"
        reasons.append(f"{label}: confidence trimmed")
        return confidence * SESSION_THIN_MULT
    return confidence


def scalp_session(pair, now=None):
    """Scalping liquidity window for a non-crypto pair (Phase 3A).

    Returns the window config plus whether 'now' (UTC epoch seconds, default
    wall clock) is inside a window / the preferred NY overlap, or None when
    the pair has no scalping window (crypto is 24/7, stocks never scalp).

    A fresh feed outside this window is not a data problem, it is the wrong
    time of day: scalping there pays wide, illiquid spreads that no stop
    widening can fix, so it is gated, not logged-and-forgiven.
    """
    from .. import constants as c
    cls = c.scalp_class(pair)
    cfg = c.SCALP_SESSIONS.get(cls)
    if cfg is None:
        return None
    now = now or _now()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    mins = dt.hour * 60 + dt.minute
    in_window = any(a <= mins < b for a, b in cfg["windows"])
    preferred = any(a <= mins < b for a, b in cfg.get("preferred", ()))
    return {"class": cls, "in_window": in_window, "preferred": preferred,
            "windows": cfg["windows"], "label": cfg["label"]}


def next_session_open(pair, now=None):
    """UTC epoch seconds when the pair's next scalping window opens, or None
    when the pair has no window. now defaults to wall clock."""
    from .. import constants as c
    cls = c.scalp_class(pair)
    cfg = c.SCALP_SESSIONS.get(cls)
    if cfg is None:
        return None
    now = now or _now()
    day = datetime.fromtimestamp(now, tz=timezone.utc)
    for offset in range(7):
        cand = (day + timedelta(days=offset))
        if cand.weekday() >= 5:
            continue  # FX/metals never open Sat/Sun; T+1 is window-shaped, not live
        for start_min in sorted({a for a, _b in cfg["windows"]}):
            when = datetime(cand.year, cand.month, cand.day,
                            start_min // 60, start_min % 60,
                            tzinfo=timezone.utc)
            if when.timestamp() > now:
                return when.timestamp()
    return None


def fmt_next_open(epoch_s):
    """Human date for a next-window open (chat copy), or None."""
    if not epoch_s:
        return None
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
    return f"{dt.strftime('%A')} {dt.strftime('%H:%M')} UTC"


def block_kind(pair, style, tf, now=None):
    """Classify a scalp-timeframe freshness failure as 'session' (window
    closed -> this is schedule, not illness), 'closed' (inside the window but
    quotes halted: weekend/holiday/halt) or 'stale' (data too old regardless
    of schedule). Non-scalping styles always classify 'stale'."""
    if style != "scalping" or not tf:
        return "stale"
    win = scalp_session(pair, now=now)
    if win is None:
        return "stale"
    if not win["in_window"]:
        return "session"
    return "closed"


def _now():
    import time
    return time.time()

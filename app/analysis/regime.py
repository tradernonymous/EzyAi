"""Market-context adjustments: volatility regime + trading sessions.

All adjustments are small, bounded, multiplicative haircuts/boosts that are
always disclosed via the reasons list. The same helpers run live in
strategy.analyze() and in scripts/backtest.py (single source of truth).
"""
from datetime import datetime, timezone

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


def _new_york(dt):
    """dt in New York time, or None when no tz database is available."""
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None


def _state_for_dt(kind, dt):
    """'closed', 'thin' or 'open' for an aware UTC datetime."""
    if kind in (None, "crypto"):
        return "open"  # crypto trades every hour of every day
    ny = _new_york(dt)
    if kind == "stock":
        if dt.weekday() >= 5:
            return "closed"
        if ny is None:  # no tz database: fall back to summer-time UTC
            mins = dt.hour * 60 + dt.minute
            return "open" if 13 * 60 + 30 <= mins <= 20 * 60 + 5 else "thin"
        mins = ny.hour * 60 + ny.minute
        if not (9 * 60 + 30 <= mins <= 16 * 60 + 5):
            return "thin"  # outside US cash session
        return "open"
    if kind in ("forex", "cfd"):
        # The FX week runs Sunday 17:00 to Friday 17:00 New York, so Sunday
        # evening is open and Friday evening is not. Treating the whole
        # weekend as shut used to hide a third of the tradeable Sunday.
        if ny is None:
            if dt.weekday() >= 5:
                return "closed"
        else:
            wd, hour = ny.weekday(), ny.hour
            if wd == 5:  # Saturday
                return "closed"
            if wd == 6 and hour < 17:  # Sunday before the open
                return "closed"
            if wd == 4 and hour >= 17:  # Friday after the close
                return "closed"
        if dt.hour < 7:
            return "thin"  # Asian hours, low liquidity
        return "open"
    return "open"


def session_state(kind, ts_ms):
    """Return 'closed', 'thin' or 'open' for a bar timestamp (ms epoch, UTC)."""
    return _state_for_dt(kind, datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc))


def venue_open_now(kind, now=None):
    """True when this venue can print a fresh bar right now.

    Stocks outside the cash session count as shut: the feed is fetched with
    includePrePost=false, so no new bars exist to analyse. Thin FX hours
    still trade, so they stay open.
    """
    dt = now or datetime.now(timezone.utc)
    state = _state_for_dt(kind, dt)
    if state == "closed":
        return False
    return not (kind == "stock" and state == "thin")


def bar_age_s(ts_ms, now=None):
    """Seconds since the last bar opened."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    return max(0.0, now - ts_ms / 1000.0)


def is_stale(ts_ms, interval, now=None):
    """True when the newest bar is too old for its timeframe to be traded."""
    from .. import constants
    limit = constants.MAX_BAR_AGE_S.get(interval, constants.DEFAULT_MAX_BAR_AGE_S)
    return bar_age_s(ts_ms, now) > limit


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

"""Autopilot signals mirrored onto the website's live board.

The EzyMap site (printezy.money) renders a "Live signals" board and a
performance page from rows it is told about; nothing here reads back. One
endpoint covers a trade's whole life:

  POST {site}/api/public/ezyai/signals   {external_id, ...}
  GET  {site}/api/public/ezyai/signals?diagnose=1   (no auth, no secrets)

Every field except `external_id` is optional and an omitted field keeps the
value the site already holds, so open / tick / close are the same call with
different fields. The site merges on `external_id`, which makes every push
idempotent: a retry after a crash updates the card it already made instead
of adding a second one. `external_id` is the outcome store's row id (see
`external_id_for`), the id this bot already uses for the trade everywhere
else.

Standard library only: the board must add no dependency to the bot.

The shop window must never be able to take the bot down with it, so the
`publish_*` helpers hand the request to a daemon thread and return at once
-- the Telegram delivery path and the resolver never wait on the website,
and a failed push logs and dies there. Empty EZYAI_SIGNAL_KEY disables the
bridge after one log line.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import config
from . import constants

logger = logging.getLogger(__name__)

PATH = "/api/public/ezyai/signals"
TIMEOUT_S = 8
# 4xx fails fast (a bad key never improves by asking again); 5xx and 429
# get these two retries with a 2 s / 4 s backoff.
RETRIES = 3
BATCH_MAX = 50

# The site rejects anything outside these, so they are caught here instead.
STATUSES = ("pending", "running", "tp", "be", "sl", "cancelled")
DIRECTIONS = ("buy", "sell")
SIDE_DIRECTION = {"long": "buy", "short": "sell"}

# Outcome-store status -> board status. 'expired' is a time-based exit, not
# a target or a stop: it closes as a flat card carrying its real R, so the
# site's performance numbers still count it (a 'cancelled' card is dropped
# from the record entirely, which would quietly flatter the track record).
CLOSE_STATUS = {"tp1": "tp", "tp2": "tp", "sl": "sl", "expired": "be"}

_no_key_logged = False


def endpoint():
    return config.signal_site_url().rstrip("/") + PATH


def enabled():
    """False (and one log line, once) until EZYAI_SIGNAL_KEY is set."""
    global _no_key_logged
    if config.signal_key().strip():
        return True
    if not _no_key_logged:
        _no_key_logged = True
        logger.info("website signal board off: EZYAI_SIGNAL_KEY is not set")
    return False


def external_id_for(row_id):
    """Stable board id for an outcome-store row -- the bot's own trade id."""
    return f"ezyai-{int(row_id)}"


def _check(signal):
    """The three things the site rejects outright. None when it looks sound."""
    if not signal.get("external_id"):
        return "external_id is required"
    status = signal.get("status")
    if status is not None and status not in STATUSES:
        return f"status must be one of {', '.join(STATUSES)}"
    direction = signal.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        return f"direction must be one of {', '.join(DIRECTIONS)}"
    return None


def push(**fields):
    """Send one signal payload, or a batch when called with signals=[...].

    Returns {"ok": bool, ...} and never raises: an unreachable or broken
    website is logged and dropped, never propagated to the caller.
    """
    if not enabled():
        return {"ok": False, "error": "no key configured"}
    batch = fields.get("signals")
    rows = batch if isinstance(batch, list) else [fields]
    for row in rows:
        problem = _check(row)
        if problem:
            logger.warning("signal push rejected locally: %s", problem)
            return {"ok": False, "error": problem}
    # None means "nothing new to say about this field"; the site would read
    # a null as "clear the column", so unset fields are dropped instead.
    body = json.dumps({k: v for k, v in fields.items()
                       if v is not None}).encode()
    ext = fields.get("external_id") or "batch"
    last_error = "unknown"
    for attempt in range(1, RETRIES + 1):
        req = urllib.request.Request(
            endpoint(), data=body, method="POST",
            headers={"Authorization": f"Bearer {config.signal_key()}",
                     "Content-Type": "application/json",
                     "User-Agent": "EzyAi-autopilot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                payload = json.loads(resp.read() or b"{}")
                if not isinstance(payload, dict):
                    payload = {}
                logger.info("signal push %s -> %s", ext, resp.status)
                return dict(payload, ok=True, status=resp.status)
        except urllib.error.HTTPError as exc:
            detail = (exc.read() or b"").decode(errors="replace")[:200]
            last_error = f"{exc.code}: {detail}"
            if exc.code < 500 and exc.code != 429:
                logger.warning("signal push rejected %s: %s", ext, last_error)
                if exc.code == 401:
                    # A bare 401 is a shrug: the diagnose body names the
                    # variable the site compared against and fingerprints
                    # both sides, which separates "wrong key" from "the key
                    # never reached the deployed site".
                    logger.warning("key check says: %s", diagnose())
                return {"ok": False, "status": exc.code, "error": last_error}
        except Exception as exc:  # timeout, DNS, TLS, malformed body
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES:
            time.sleep(2 ** attempt)
    logger.warning("signal push failed %s after %d tries: %s", ext, RETRIES,
                   last_error)
    return {"ok": False, "error": last_error}


def open_signal(external_id, symbol, direction, status="pending",
                entry_low=None, entry_high=None, stop_price=None, tp1=None,
                tp2=None, rr=None, setup_score=None, setup=None,
                timeframe=None, note=None, opened_at=None):
    """Put a new card on the board ('running' when it filled immediately)."""
    return push(external_id=external_id, symbol=symbol, direction=direction,
                status=status, entry_low=entry_low, entry_high=entry_high,
                stop_price=stop_price, tp1=tp1, tp2=tp2, rr=rr,
                setup_score=setup_score, setup=setup, timeframe=timeframe,
                note=note, opened_at=opened_at)


def tick(external_id, last_price, status=None):
    """Move the card's progress rail between its stop and its target."""
    return push(external_id=external_id, last_price=last_price, status=status)


def close_signal(external_id, status, result_r=None, result_pips=None,
                 last_price=None, closed_at=None):
    """Take the card off the live board and into the track record.

    `status` is 'tp', 'be' or 'sl'; `result_r` (+rr at target, -1 at stop,
    0 flat) is what every performance figure on the site is built from. The
    site stamps closed_at itself when it is left out."""
    return push(external_id=external_id, status=status, result_r=result_r,
                result_pips=result_pips, last_price=last_price,
                closed_at=closed_at)


def push_many(signals):
    """Up to 50 at once, for a reconcile sweep after downtime. Answers 207
    when some landed and some did not -- read `results` per signal rather
    than assuming all-or-nothing."""
    if not signals:
        return {"ok": True, "accepted": 0, "results": []}
    if len(signals) > BATCH_MAX:
        return {"ok": False, "error": f"at most {BATCH_MAX} signals per request"}
    return push(signals=signals)


def diagnose():
    """Ask the site which key it is actually checking. No auth, no secrets.

    The answer says whether the site has EZYAI_SIGNAL_KEY at all, which
    variable it compared against and a truncated digest of it -- enough to
    tell a wrong secret from a secret that never reached the deployed site,
    which have opposite fixes."""
    req = urllib.request.Request(
        endpoint() + "?diagnose=1", method="GET",
        headers={"User-Agent": "EzyAi-autopilot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read() or b"{}")
            return dict(body if isinstance(body, dict) else {}, ok=True)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _iso(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def _setup_label(signal):
    """The card's one-line label, worded like the Telegram message header."""
    style = constants.STYLE_PROFILE[signal["style"]]["label"]
    mode = constants.MODE_PROFILE[signal["mode"]]["label"]
    return f"{style} \u00b7 {mode} risk \u00b7 {signal['tf']}"


def _dispatch(fn, *args, **kwargs):
    """Run a push on a daemon thread: the website is never on a path that
    must not fail, and its retries never hold up a Telegram send or a
    resolver pass. Returns the thread (tests join it); never raises."""
    try:
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs,
                                  name="signal-push", daemon=True)
        thread.start()
        return thread
    except Exception as exc:
        logger.warning("signal push not dispatched: %s: %s",
                       type(exc).__name__, exc)
        return None


def publish_signal(external_id, signal):
    """Mirror a delivered autopilot signal onto the live board.

    Demo/synthetic prices are never published: the board is a public track
    record and simulated fills must not land in it. Mapping the card runs
    on the caller's thread, so it is guarded too -- this is called from the
    Telegram delivery loop, which nothing here may interrupt."""
    if not enabled():
        return None
    try:
        if signal.get("data_mode", "live") != "live":
            return None
        reasons = signal.get("reasons") or []
        return _dispatch(
            open_signal, external_id, str(signal["pair"]).upper(),
            SIDE_DIRECTION.get(signal["side"], signal["side"]),
            status="running",
            entry_low=signal["entry_zone"][0],
            entry_high=signal["entry_zone"][1],
            stop_price=signal["sl"], tp1=signal["tp1"], tp2=signal["tp2"],
            rr=signal["rr"], setup_score=int(round(signal["confidence"])),
            setup=_setup_label(signal), timeframe=signal["tf"],
            note=(reasons[0] if reasons else None),
            opened_at=_iso(signal["ts"]))
    except Exception as exc:
        logger.warning("signal card not built for %s: %s: %s", external_id,
                       type(exc).__name__, exc)
        return None


def publishable(row):
    """True for outcome-store rows the board carries: autopilot signals on
    live data. `data_source` is the column `record()` fills from the
    signal's data_mode, so 'demo' and shadow captures are excluded here."""
    try:
        return (enabled() and row.get("source") == "autopilot"
                and row.get("data_source") == "live")
    except Exception:
        return False


def publish_tick(row, last_price):
    """Move an open card's rail from the resolver's own price fetch."""
    if not publishable(row):
        return None
    try:
        return _dispatch(tick, external_id_for(row["id"]), float(last_price))
    except Exception as exc:
        logger.warning("signal tick not built for id=%s: %s: %s",
                       row.get("id"), type(exc).__name__, exc)
        return None


def publish_close(row, status, exit_price, r_multiple):
    """Close a card with the outcome the resolver just wrote to the store."""
    if not publishable(row) or status not in CLOSE_STATUS:
        return None
    try:
        return _dispatch(close_signal, external_id_for(row["id"]),
                         CLOSE_STATUS[status], result_r=r_multiple,
                         last_price=exit_price)
    except Exception as exc:
        logger.warning("signal close not built for id=%s: %s: %s",
                       row.get("id"), type(exc).__name__, exc)
        return None

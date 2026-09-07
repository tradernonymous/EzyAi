"""Phase 3D: event calendar that pauses signal emission around high-impact
releases (ForexFactory feed, keep-last-good, fail-open).

Semantics (from the brief, user-confirmed):
  - A "High" impact release opens a blackout window around it. For the whole
    window the scheduler emits nothing: no watch alert, no autopilot signal,
    /analyze is exempt (it is informational).
  - The blackout is GLOBAL — a High-impact USD release blocks an ETHUSD
    signal too. Crypto markets stay open through fiat releases and rip on
    the news: this is exactly the sort of spike that produces a 9-R filler
    gone.
  - The gate runs BEFORE the daily-cap check, so a blackout does not spend a
    user's or the bot's daily allowance, and nothing is recorded as fired.

Fail-open: a calendar outage must never take the bot down, so when we
cannot fetch (or are offline) the blackout test returns False and emission
continues on live data. Keep-last-good: a failed refresh keeps serving the
previous fetch for the same NY day so a single HTTP blip cannot open a
15-minute hole mid-afternoon.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_POLL_S = 600.0
DEFAULT_BUFFER_MIN = 30.0
NY = ZoneInfo("America/New_York")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HTTP_TIMEOUT_S = 10.0


class Calendar:
    def __init__(self, url=None, poll_s=None, buffer_min=None,
                 fetch_json: Optional[Callable] = None):
        self.url = url or DEFAULT_URL
        self.poll_s = float(poll_s or DEFAULT_POLL_S)
        self.buffer = int((buffer_min if buffer_min is not None
                           else DEFAULT_BUFFER_MIN) * 60.0)
        self._fetch = fetch_json or self._default_fetch_json
        self._events: list[dict] = []        # keep-last-good store
        self._fetched_at = 0.0
        self._fetch_failures = 0

    # -- fetch ------------------------------------------------------------
    def _default_fetch_json(self, url):
        import json
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.load(resp)

    def refresh(self, now=None):
        """Fetch the current week's events, keeping the previous data on
        failure. Returns True when the store was updated or already holds
        events for the active NY day."""
        now = now or datetime.now(tz=UTC)
        if time.time() - self._fetched_at < self.poll_s and self._events:
            return True
        try:
            raw = self._fetch(self.url)
            events = [self._normalise(e) for e in raw or []]
            events = [e for e in events if e is not None]
            self._events = events
            self._fetched_at = time.time()
            self._fetch_failures = 0
            return True
        except Exception as exc:
            self._fetch_failures += 1
            if self._events:
                logger.warning("calendar refresh failed, keeping last good "
                               "(%s: %s)", type(exc).__name__, exc)
                return True
            logger.warning("calendar unreachable (%s: %s) - fail-open",
                           type(exc).__name__, exc)
            return False

    @staticmethod
    def _normalise(ev):
        """Feed row -> (utc_start, country, impact) or None if unusable.
        date is ISO with an offset, dthe parser must respect it (the brief
        showed explicit UTC offsets like 2026-09-11T08:30:00-04:00, so we
        never hand-roll DST math)."""
        try:
            s = str(ev.get("date", "")).replace("Z", "+00:00")
            start = datetime.fromisoformat(s)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            impact = str(ev.get("impact") or "").strip().lower()
            country = str(ev.get("country") or "").strip().upper()
            if not impact or not start:
                return None
            return {"start": start.astimezone(UTC).timestamp(),
                    "country": country or "??", "impact": impact}
        except Exception:
            return None

    # -- blackout ---------------------------------------------------------
    def in_blackout(self, now=None):
        """True when `now` (UTC datetime, default wall clock) sits within a
        High-impact window. Never raises: an unreachable feed means False."""
        try:
            self.refresh(now=now)
            now = now or datetime.now(tz=UTC)
            ts = now.timestamp()
            for ev in self._events:
                if ev["impact"] not in ("high",):
                    continue
                if abs(ts - ev["start"]) <= self.buffer:
                    return True
            return False
        except Exception as exc:
            logger.warning("calendar blackout eval failed: %s", exc)
            return False

    # -- test seam --------------------------------------------------------
    def set_events(self, events, fetched_at=None):
        """Inject a parsed event list (test seam). Each entry:
        {"start": epoch_s, "country": "USD", "impact": "high"}. Impact is
        normalised to lowercase (the feed ships "High"; fromisoformat rows
        are lowercased in _normalise too) so a seam can never dodge the gate
        by passing the wrong case."""
        clean = []
        for ev in events:
            rec = dict(ev)
            rec["impact"] = str(rec.get("impact") or "").strip().lower()
            clean.append(rec)
        self._events = clean
        self._fetched_at = fetched_at or time.time()


UTC = timezone.utc
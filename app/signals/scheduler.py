import asyncio
import calendar
import json
import logging
import os
import random
import shutil
import threading
import time
from datetime import datetime, timezone

from .. import constants
from . import engine as signal_engine
from .autopilot import AutoPilot

logger = logging.getLogger(__name__)

STATE_VERSION = 1
# Payment idempotency history. 500 was small enough that an old Stripe
# redelivery could stack a second period once the list rolled over.
PAID_EVENTS_MAX = 20000
# A rolling copy of the last good state file, refreshed at most this often.
BACKUP_INTERVAL_S = 600
# Consecutive analysis failures for one watch before the admin is told.
FEED_ALERT_AFTER = 5
# Repeated alerts of the same kind are throttled to one per this window.
ALERT_THROTTLE_S = 600


class StateError(RuntimeError):
    """The state file could not be written (or must not be, after a bad load)."""


def add_months(ts, months):
    """Calendar-month arithmetic in UTC: 12 months is a year, not 360 days.
    The day of month is clamped to the target month's length."""
    dt = datetime.fromtimestamp(ts, timezone.utc)
    month_index = dt.year * 12 + (dt.month - 1) + int(months)
    year, month = divmod(month_index, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day).timestamp()


class Service:
    def __init__(self, hub, state_path, pro_ids=(), admin_id=None, on_alert=None):
        self.hub = hub
        self.state_path = state_path
        self.watches = {}
        self.autopilots = {}
        self.daily_counters = {}
        self.last_check = {}
        self.plans = {}
        self.paid_events = []
        self._paid_set = set()
        self.users = {}  # lowercased telegram username -> chat_id
        self.pro_ids = {int(i) for i in pro_ids}
        self.admin_id = admin_id
        # Best-effort operator notification: callable(text). Set by main.
        self.on_alert = on_alert
        self._alert_ts = {}
        # Set when the state file was unreadable and no backup could be
        # restored. While set, every save is refused so the broken file is
        # never overwritten with an empty one.
        self.load_error = None
        self._last_backup = 0.0
        self._feed_failures = {}
        # State is mutated from two threads: the asyncio bot loop and the
        # Stripe webhook thread. Every mutate+save runs under this lock so
        # activations and watch edits can never clobber each other.
        self._lock = threading.RLock()
        self._load()

    # -- alerts -----------------------------------------------------------
    def _alert(self, key, text):
        now = time.time()
        if now - self._alert_ts.get(key, 0.0) < ALERT_THROTTLE_S:
            return
        self._alert_ts[key] = now
        cb = self.on_alert
        if cb is None:
            return
        try:
            cb(text)
        except Exception as exc:  # alerts must never take the caller down
            logger.warning("alert callback failed: %s", exc)

    # -- plans ------------------------------------------------------------
    def _plan_rec(self, chat_id):
        key = str(chat_id)
        rec = self.plans.get(key)
        if not rec:
            rec = {"plan": "free", "until": 0.0, "trial_used": False,
                   "nudged": False}
            self.plans[key] = rec
        return rec

    def get_plan(self, chat_id):
        """Effective plan, auto-downgrading expired trial/pro to free."""
        with self._lock:
            rec = self._plan_rec(chat_id)
            if rec["plan"] in ("trial", "pro") and rec.get("until", 0) <= time.time():
                rec["plan"] = "free"
                rec["until"] = 0.0
                rec["nudged"] = False
                self._try_save()
            return rec["plan"]

    def plan_status(self, chat_id):
        with self._lock:
            rec = self._plan_rec(chat_id)
            return {"plan": self.get_plan(chat_id), "until": rec.get("until", 0.0),
                    "trial_used": rec.get("trial_used", False)}

    def is_pro(self, chat_id):
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return False
        if self.admin_id is not None and cid == self.admin_id:
            return True
        if cid in self.pro_ids:
            return True
        return self.get_plan(chat_id) in ("trial", "pro")

    def is_comped(self, chat_id):
        """True when access comes from team lists rather than a plan."""
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return False
        if self.admin_id is not None and cid == self.admin_id:
            return True
        return cid in self.pro_ids

    def start_trial(self, chat_id):
        with self._lock:
            rec = self._plan_rec(chat_id)
            if rec.get("trial_used"):
                return None
            rec["trial_used"] = True
            rec["plan"] = "trial"
            rec["until"] = time.time() + constants.TRIAL_DAYS * 86400
            rec["nudged"] = False
            self._save()
            return rec["until"]

    def activate_pro(self, chat_id, months, event_id=None):
        """Extend PRO by calendar months. event_id makes payment webhooks
        idempotent: a redelivered Stripe/Telegram event returns the existing
        expiry instead of stacking another period. Raises StateError when
        the grant could not be persisted, so callers never confirm a
        purchase that was not saved."""
        months = max(1, min(int(months), constants.MAX_PLAN_MONTHS))
        with self._lock:
            if event_id:
                event_id = str(event_id)
                if event_id in self._paid_set:
                    return self._plan_rec(chat_id).get("until", 0.0)
            now = time.time()
            rec = self._plan_rec(chat_id)
            snapshot = dict(rec)
            # Remaining trial or PRO time is kept, never replaced.
            base = now
            if rec["plan"] in ("trial", "pro"):
                base = max(now, float(rec.get("until", 0.0)))
            rec["plan"] = "pro"
            rec["until"] = add_months(base, months)
            rec["nudged"] = False
            if event_id:
                self.paid_events.append(event_id)
                self._paid_set.add(event_id)
                if len(self.paid_events) > PAID_EVENTS_MAX:
                    dropped = self.paid_events[:-PAID_EVENTS_MAX]
                    del self.paid_events[:-PAID_EVENTS_MAX]
                    self._paid_set.difference_update(dropped)
            try:
                self._save()
            except StateError:
                # Roll back so a retry of the same event (Stripe redelivers
                # on a non-2xx) is not mistaken for a replay.
                rec.clear()
                rec.update(snapshot)
                if event_id and event_id in self._paid_set:
                    self._paid_set.discard(event_id)
                    if self.paid_events and self.paid_events[-1] == event_id:
                        self.paid_events.pop()
                raise
            return rec["until"]

    def already_processed(self, event_id):
        with self._lock:
            return bool(event_id) and str(event_id) in self._paid_set

    # -- users (handle -> chat id, for website purchases) -------------------
    def remember_user(self, chat_id, username):
        """Record the handle seen on an update so website purchases typed
        with that handle can be matched to this chat by the sweep."""
        handle = str(username or "").strip().lstrip("@").lower()
        if not handle:
            return
        with self._lock:
            if self.users.get(handle) == int(chat_id):
                return
            self.users[handle] = int(chat_id)
            self._try_save()

    def chat_for_username(self, username):
        handle = str(username or "").strip().lstrip("@").lower()
        with self._lock:
            return self.users.get(handle)

    def expiry_nudges(self):
        """Chat ids with stored watches/autopilot whose plan is free and
        who haven't been nudged yet (covers expiry + legacy pre-PRO)."""
        with self._lock:
            chats = {w["chat_id"] for w in self.watches.values()}
            chats.update(int(k) for k in self.autopilots)
            out = []
            for chat_id in chats:
                rec = self._plan_rec(chat_id)
                if self.is_pro(chat_id) or rec.get("nudged"):
                    continue
                rec["nudged"] = True
                out.append(int(chat_id))
            if out:
                self._try_save()
            return out

    def forget_chat(self, chat_id):
        """A user blocked the bot or deleted the chat: stop working for them.
        Plan records are kept so a returning payer keeps their entitlement."""
        with self._lock:
            keys = [k for k, w in self.watches.items() if w["chat_id"] == chat_id]
            for k in keys:
                del self.watches[k]
            removed = bool(keys)
            if str(chat_id) in self.autopilots:
                del self.autopilots[str(chat_id)]
                removed = True
            if removed:
                self._try_save()
            return removed

    # -- persistence --------------------------------------------------------
    @property
    def backup_path(self):
        return self.state_path.with_name(self.state_path.name + ".bak")

    def _load(self):
        path = self.state_path
        if not path.exists():
            return
        try:
            self._apply(self._read(path))
            return
        except Exception as exc:
            main_error = f"{type(exc).__name__}: {exc}"
        # The main file is unreadable. Starting with empty state would let
        # the next save wipe every subscription, so quarantine the file and
        # try the rolling backup instead.
        quarantine = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.replace(quarantine)
        except OSError as exc:
            logger.error("could not quarantine %s: %s", path, exc)
        logger.error("state file %s unreadable (%s); moved to %s",
                     path, main_error, quarantine.name)
        bak = self.backup_path
        if bak.exists():
            try:
                self._apply(self._read(bak))
                logger.error("state restored from backup %s", bak.name)
                self._alert("state_restored",
                            f"State file was corrupt ({main_error}). Restored "
                            f"from backup {bak.name}; the bad copy is "
                            f"{quarantine.name}. Changes since the backup "
                            "may be lost — check recent payments.")
                return
            except Exception as exc:
                logger.error("backup %s also unreadable: %s", bak, exc)
        self.load_error = main_error
        logger.critical("no usable state; saves disabled until %s is repaired",
                        path)
        self._alert("state_unusable",
                    f"State file unreadable ({main_error}) and no backup "
                    f"could be loaded. Saves are DISABLED. Repair "
                    f"{quarantine.name} and restart.")

    @staticmethod
    def _read(path):
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            # A zero-length file is what a truncated write leaves behind,
            # never a legitimate empty state (a fresh install has no file).
            raise ValueError("state file is empty")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("state root is not an object")
        return data

    def _apply(self, data):
        watches, autopilots, users, plans = {}, {}, {}, {}
        for w in data.get("watches") or []:
            try:
                key = self._watch_key(w["chat_id"], w["pair"])
                w.setdefault("last_signal_ts", 0.0)
                w.setdefault("last_signal_side", None)
                watches[key] = w
            except (KeyError, TypeError) as exc:
                logger.warning("skipping malformed watch row %r: %s", w, exc)
        for a in data.get("autopilots") or []:
            try:
                autopilots[str(a["chat_id"])] = AutoPilot(
                    self.hub, a["chat_id"], a["style"], a["mode"])
            except (KeyError, TypeError) as exc:
                logger.warning("skipping malformed autopilot row %r: %s", a, exc)
        for k, v in (data.get("users") or {}).items():
            try:
                users[str(k).lower()] = int(v)
            except (TypeError, ValueError):
                continue
        for k, v in (data.get("plans") or {}).items():
            rec = {"plan": "free", "until": 0.0, "trial_used": False,
                   "nudged": False}
            rec.update(v or {})
            plans[str(k)] = rec
        self.watches = watches
        self.autopilots = autopilots
        # Spread the first check of every restored watch across its interval
        # so a restart does not hit every upstream feed at once. Watches
        # added later start with no entry and are checked on the next tick.
        now = time.time()
        for key, w in watches.items():
            interval = constants.STYLE_PROFILE.get(w.get("style"), {}).get(
                "check_interval_s", 300)
            self.last_check[key] = now - random.uniform(0, interval)
        self.users = users
        self.plans = plans
        daily = data.get("daily") or {}
        self.daily_counters = daily if isinstance(daily, dict) else {}
        self.paid_events = [str(x) for x in (data.get("paid_events") or [])][-PAID_EVENTS_MAX:]
        self._paid_set = set(self.paid_events)

    def _payload(self):
        return {
            "version": STATE_VERSION,
            "saved_at": time.time(),
            "watches": list(self.watches.values()),
            "autopilots": [
                {"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                for a in self.autopilots.values()
            ],
            "daily": self.daily_counters,
            "plans": self.plans,
            "paid_events": self.paid_events[-PAID_EVENTS_MAX:],
            "users": self.users,
        }

    def _save(self):
        """Durably write the state file. Raises StateError on failure.

        Write to a temp file, fsync it, keep a rolling backup of the previous
        good copy, then atomically replace. Always called under the lock so
        two threads can never interleave into the same temp file."""
        with self._lock:
            if self.load_error:
                msg = f"state saves disabled after bad load: {self.load_error}"
                logger.error(msg)
                self._alert("save_refused", msg)
                raise StateError(msg)
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                data = json.dumps(self._payload(), indent=2)
                tmp = self.state_path.with_name(self.state_path.name + ".tmp")
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                now = time.time()
                if (self.state_path.exists()
                        and now - self._last_backup >= BACKUP_INTERVAL_S):
                    shutil.copy2(self.state_path, self.backup_path)
                    self._last_backup = now
                os.replace(tmp, self.state_path)
                self._fsync_dir(self.state_path.parent)
            except Exception as exc:
                logger.exception("state save failed: %s", exc)
                self._alert("save_failed",
                            f"Saving {self.state_path.name} failed: "
                            f"{type(exc).__name__}: {exc}. Recent changes "
                            "(payments, watches) are NOT persisted.")
                raise StateError(str(exc)) from exc

    def _try_save(self):
        """Save for non-critical mutations (reads that downgrade, nudges,
        signal timestamps): the failure is logged and alerted by _save but
        must not break the caller."""
        try:
            self._save()
            return True
        except StateError:
            return False

    @staticmethod
    def _fsync_dir(directory):
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def export_bytes(self):
        """Current state as JSON bytes (admin backup export)."""
        with self._lock:
            return json.dumps(self._payload(), indent=2).encode("utf-8")

    # -- watches ------------------------------------------------------------
    @staticmethod
    def _watch_key(chat_id, pair):
        return f"{chat_id}:{pair}"

    def add_watch(self, chat_id, pair, style, mode):
        """Store a watch. Returns None when the per-chat cap is reached."""
        pair = pair.upper()
        key = self._watch_key(chat_id, pair)
        now = time.time()
        with self._lock:
            if key not in self.watches and \
                    len(self.list_watches(chat_id)) >= constants.MAX_WATCHES:
                return None
            self.watches[key] = {
                "key": key, "chat_id": chat_id, "pair": pair,
                "style": style, "mode": mode, "added_ts": now,
                "last_signal_ts": 0.0, "last_signal_side": None,
            }
            self._save()
            return self.watches[key]

    def remove_watch(self, chat_id, pair):
        key = self._watch_key(chat_id, pair.upper())
        with self._lock:
            if key in self.watches:
                del self.watches[key]
                self._save()
                return True
            return False

    def list_watches(self, chat_id):
        with self._lock:
            return [w for w in self.watches.values() if w["chat_id"] == chat_id]

    def start_autopilot(self, chat_id, style, mode):
        key = str(chat_id)
        with self._lock:
            self.autopilots[key] = AutoPilot(self.hub, chat_id, style, mode)
            self._save()

    def stop_autopilot(self, chat_id):
        key = str(chat_id)
        with self._lock:
            if key in self.autopilots:
                del self.autopilots[key]
                self._save()
                return True
            return False

    # -- background tick ----------------------------------------------------
    def _count_daily(self, key, limit):
        """Increment a per-day counter under the lock; False when over limit."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            counter = self.daily_counters.get(key) or {}
            if counter.get("date") != today:
                counter = {"date": today, "count": 0}
            if counter["count"] >= limit:
                return False
            counter["count"] += 1
            self.daily_counters[key] = counter
            return True

    def _prune_counters(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            stale = [k for k, v in self.daily_counters.items()
                     if not isinstance(v, dict) or v.get("date") != today]
            for k in stale:
                del self.daily_counters[k]

    def _note_feed_result(self, key, pair, error):
        """Track consecutive analysis failures per watch so a dead feed is
        visible in the logs and to the admin instead of silently retrying."""
        if error is None:
            self._feed_failures.pop(key, None)
            return
        n = self._feed_failures.get(key, 0) + 1
        self._feed_failures[key] = n
        logger.warning("watch %s analysis failed (%d in a row): %s: %s",
                       pair, n, type(error).__name__, error)
        if n == FEED_ALERT_AFTER:
            self._alert(f"feed:{pair}",
                        f"Feed for {pair} failed {n} times in a row: "
                        f"{type(error).__name__}: {error}")

    def _backoff_ok(self, key, now, interval):
        """Exponential backoff for watches whose feed keeps failing."""
        n = self._feed_failures.get(key, 0)
        if n == 0:
            return True
        return now - self.last_check.get(key, 0) >= interval * min(2 ** n, 16)

    async def tick(self, send):
        now = time.time()
        self._prune_counters()
        sem = asyncio.Semaphore(4)
        memo = {}

        async def analyze(pair, style, mode):
            k = (pair, style, mode)
            if k not in memo:
                async with sem:
                    memo[k] = await asyncio.to_thread(
                        signal_engine.quick_analyze, pair, style, mode, self.hub)
            return memo[k]

        due = []
        with self._lock:
            watches = list(self.watches.values())
        for watch in watches:
            key = watch["key"]
            if not self.is_pro(watch["chat_id"]):
                continue  # free tier: watches stay stored, resume on PRO
            style_profile = constants.STYLE_PROFILE[watch["style"]]
            interval = style_profile["check_interval_s"]
            if now - self.last_check.get(key, 0.0) < interval:
                continue
            if not self._backoff_ok(key, now, interval):
                continue
            self.last_check[key] = now
            due.append(watch)

        async def run_watch(watch):
            try:
                _, signal = await analyze(watch["pair"], watch["style"], watch["mode"])
            except Exception as exc:
                self._note_feed_result(watch["key"], watch["pair"], exc)
                return None
            self._note_feed_result(watch["key"], watch["pair"], None)
            return signal

        results = await asyncio.gather(*(run_watch(w) for w in due))
        for watch, signal in zip(due, results):
            if not signal:
                continue
            min_gap = constants.STYLE_PROFILE[watch["style"]]["min_gap_s"]
            # One alert per pair per gap, whichever side: a symbol hovering
            # at a crossover must not fire long/short/long every tick.
            if now - watch.get("last_signal_ts", 0.0) < min_gap:
                continue
            if not self._count_daily(f"{watch['chat_id']}:watch",
                                     constants.WATCH_DAILY_LIMIT):
                continue
            with self._lock:
                watch["last_signal_ts"] = now
                watch["last_signal_side"] = signal["side"]
                self._try_save()
            await send(watch["chat_id"], signal)

        with self._lock:
            pilots = list(self.autopilots.values())
        for pilot in pilots:
            if not self.is_pro(pilot.chat_id):
                continue
            style_profile = constants.STYLE_PROFILE[pilot.style]
            cadence = max(style_profile["check_interval_s"] * 2, 120)
            if now - pilot.last_run < cadence:
                continue
            try:
                async with sem:
                    signal, info = await asyncio.to_thread(
                        pilot.run, self.daily_counters, self._lock)
            except Exception as exc:
                logger.warning("autopilot chat=%s failed: %s: %s",
                               pilot.chat_id, type(exc).__name__, exc)
                continue
            if signal is not None:
                self._try_save()
                if info is None:
                    await send(pilot.chat_id, signal, source="autopilot")

    def watch_view(self):
        with self._lock:
            return [dict(w) for w in self.watches.values()]

    def autopilot_view(self):
        with self._lock:
            return [{"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                    for a in self.autopilots.values()]

import asyncio
import json
import threading
import time
from datetime import datetime, timezone

from .. import constants
from . import engine as signal_engine
from .autopilot import AutoPilot


class Service:
    def __init__(self, hub, state_path, pro_ids=(), admin_id=None):
        self.hub = hub
        self.state_path = state_path
        self.watches = {}
        self.autopilots = {}
        self.daily_counters = {}
        self.last_alert = {}
        self.last_check = {}
        self.plans = {}
        self.paid_events = []
        self.pro_ids = {int(i) for i in pro_ids}
        self.admin_id = admin_id
        # State is mutated from two threads: the asyncio bot loop and the
        # Stripe webhook thread. Every mutate+save runs under this lock so
        # activations and watch edits can never clobber each other.
        self._lock = threading.RLock()
        self._load()

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
                self._save()
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
        """Extend PRO. event_id makes payment webhooks idempotent: a
        redelivered Stripe/Telegram event returns the existing expiry
        instead of stacking another period."""
        with self._lock:
            if event_id:
                if event_id in self.paid_events:
                    return self._plan_rec(chat_id).get("until", 0.0)
                self.paid_events.append(str(event_id))
                del self.paid_events[:-500]  # bounded history
            now = time.time()
            rec = self._plan_rec(chat_id)
            base = max(now, rec.get("until", 0.0) if rec["plan"] == "pro" else 0.0)
            rec["plan"] = "pro"
            rec["until"] = base + months * 30 * 86400
            rec["nudged"] = False
            self._save()
            return rec["until"]

    def already_processed(self, event_id):
        with self._lock:
            return bool(event_id) and str(event_id) in self.paid_events

    def expiry_nudges(self):
        """Chat ids with stored watches/autopilot whose plan is free and
        who haven't been nudged yet (covers expiry + legacy pre-PRO)."""
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
            self._save()
        return out

    def _load(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for w in data.get("watches", []):
            self.watches[self._watch_key(w["chat_id"], w["pair"])] = w
        for a in data.get("autopilots", []):
            self.autopilots[str(a["chat_id"])] = AutoPilot(
                self.hub, a["chat_id"], a["style"], a["mode"])
        self.daily_counters = data.get("daily", {})
        self.paid_events = [str(x) for x in (data.get("paid_events") or [])][-500:]
        for k, v in (data.get("plans") or {}).items():
            rec = {"plan": "free", "until": 0.0, "trial_used": False,
                   "nudged": False}
            rec.update(v or {})
            self.plans[str(k)] = rec

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "watches": list(self.watches.values()),
                "autopilots": [
                    {"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                    for a in self.autopilots.values()
                ],
                "daily": self.daily_counters,
                "plans": self.plans,
                "paid_events": self.paid_events[-500:],
            }
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            pass

    @staticmethod
    def _watch_key(chat_id, pair):
        return f"{chat_id}:{pair}"

    def add_watch(self, chat_id, pair, style, mode):
        pair = pair.upper()
        key = self._watch_key(chat_id, pair)
        now = time.time()
        with self._lock:
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

    async def tick(self, send):
        now = time.time()
        for key, watch in list(self.watches.items()):
            if not self.is_pro(watch["chat_id"]):
                continue  # free tier: watches stay stored, resume on PRO
            style_profile = constants.STYLE_PROFILE[watch["style"]]
            interval = style_profile["check_interval_s"]
            if now - self.last_check.get(key, 0) < interval:
                continue
            self.last_check[key] = now
            try:
                # network I/O off the event loop: one slow feed must not
                # stall other users' replies or the rest of the tick
                analysis, signal = await asyncio.to_thread(
                    signal_engine.quick_analyze,
                    watch["pair"], watch["style"], watch["mode"], self.hub)
            except Exception:
                continue
            if not signal:
                continue
            min_gap = style_profile["min_gap_s"]
            if now - watch["last_signal_ts"] < min_gap and \
                    watch["last_signal_side"] == signal["side"]:
                continue
            with self._lock:
                watch["last_signal_ts"] = now
                watch["last_signal_side"] = signal["side"]
                self._save()
            await send(watch["chat_id"], signal)

        for pilot in list(self.autopilots.values()):
            if not self.is_pro(pilot.chat_id):
                continue
            style_profile = constants.STYLE_PROFILE[pilot.style]
            cadence = max(style_profile["check_interval_s"] * 2, 120)
            if now - pilot.last_run < cadence:
                continue
            try:
                signal, info = await asyncio.to_thread(
                    pilot.run, self.daily_counters)
            except Exception:
                continue
            if signal is not None:
                with self._lock:
                    self._save()
                if info is None:
                    await send(pilot.chat_id, signal, source="autopilot")

    def watch_view(self):
        return [dict(w) for w in self.watches.values()]

    def autopilot_view(self):
        return [{"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                for a in self.autopilots.values()]
import json
import time
from datetime import datetime, timezone

from .. import constants
from . import engine as signal_engine
from .autopilot import AutoPilot


class Service:
    def __init__(self, hub, state_path):
        self.hub = hub
        self.state_path = state_path
        self.watches = {}
        self.autopilots = {}
        self.daily_counters = {}
        self.last_alert = {}
        self.last_check = {}
        self._load()

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

    def _save(self):
        payload = {
            "watches": list(self.watches.values()),
            "autopilots": [
                {"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                for a in self.autopilots.values()
            ],
            "daily": self.daily_counters,
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _watch_key(chat_id, pair):
        return f"{chat_id}:{pair}"

    def add_watch(self, chat_id, pair, style, mode):
        pair = pair.upper()
        key = self._watch_key(chat_id, pair)
        now = time.time()
        self.watches[key] = {
            "key": key, "chat_id": chat_id, "pair": pair,
            "style": style, "mode": mode, "added_ts": now,
            "last_signal_ts": 0.0, "last_signal_side": None,
        }
        self._save()
        return self.watches[key]

    def remove_watch(self, chat_id, pair):
        key = self._watch_key(chat_id, pair.upper())
        if key in self.watches:
            del self.watches[key]
            self._save()
            return True
        return False

    def list_watches(self, chat_id):
        return [w for w in self.watches.values() if w["chat_id"] == chat_id]

    def start_autopilot(self, chat_id, style, mode):
        key = str(chat_id)
        self.autopilots[key] = AutoPilot(self.hub, chat_id, style, mode)
        self._save()

    def stop_autopilot(self, chat_id):
        key = str(chat_id)
        if key in self.autopilots:
            del self.autopilots[key]
            self._save()
            return True
        return False

    async def tick(self, send):
        now = time.time()
        for key, watch in list(self.watches.items()):
            style_profile = constants.STYLE_PROFILE[watch["style"]]
            interval = style_profile["check_interval_s"]
            if now - self.last_check.get(key, 0) < interval:
                continue
            self.last_check[key] = now
            try:
                analysis, signal = signal_engine.quick_analyze(
                    watch["pair"], watch["style"], watch["mode"], self.hub)
            except Exception:
                continue
            if not signal:
                continue
            min_gap = style_profile["min_gap_s"]
            if now - watch["last_signal_ts"] < min_gap and \
                    watch["last_signal_side"] == signal["side"]:
                continue
            watch["last_signal_ts"] = now
            watch["last_signal_side"] = signal["side"]
            self._save()
            await send(watch["chat_id"], signal)

        for pilot in list(self.autopilots.values()):
            style_profile = constants.STYLE_PROFILE[pilot.style]
            cadence = max(style_profile["check_interval_s"] * 2, 120)
            if now - pilot.last_run < cadence:
                continue
            try:
                signal, info = pilot.run(self.daily_counters)
            except Exception:
                continue
            if signal is not None:
                self._save()
                if info is None:
                    await send(pilot.chat_id, signal, source="autopilot")

    def watch_view(self):
        return [dict(w) for w in self.watches.values()]

    def autopilot_view(self):
        return [{"chat_id": a.chat_id, "style": a.style, "mode": a.mode}
                for a in self.autopilots.values()]
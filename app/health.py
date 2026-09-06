"""Liveness signals shared between the bot loop and the HTTP health probe.

The probe used to return a constant 200 from a thread that had nothing to
do with the bot, so a wedged poller or a dead scheduler looked healthy to
the host forever. Now the scheduler and the Telegram client report in here
and the probe fails when they stop."""

import threading
import time

_lock = threading.Lock()
_beats = {}
_started = time.time()


def beat(name):
    with _lock:
        _beats[name] = time.time()


def age(name):
    with _lock:
        ts = _beats.get(name)
    return None if ts is None else time.time() - ts


def uptime():
    return time.time() - _started


def status(stale_s, grace_s=90):
    """(healthy, detail). Healthy during the start-up grace period, then
    only while the scheduler tick and the Telegram API check are fresh."""
    tick_age = age("tick")
    tg_age = age("telegram")
    detail = {
        "uptime_s": round(uptime()),
        "tick_age_s": None if tick_age is None else round(tick_age),
        "telegram_age_s": None if tg_age is None else round(tg_age),
    }
    if uptime() < grace_s:
        return True, detail
    if tick_age is None or tick_age > stale_s:
        return False, detail
    # The Telegram check runs every few minutes; allow a generous window.
    if tg_age is not None and tg_age > max(stale_s * 5, 900):
        return False, detail
    return True, detail

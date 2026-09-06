"""State-file durability and payment-integrity regressions.

These cover the failure modes that could lose a paying user's entitlement:
a corrupt state file being overwritten with empty state, a save failure
being confirmed as success, month arithmetic short-changing annual plans,
and idempotency keys rolling over."""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app.signals import scheduler as sched  # noqa: E402
from app.signals.scheduler import Service, StateError, add_months  # noqa: E402


def _svc(tmp_path, **kw):
    return Service(object(), tmp_path / "state.json", **kw)


# -- month arithmetic ---------------------------------------------------------

def test_add_months_is_calendar_based():
    jan1 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert datetime.fromtimestamp(add_months(jan1, 12), timezone.utc) == \
        datetime(2027, 1, 1, tzinfo=timezone.utc)
    jan31 = datetime(2026, 1, 31, tzinfo=timezone.utc).timestamp()
    assert datetime.fromtimestamp(add_months(jan31, 1), timezone.utc) == \
        datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert add_months(jan1, 12) - jan1 == 365 * 86400  # not 360 days


def test_twelve_month_plan_is_a_full_year(tmp_path):
    svc = _svc(tmp_path)
    until = svc.activate_pro(1, 12)
    assert until - time.time() > 364 * 86400


def test_purchase_during_trial_keeps_trial_days(tmp_path):
    svc = _svc(tmp_path)
    trial_until = svc.start_trial(2)
    until = svc.activate_pro(2, 1)
    assert until > add_months(trial_until, 1) - 5


def test_months_are_clamped(tmp_path):
    svc = _svc(tmp_path)
    until = svc.activate_pro(3, 10 ** 9)
    assert until - time.time() < (constants.MAX_PLAN_MONTHS + 1) * 31 * 86400


# -- corrupt state ---------------------------------------------------------------

def test_corrupt_state_is_quarantined_and_saves_refused(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    alerts = []
    svc = Service(object(), path, on_alert=alerts.append)
    assert svc.load_error
    assert not path.exists()  # moved aside, never overwritten
    assert list(tmp_path.glob("state.json.corrupt-*"))
    with pytest.raises(StateError):
        svc.activate_pro(1, 1, event_id="evt_1")
    # rolled back: a retry of the same event is not treated as a replay
    assert not svc.already_processed("evt_1")
    assert svc.get_plan(1) == "free"
    assert any("DISABLED" in a for a in alerts)


def test_corrupt_state_restores_from_backup(tmp_path):
    path = tmp_path / "state.json"
    good = Service(object(), path)
    good.activate_pro(7, 6, event_id="evt_good")
    # promote the current good file to the backup slot, then corrupt main
    path.replace(good.backup_path)
    path.write_text("", encoding="utf-8")
    alerts = []
    svc = Service(object(), path, on_alert=alerts.append)
    assert svc.load_error is None
    assert svc.is_pro(7) and svc.already_processed("evt_good")
    assert alerts and "Restored" in alerts[-1]
    svc.add_watch(7, "BTCUSD", "intraday", "normal")  # saves work again
    assert json.loads(path.read_text())["plans"]["7"]["plan"] == "pro"


def test_malformed_rows_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "watches": [{"chat_id": 1}, {"chat_id": 2, "pair": "ETHUSD",
                                    "style": "swing", "mode": "safe"}],
        "autopilots": [{"chat_id": 3}],
        "plans": {"9": {"plan": "pro", "until": time.time() + 86400}},
    }))
    svc = Service(object(), path)
    assert svc.load_error is None
    assert list(svc.watches) == ["2:ETHUSD"]
    assert svc.is_pro(9)


def test_backup_written_and_state_versioned(tmp_path):
    svc = _svc(tmp_path)
    svc.activate_pro(1, 1)
    svc.add_watch(1, "BTCUSD", "intraday", "normal")  # second save copies backup
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["version"] == sched.STATE_VERSION and data["saved_at"] > 0
    assert svc.backup_path.exists()


def test_save_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    alerts = []
    svc.on_alert = alerts.append

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sched.os, "replace", boom)
    with pytest.raises(StateError):
        svc.activate_pro(4, 1, event_id="evt_x")
    assert not svc.already_processed("evt_x")
    assert alerts and "NOT persisted" in alerts[0]


# -- idempotency ---------------------------------------------------------------

def test_paid_event_history_survives_many_payments(tmp_path):
    svc = _svc(tmp_path)
    for i in range(600):
        svc.paid_events.append(f"evt_{i}")
        svc._paid_set.add(f"evt_{i}")
    svc.activate_pro(1, 1, event_id="evt_new")
    # the old 500 cap would already have forgotten evt_0
    assert svc.already_processed("evt_0")


def test_forget_chat_drops_watches_keeps_plan(tmp_path):
    svc = _svc(tmp_path)
    svc.activate_pro(5, 1)
    svc.add_watch(5, "BTCUSD", "intraday", "normal")
    svc.start_autopilot(5, "intraday", "normal")
    assert svc.forget_chat(5)
    assert svc.list_watches(5) == [] and "5" not in svc.autopilots
    assert svc.is_pro(5)


def test_watch_cap_per_chat(tmp_path):
    svc = _svc(tmp_path)
    for i in range(constants.MAX_WATCHES):
        assert svc.add_watch(6, f"P{i}USD", "intraday", "normal") is not None
    assert svc.add_watch(6, "EXTRAUSD", "intraday", "normal") is None
    # re-adding an existing pair is an update, not a new slot
    assert svc.add_watch(6, "P0USD", "swing", "safe") is not None


# -- tick behaviour ---------------------------------------------------------------

def _run_tick(svc, signal_side):
    calls = []

    def fake_qa(pair, style, mode, hub):
        return {"pair": pair}, {"pair": pair, "side": signal_side}

    async def send(chat_id, signal, source="watch"):
        calls.append((chat_id, signal["side"]))

    import app.signals.engine as eng
    orig = eng.quick_analyze
    eng.quick_analyze = fake_qa
    try:
        asyncio.run(svc.tick(send))
    finally:
        eng.quick_analyze = orig
    return calls


def test_flip_flop_is_suppressed_within_min_gap(tmp_path):
    svc = _svc(tmp_path)
    svc.activate_pro(11, 1)
    svc.add_watch(11, "BTCUSD", "intraday", "normal")
    assert _run_tick(svc, "long") == [(11, "long")]
    svc.last_check.clear()  # force the watch to be due again
    assert _run_tick(svc, "short") == []  # opposite side, still inside the gap


def test_feed_failures_are_counted_and_backed_off(tmp_path):
    svc = _svc(tmp_path)
    svc.activate_pro(12, 1)
    svc.add_watch(12, "BTCUSD", "intraday", "normal")
    alerts = []
    svc.on_alert = alerts.append

    def fail(pair, style, mode, hub):
        raise IOError("feed down")

    async def send(*a, **k):
        raise AssertionError("must not send")

    import app.signals.engine as eng
    orig = eng.quick_analyze
    eng.quick_analyze = fail
    try:
        for _ in range(sched.FEED_ALERT_AFTER):
            # bypass the exponential backoff so every iteration counts one
            svc.last_check["12:BTCUSD"] = 0.0
            asyncio.run(svc.tick(send))
    finally:
        eng.quick_analyze = orig
    assert svc._feed_failures["12:BTCUSD"] == sched.FEED_ALERT_AFTER
    assert any("BTCUSD" in a for a in alerts)


def test_restored_watches_are_spread_after_restart(tmp_path):
    svc = _svc(tmp_path)
    for i in range(5):
        svc.add_watch(20 + i, "BTCUSD", "intraday", "normal")
    svc2 = Service(object(), tmp_path / "state.json")
    now = time.time()
    ages = [now - ts for ts in svc2.last_check.values()]
    assert len(ages) == 5 and all(0 <= a <= 300 for a in ages)

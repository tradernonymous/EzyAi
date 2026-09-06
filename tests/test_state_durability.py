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


# -- gift codes ------------------------------------------------------------------

def test_mint_and_redeem_gift_code(tmp_path):
    svc = _svc(tmp_path)
    codes = svc.mint_codes("1mo", 2, uses=1, days=30, created_by=1)
    assert len(codes) == 2 and all(c.startswith("EZY-") and len(c) == 13 for c in codes)
    assert not any(ch in "O0I1" for c in codes for ch in c[4:])
    status, until = svc.redeem_local_code(55, codes[0])
    assert status == "ok" and svc.is_pro(55)
    # same chat again: idempotent, no second period
    assert svc.redeem_local_code(55, codes[0]) == ("already", until)
    # single use: a different chat is refused
    assert svc.redeem_local_code(56, codes[0])[0] == "used"
    assert svc.redeem_local_code(56, "EZY-ZZZZ-ZZZZ")[0] == "not_found"
    # persisted
    svc2 = Service(object(), tmp_path / "state.json")
    assert svc2.codes[codes[0]]["redeemed"] == [55]
    assert svc2.redeem_local_code(57, codes[1])[0] == "ok"


def test_gift_code_multi_use_expiry_and_revoke(tmp_path):
    svc = _svc(tmp_path)
    (multi,) = svc.mint_codes("6mo", 1, uses=2)
    assert svc.redeem_local_code(1, multi)[0] == "ok"
    assert svc.redeem_local_code(2, multi)[0] == "ok"
    assert svc.redeem_local_code(3, multi)[0] == "used"
    (old,) = svc.mint_codes("1mo", 1)
    svc.codes[old]["expires_at"] = time.time() - 1
    assert svc.redeem_local_code(4, old)[0] == "expired"
    (gone,) = svc.mint_codes("1mo", 1)
    assert svc.revoke_code(gone) and not svc.revoke_code(gone)
    assert svc.redeem_local_code(5, gone)[0] == "not_found"
    live = [c for c, _, _ in svc.list_codes()]
    assert live == [] or all(c not in (multi, old, gone) for c in live)


def test_gift_code_rolls_back_when_save_fails(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    (code,) = svc.mint_codes("1mo", 1)
    monkeypatch.setattr(sched.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(StateError):
        svc.redeem_local_code(9, code)
    assert svc.codes[code]["uses_left"] == 1 and svc.codes[code]["redeemed"] == []


# -- trial and discount codes, trial length ----------------------------------------

def test_trial_code_grants_days_and_extends(tmp_path):
    svc = _svc(tmp_path)
    (code,) = svc.mint_codes(kind="trial", value=7, count=1)
    status, until = svc.redeem_local_code(21, code)
    assert status == "ok" and abs(until - (time.time() + 7 * 86400)) < 5
    assert svc.is_pro(21) and svc.plan_status(21)["trial_used"] is False
    (more,) = svc.mint_codes(kind="trial", value=3)
    status, until2 = svc.redeem_local_code(21, more)
    assert status == "ok" and abs(until2 - (until + 3 * 86400)) < 5


def test_discount_code_sets_and_consumes(tmp_path):
    svc = _svc(tmp_path)
    (code,) = svc.mint_codes(kind="discount", value=20, count=1, uses=5)
    assert svc.redeem_local_code(31, code) == ("ok_discount", 20)
    d = svc.discount_for(31)
    assert d and d["percent"] == 20 and d["code"] == code
    assert not svc.is_pro(31)  # a discount grants nothing by itself
    svc2 = Service(object(), tmp_path / "state.json")
    assert svc2.discount_for(31)["percent"] == 20
    assert svc2.consume_discount(31)["percent"] == 20
    assert svc2.consume_discount(31) is None and svc2.discount_for(31) is None
    # same code, same chat: refused; another chat: fine (5 uses)
    assert svc2.redeem_local_code(31, code)[0] == "already"
    assert svc2.redeem_local_code(32, code)[0] == "ok_discount"


def test_expired_discount_is_ignored(tmp_path):
    svc = _svc(tmp_path)
    svc.set_discount(41, "EZY-AAAA-BBBB", 50, time.time() - 1)
    assert svc.discount_for(41) is None


def test_mint_validates_kind_and_value(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.mint_codes(kind="discount", value=0)
    with pytest.raises(ValueError):
        svc.mint_codes(kind="discount", value=95)
    with pytest.raises(ValueError):
        svc.mint_codes(kind="bogus", value=1)
    (t,) = svc.mint_codes(kind="trial", value=9999)
    assert svc.codes[t]["days"] == 366


def test_trial_days_setting_persists_and_bounds(tmp_path):
    svc = _svc(tmp_path)
    assert svc.trial_days() == constants.TRIAL_DAYS
    assert svc.set_trial_days(7) == 7
    assert svc.set_trial_days(99) == sched.TRIAL_DAYS_MAX
    assert svc.set_trial_days(0) == 1
    svc.set_trial_days(5)
    svc2 = Service(object(), tmp_path / "state.json")
    assert svc2.trial_days() == 5
    until = svc2.start_trial(51)
    assert abs(until - (time.time() + 5 * 86400)) < 5

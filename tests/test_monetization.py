import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import billing  # noqa: E402
from app import constants  # noqa: E402
from app import ui  # noqa: E402
from app.formatting import message as msg  # noqa: E402
from app.signals.scheduler import Service  # noqa: E402


def _svc(tmp_path, **kw):
    return Service(object(), tmp_path / "state.json", **kw)


# -- billing helpers --------------------------------------------------------

def test_payload_round_trip():
    p = billing.encode_payload("6mo", "stars", 123)
    assert billing.decode_payload(p) == {"tier": "6mo", "method": "stars",
                                         "chat_id": 123}
    assert billing.decode_payload("junk") is None
    assert billing.decode_payload("ezypro:9mo:stars:1") is None
    assert billing.decode_payload("ezypro:6mo:cash:1") is None
    assert billing.decode_payload("ezypro:6mo:stars:abc") is None


def test_tier_catalog():
    assert billing.duration_days("6mo") == 180
    line = billing.tier_line("6mo")
    assert "$44.99" in line and "MOST POPULAR" in line
    assert "$99.99" in billing.tier_line("12mo")
    assert "$14.99" in billing.tier_line("1mo")
    assert billing.tier("nope") is None


# -- plans lifecycle ---------------------------------------------------------

def test_free_default_and_trial_once(tmp_path):
    svc = _svc(tmp_path)
    assert svc.get_plan(1) == "free"
    assert not svc.is_pro(1)
    until = svc.start_trial(1)
    assert until and svc.is_pro(1)
    assert svc.plan_status(1)["trial_used"] is True
    assert svc.start_trial(1) is None  # one trial ever


def test_expiry_downgrades(tmp_path):
    svc = _svc(tmp_path)
    svc.start_trial(2)
    svc.plans["2"]["until"] = time.time() - 1
    assert svc.get_plan(2) == "free"
    assert not svc.is_pro(2)


def test_pro_activation_extends(tmp_path):
    svc = _svc(tmp_path)
    u1 = svc.activate_pro(3, 1)
    assert svc.is_pro(3)
    u2 = svc.activate_pro(3, 1)
    assert u2 > u1 + 29 * 86400  # stacked, not reset


def test_plans_persist(tmp_path):
    svc = _svc(tmp_path)
    svc.start_trial(4)
    svc.activate_pro(5, 6)
    svc2 = Service(object(), tmp_path / "state.json")
    assert svc2.is_pro(4) and svc2.is_pro(5)
    assert svc2.plan_status(4)["trial_used"] is True


def test_team_access_lists(tmp_path):
    svc = _svc(tmp_path, pro_ids=(7,), admin_id=8)
    assert svc.is_pro(7) and svc.is_comped(7)
    assert svc.is_pro(8) and svc.is_comped(8)
    assert not svc.is_pro(9) and not svc.is_comped(9)
    assert svc.get_plan(7) == "free"  # comped, no stored plan


def test_expiry_nudges_once(tmp_path):
    svc = _svc(tmp_path)
    svc.add_watch(10, "BTCUSD", "intraday", "normal")
    assert svc.expiry_nudges() == [10]
    assert svc.expiry_nudges() == []


def test_tick_skips_free_watches(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc.add_watch(11, "BTCUSD", "intraday", "normal")
    calls = []

    def fake_qa(pair, style, mode, hub):
        return {"pair": pair}, {"pair": pair, "side": "long"}

    async def send(chat_id, signal, source="watch"):
        calls.append((chat_id, signal))

    monkeypatch.setattr("app.signals.engine.quick_analyze", fake_qa)
    asyncio.run(svc.tick(send))
    assert calls == []  # free: stored but silent
    svc.activate_pro(11, 1)
    asyncio.run(svc.tick(send))
    assert len(calls) == 1 and calls[0][0] == 11


# -- copy --------------------------------------------------------------------

def test_pro_gate_copy():
    t = msg.pro_gate("Live watch alerts", True)
    assert "PRO feature" in t and "3-day free trial" in t
    t2 = msg.pro_gate("Live watch alerts", False)
    assert "trial" not in t2.lower() or "already" in t2.lower()


def test_plans_copy():
    t = msg.plans_text(True)
    assert "$14.99" in t and "$44.99" in t and "$99.99" in t
    assert "MOST POPULAR" in t and "free trial" in t
    t2 = msg.plans_text(False)
    assert "already used" in t2


def test_account_copy():
    free = msg.account_text({"plan": "free", "until": 0, "trial_used": False},
                            0, False)
    assert "Free" in free and "trial" in free
    pro = msg.account_text({"plan": "pro", "until": time.time() + 10 * 86400,
                            "trial_used": True}, 2, True)
    assert "PRO" in pro and "2 pair" in pro
    comped = msg.account_text({"plan": "free", "until": 0, "trial_used": False},
                              0, False, comped=True)
    assert "team access" in comped
    assert "/plans" in msg.expiry_nudge_text()


# -- ui ----------------------------------------------------------------------

def test_commands_and_callbacks():
    cmds = [c for c, _ in ui.COMMANDS]
    assert "plans" in cmds and "account" in cmds
    assert ui.parse_callback("ezy:plans") == {"a": "plans"}
    assert ui.parse_callback("ezy:trial") == {"a": "trial"}
    assert ui.parse_callback("ezy:pay:6mo:stars") == {
        "a": "pay", "tier": "6mo", "method": "stars"}
    assert ui.parse_callback("ezy:paid:1mo") == {"a": "paid", "tier": "1mo"}
    assert ui.parse_callback("ezy:admin_ok:5:12mo") == {
        "a": "admin_ok", "chat": 5, "tier": "12mo"}
    assert ui.parse_callback("ezy:admin_no:5") == {"a": "admin_no", "chat": 5}


def _cbs(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_plans_and_pay_keyboards():
    cbs = _cbs(ui.plans_keyboard(True))
    assert "ezy:trial" in cbs
    assert "ezy:pay:6mo:stars" in cbs
    assert "ezy:pay:12mo:stars" in cbs
    assert "ezy:trial" not in _cbs(ui.plans_keyboard(False))
    pay = _cbs(ui.pay_methods_keyboard("1mo"))
    assert "ezy:pay:1mo:stars" in pay
    assert "ezy:pay:1mo:card" in pay
    assert "ezy:pay:1mo:usdt" in pay


# -- fundamentals gating ------------------------------------------------------

def _rich_fund():
    return {
        "price": 200.0, "high_52w": 220.0, "low_52w": 150.0,
        "chg_1w": 1.0, "chg_1m": 2.0, "chg_3m": 5.0, "chg_1y": 20.0,
        "vol_pct": 20.0, "avg_volume_20": 1000, "fscore": 72.0, "fgrade": "B",
        "fpillars": {"valuation": 15.0}, "fpe": 22.0, "fnotes": [],
        "piotroski": {"score": 7, "total": 9, "passed": [], "failed": []},
        "earn_quality": "strong cash conversion",
        "dcf": {"intrinsic": 250.0, "growth_used": 0.1, "discount": 0.09,
                "terminal": 0.025, "years": 5, "assumptions": "x"},
        "dcf_verdict": {"intrinsic": 250.0, "mos_pct": 20.0, "label": "undervalued"},
    }


def test_fundamentals_pro_gating():
    full = msg.fundamentals_report("stock", "AAPL", _rich_fund(), "live", pro=True)
    assert "Fundamental score" in full and "Fair value" in full
    assert "Piotroski" in full and "Executive summary" in full
    lite = msg.fundamentals_report("stock", "AAPL", _rich_fund(), "live", pro=False)
    assert "Fundamental score" not in lite and "Fair value" not in lite
    assert "Piotroski" not in lite and "Executive summary" not in lite
    assert "52w range" in lite  # free keeps price stats
    assert msg.pro_upsell_note().strip().startswith("\U0001f512")


def test_config_pro_ids(monkeypatch):
    import os
    from app import config
    monkeypatch.setenv("PRO_ACCESS_IDS", "1, 2,abc,3")
    assert config.pro_access_ids() == (1, 2, 3)
    monkeypatch.delenv("PRO_ACCESS_IDS")
    assert config.pro_access_ids() == ()
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    assert config.stripe_api_key() == "sk_test_x"
    assert config.stripe_webhook_secret() == "whsec_x"
    assert config.bot_username() == "ezytradeai_bot"


def test_stripe_session_params_dynamic():
    p = billing.stripe_session_params("6mo", 42, "https://t.me/x?start=paid",
                                      "https://t.me/x?start=cancelled")
    assert p["mode"] == "payment"
    item = p["line_items"][0]
    assert item["quantity"] == 1
    assert "price" not in item  # no preset Price ID: dynamic price_data
    pd = item["price_data"]
    assert pd["currency"] == "usd" and pd["unit_amount"] == 4499
    assert "6 months" in pd["product_data"]["name"]
    assert billing.decode_payload(p["metadata"]["order"]) == {
        "tier": "6mo", "method": "card", "chat_id": 42}
    assert billing.stripe_session_params(
        "1mo", 1, "s", "c")["line_items"][0]["price_data"]["unit_amount"] == 1499
    assert billing.stripe_session_params(
        "12mo", 1, "s", "c")["line_items"][0]["price_data"]["unit_amount"] == 9999


class _FakeService:
    def __init__(self):
        self.calls = []

    def activate_pro(self, chat_id, months):
        self.calls.append((chat_id, months))
        return 999.0


def test_fulfill_checkout_session():
    svc = _FakeService()
    sess = {"payment_status": "paid",
            "metadata": {"order": billing.encode_payload("12mo", "card", 77)}}
    assert billing.fulfill_checkout_session(sess, svc) == (77, "12mo", 999.0)
    assert svc.calls == [(77, 12)]
    assert billing.fulfill_checkout_session(
        {"payment_status": "unpaid", "metadata": {}}, svc) is None
    assert billing.fulfill_checkout_session(
        {"payment_status": "paid", "metadata": {"order": "junk"}}, svc) is None
    assert billing.fulfill_checkout_session({"payment_status": "paid"}, svc) is None
    assert svc.calls == [(77, 12)]  # no activation on rejects


def test_verify_stripe_event_rejects_garbage():
    assert billing.verify_stripe_event(b"{}", "bad", "whsec_x") is None
    assert billing.verify_stripe_event(b"", "", "") is None

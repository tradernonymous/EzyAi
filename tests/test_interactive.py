import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants  # noqa: E402
from app import ui  # noqa: E402
from app.formatting import message as msg  # noqa: E402


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_no_reply_keyboard_anywhere():
    # The bottom reply-keyboard was removed for good: only inline buttons
    # (chat-attached, silent) plus the / command menu remain.
    import re
    app_dir = Path(__file__).resolve().parent.parent / "app"
    for py in app_dir.rglob("*.py"):
        for line in py.read_text().splitlines():
            assert "ReplyKeyboardMarkup" not in line, f"{py.name}: {line}"
            assert not re.match(r"\s*main_keyboard\s*\(", line)


def test_menu_routes_all_labels():
    for label in ui.MENU_LABELS:
        assert ui.route_menu(label) is not None
    assert ui.route_menu("random text") is None
    assert ui.route_menu("") is None


def test_commands_cover_every_function():
    cmds = [c for c, _ in ui.COMMANDS]
    for c in ("analyze", "watch", "watches", "quote", "fundamentals",
              "autopilot", "dashboard", "help"):
        assert c in cmds


def test_callback_round_trips():
    cases = [
        ui.cb_menu("analyze"), ui.cb_menu("watch", "BTCUSD"),
        ui.cb_ppage("fund", 3), ui.cb_pick("quote", "XAUUSD"),
        ui.cb_pick("analyze", "custom"), ui.cb_style("watch", "swing"),
        ui.cb_mode("auto", "aggressive"), ui.cb_back("analyze", "pair"),
        ui.cb_unwatch("EURUSD"),
        "ezy:cancel", "ezy:watch_go", "ezy:auto_go",
        "ezy:auto_stop", "ezy:auto_stop_yes", "ezy:dash",
    ]
    for data in cases:
        assert len(data) <= 64, data
        parsed = ui.parse_callback(data)
        assert parsed["a"] != "unknown", data
    assert ui.parse_callback("ezy:menu:watch:BTCUSD") == {
        "a": "menu", "flow": "watch", "pair": "BTCUSD"}
    assert ui.parse_callback("ezy:style:auto:intraday") == {
        "a": "style", "flow": "auto", "style": "intraday"}
    assert ui.parse_callback("junk")["a"] == "unknown"
    assert ui.parse_callback("ezy:bogus:x")["a"] == "unknown"
    assert ui.parse_callback("ezy:ppage:analyze:zz")["a"] == "unknown"


def test_pair_keyboard_pagination():
    first = ui.pair_keyboard("analyze", 0)
    cbs = _callbacks(first)
    assert any(c == "ezy:ppage:analyze:1" for c in cbs)
    assert not any("ppage:analyze:-1" in c for c in cbs)
    last = ui.pair_keyboard("analyze", 99)
    cbs = _callbacks(last)
    assert any("ppage:analyze:" in c and ":1" not in c.split(":ppage:")[1][:2]
               for c in cbs) or any("Prev" in t for t in _texts(last))
    assert any("Prev" in t for t in _texts(last))
    picks = [c for c in _callbacks(first) if c.startswith("ezy:pick:analyze:")]
    assert len(picks) >= 9
    assert "ezy:pick:analyze:custom" in cbs
    assert "ezy:cancel" in cbs


def test_style_mode_keyboards_cover_options():
    skb = ui.style_keyboard("watch")
    for s in constants.STYLES:
        assert f"ezy:style:watch:{s}" in _callbacks(skb)
    assert "ezy:back:watch:pair" in _callbacks(skb)
    mkb = ui.mode_keyboard("watch")
    for m in constants.MODES:
        assert f"ezy:mode:watch:{m}" in _callbacks(mkb)
    assert "ezy:back:watch:style" in _callbacks(mkb)


def test_back_buttons_and_main_menu_everywhere():
    assert ui.MENU_DASH in ui.MENU_LABELS  # main menu button exists
    assert ui.route_menu(ui.MENU_DASH) == "dash"
    # pair picker carries a menu escape hatch, not just cancel
    cbs = _callbacks(ui.pair_keyboard("analyze", 0))
    assert "ezy:menu:dash" in cbs
    assert "ezy:cancel" in cbs
    # autopilot has no pair step: its style Back goes to the menu
    auto_cbs = _callbacks(ui.style_keyboard("auto"))
    assert "ezy:menu:dash" in auto_cbs
    assert not any(c.startswith("ezy:back:auto:") for c in auto_cbs)
    # custom-symbol prompt has Back + Cancel
    custom_cbs = _callbacks(ui.custom_pair_keyboard("analyze"))
    assert "ezy:menu:analyze" in custom_cbs
    assert "ezy:cancel" in custom_cbs
    # every guided flow's keyboards carry cancel
    for kb in (ui.style_keyboard("watch"), ui.mode_keyboard("watch"),
               ui.confirm_keyboard("ezy:watch_go", "ezy:back:watch:mode")):
        assert "ezy:cancel" in _callbacks(kb)


def test_followup_carries_pair():
    cbs = _callbacks(ui.followup_keyboard("BTCUSD"))
    assert "ezy:menu:watch:BTCUSD" in cbs
    assert "ezy:menu:fund:BTCUSD" in cbs
    assert "ezy:menu:quote:BTCUSD" in cbs


def test_watches_keyboard_per_row_buttons():
    rows = [{"pair": "BTCUSD", "style": "intraday", "mode": "normal",
             "last_signal_ts": 1},
            {"pair": "XAUUSD", "style": "swing", "mode": "safe",
             "last_signal_ts": 0}]
    cbs = _callbacks(ui.watches_keyboard(rows))
    assert "ezy:unwatch:BTCUSD" in cbs
    assert "ezy:unwatch:XAUUSD" in cbs
    assert "ezy:menu:watch" in cbs


def test_dashboard_is_status_only_with_refresh():
    # dashboard no longer duplicates the bottom menu: status + refresh only
    cbs = _callbacks(ui.refresh_keyboard())
    assert cbs == ["ezy:dash"]
    assert ui.parse_callback("ezy:dash")["a"] == "dash"


def test_flow_prompts_reference_steps():
    for flow in ("analyze", "watch", "fund", "quote"):
        text, kb = ui.prompt_pair(flow)
        assert "step 1/3" in text
        assert kb is not None
    text, kb = ui.prompt_style("analyze", "BTCUSD")
    assert "BTCUSD" in text and "step 2/3" in text
    text, kb = ui.prompt_mode("analyze", "BTCUSD", "intraday")
    assert "intraday" in text and "step 3/3" in text


class _Pilot:
    style = "intraday"
    mode = "normal"


def test_dashboard_view_states():
    watches = [{"pair": "BTCUSD", "style": "intraday", "mode": "normal"}]
    on = msg.dashboard_view(watches, _Pilot(), "live")
    assert "BTCUSD" in on and "ON" in on and "live" in on
    off = msg.dashboard_view([], None, "live")
    assert "off" in off and "0" in off


def test_dashboard_dots_reflect_alert_state():
    ws = [{"pair": "BTCUSD", "style": "intraday", "mode": "normal",
           "last_signal_ts": 123.0},
          {"pair": "XAUUSD", "style": "swing", "mode": "safe",
           "last_signal_ts": 0.0}]
    on = msg.dashboard_view(ws, _Pilot(), "live")
    assert "\U0001f7e2" in on and "\U0001f7e1" in on


def test_visual_helpers():
    spark = msg.sparkline([1, 2, 3, 2, 5, 4])
    assert len(spark) == 6 and spark[-2] == "\u2588"  # peak maps to full block
    assert msg.sparkline([5, 5, 5]) == "\u2500\u2500\u2500"
    assert msg.sparkline([]) == "" and msg.sparkline([1]) == ""
    bar = msg.position_bar(100.0, 200.0, 150.0, width=11)
    assert bar == "\u2500" * 5 + "\u25cf" + "\u2500" * 5
    assert msg.position_bar(100.0, 100.0, 100.0) == ""
    assert msg.position_bar(None, 200.0, 150.0) == ""
    # clamping at the edges
    assert msg.position_bar(0.0, 10.0, 99.0).endswith("\u25cf")
    assert msg.position_bar(0.0, 10.0, -5.0).startswith("\u25cf")


def test_quote_carries_range_bar():
    tick = {"price": 300.0, "change_pct": 1.0, "high": 310.0, "low": 290.0,
            "volume": 1234.0, "quote": "USDT", "mode": "live"}
    t = msg.quote_report("BTCUSD", tick)
    assert "BTCUSD" in t and "300.0000" in t and "+1.00%" in t
    assert "Day range:" in t and "\u25cf" in t


def test_signal_confidence_meter_and_levels():
    sig = {"pair": "BTCUSD", "side": "long", "style": "intraday",
           "mode": "normal", "tf": "15m", "entry_zone": (100.0, 101.0),
           "sl": 99.0, "tp1": 102.0, "tp2": 103.0, "rr": 2.0,
           "risk_pct": 1.0, "confidence": 78.0, "reasons": ["rsi bounce"],
           "support": [98.0], "resistance": [104.0]}
    t = msg.signal_message(sig)
    assert "\u25b0" in t and "Levels" in t and "support" in t
    assert "Not financial advice" in t


def test_confirm_texts_carry_risk():
    t = msg.confirm_watch_text("BTCUSD", "intraday", "normal", 300, 2.0, 1.0)
    assert "BTCUSD" in t and "300" in t and "1.0%" in t
    t = msg.confirm_auto_text("swing", "safe", 3)
    assert "swing/safe" in t and "3" in t
    assert "autopilot" in msg.auto_started_text("intraday", "normal").lower()
    assert "live" in msg.watch_added_text("XAUUSD", "swing", "safe").lower()

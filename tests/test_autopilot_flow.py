"""The autopilot button flow has no pair step. Drive it through the real
callback handler with fake Telegram objects: style -> mode -> confirm ->
running, plus Back at each step, for a PRO chat."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.signals.scheduler import Service  # noqa: E402


class _Query:
    def __init__(self, data, chat):
        self.data = data
        self.edits = []
        self.message = SimpleNamespace(reply_text=self._reply)
        self._chat = chat

    async def answer(self, *a, **k):
        return None

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw))

    async def _reply(self, text, **kw):
        self.edits.append((text, kw))


class _Chat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.sent = []

    async def send_message(self, text, **kw):
        self.sent.append((text, kw))


def _tap(bot, ctx, chat, data):
    q = _Query(data, chat)
    update = SimpleNamespace(
        callback_query=q, effective_chat=chat,
        effective_user=SimpleNamespace(id=chat.id, username="tester"))
    asyncio.run(bot.cb_flow(update, ctx))
    return q.edits[-1][0] if q.edits else (chat.sent[-1][0] if chat.sent else "")


def _bot(tmp_path):
    from app.bot import Bot
    svc = Service(object(), tmp_path / "state.json")
    svc.activate_pro(77, 1)
    return Bot("0" * 46, object(), svc), svc


def test_autopilot_button_flow_starts_a_scanner(tmp_path):
    bot, svc = _bot(tmp_path)
    ctx = SimpleNamespace(user_data={}, bot=None)
    chat = _Chat(77)
    assert "step 1/3" in _tap(bot, ctx, chat, "ezy:menu:auto")
    text = _tap(bot, ctx, chat, "ezy:style:auto:swing")
    assert "step 2/3" in text and "start over" not in text
    assert "Confirm autopilot" in _tap(bot, ctx, chat, "ezy:mode:auto:normal")
    assert "Autopilot live" in _tap(bot, ctx, chat, "ezy:auto_go")
    assert [(p.style, p.mode) for p in svc.list_autopilots(77)] == [("swing", "normal")]
    # the button now shows the running scanner; Add style re-enters step 1
    assert "swing \u00b7 normal" in _tap(bot, ctx, chat, "ezy:menu:auto")
    assert "step 1/3" in _tap(bot, ctx, chat, "ezy:auto_add")
    assert "step 2/3" in _tap(bot, ctx, chat, "ezy:style:auto:scalping")
    _tap(bot, ctx, chat, "ezy:mode:auto:safe")
    _tap(bot, ctx, chat, "ezy:auto_go")
    assert sorted(p.style for p in svc.list_autopilots(77)) == ["scalping", "swing"]


def test_autopilot_flow_back_buttons_stay_in_the_flow(tmp_path):
    bot, svc = _bot(tmp_path)
    ctx = SimpleNamespace(user_data={}, bot=None)
    chat = _Chat(77)
    _tap(bot, ctx, chat, "ezy:menu:auto")
    _tap(bot, ctx, chat, "ezy:style:auto:intraday")
    _tap(bot, ctx, chat, "ezy:mode:auto:aggressive")
    assert "step 2/3" in _tap(bot, ctx, chat, "ezy:back:auto:mode")
    assert "step 1/3" in _tap(bot, ctx, chat, "ezy:back:auto:style")
    assert svc.list_autopilots(77) == []


def test_autopilot_stop_one_style_keeps_the_other(tmp_path):
    bot, svc = _bot(tmp_path)
    ctx = SimpleNamespace(user_data={}, bot=None)
    chat = _Chat(77)
    svc.start_autopilot(77, "swing", "normal")
    svc.start_autopilot(77, "scalping", "safe")
    assert "Stop autopilot (swing/normal)?" in _tap(bot, ctx, chat, "ezy:auto_stop:swing")
    left = _tap(bot, ctx, chat, "ezy:auto_stop_yes:swing")
    assert "scalping \u00b7 safe" in left and "swing" not in left
    assert [p.style for p in svc.list_autopilots(77)] == ["scalping"]
    assert "Autopilot stopped." in _tap(bot, ctx, chat, "ezy:auto_stop_yes")
    assert svc.list_autopilots(77) == []


def test_autopilot_scope_toggles_exclude_classes(tmp_path):
    bot, svc = _bot(tmp_path)
    ctx = SimpleNamespace(user_data={}, bot=None)
    chat = _Chat(77)
    _tap(bot, ctx, chat, "ezy:menu:auto")
    _tap(bot, ctx, chat, "ezy:style:auto:swing")
    text = _tap(bot, ctx, chat, "ezy:mode:auto:normal")
    assert "step 3/3" in text and "Confirm autopilot" in text
    text = _tap(bot, ctx, chat, "ezy:auto_x:crypto")
    assert "without Crypto" in text
    text = _tap(bot, ctx, chat, "ezy:auto_x:stocks")
    assert "Metals & CFDs and Forex only" in text
    # toggling back re-includes the class
    text = _tap(bot, ctx, chat, "ezy:auto_x:crypto")
    assert "without Stocks" in text
    # every class off is refused: the last one stays on
    for cls in ("metals", "forex", "crypto"):
        text = _tap(bot, ctx, chat, f"ezy:auto_x:{cls}")
    assert "only" in text
    started = _tap(bot, ctx, chat, "ezy:auto_go")
    assert "Autopilot live" in started
    pilot = svc.list_autopilots(77)[0]
    assert len(pilot.exclude) == 3 and pilot._order
    from app import constants
    assert all(constants.asset_class(p) not in pilot.exclude for p in pilot._order)


def test_typed_autopilot_flags_exclude_classes(tmp_path):
    bot, svc = _bot(tmp_path)
    assert bot._parse_scope_args(["-crypto", "no-stocks", "--forex", "junk"]) == [
        "crypto", "stocks", "forex"]
    assert bot._parse_scope_args([]) == []

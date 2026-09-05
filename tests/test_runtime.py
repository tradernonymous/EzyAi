import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram.ext import Application  # noqa: E402


def test_post_init_must_not_set_custom_attrs_on_application():
    # python-telegram-bot's Application uses __slots__: assigning an ad-hoc
    # attribute (the original _ezy_loop) raised AttributeError at startup and
    # crash-looped the deployed bot. The loop must be kept in a module global
    # instead. This test fails if the pattern regresses.
    captured = {}

    async def post_init(app):
        captured["loop"] = asyncio.get_running_loop()

    app = Application.builder().token("0" * 46).build()
    app.post_init = post_init
    asyncio.run(app.post_init(app))  # noqa:  unreliable_call
    assert captured["loop"] is not None
    # an attempt to stash a custom attr must not be what transports the value
    assert not hasattr(app, "_ezy_loop")
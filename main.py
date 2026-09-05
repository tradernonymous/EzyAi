import asyncio
import datetime
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import billing, config
from app.bot import Bot
from app.data.provider import DataHub
from app.signals.scheduler import Service

logger = logging.getLogger("ezyai")

# The bot loop runs inside python-telegram-bot's Application, which uses
# __slots__ and rejects custom attributes. The webhook thread needs a handle
# on that loop to post confirmations, so we capture it here at startup.
_EZY_LOOP = None


def _ev(event, *path):
    cur = event
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            try:
                cur = cur[key]
            except Exception:
                return None
        if cur is None:
            return None
    return cur


def admin_alert(bot_app, text):
    """Best-effort notify the configured admin of a paid-flow failure."""
    try:
        admin = config.admin_id()
        if not admin:
            return
        async def _send():
            await bot_app.bot.send_message(
                admin, f"\u26a0\ufe0f {text}", parse_mode="HTML")
        _post_to_loop(_send)
    except Exception as exc:
        logger.warning("admin alert failed: %s", exc)


def _post_to_loop(coro_factory):
    """Run a coroutine on the bot's loop from another thread."""
    loop = _EZY_LOOP
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro_factory(), loop).result(timeout=20)
    else:
        asyncio.run(coro_factory())


def notify_pro_active(bot_app, chat_id, tier, until):
    """Send the PRO confirmation from the webhook thread.

    Schedules onto the bot's own running event loop instead of spinning up
    a fresh one per payment (asyncio.run would build a new HTTP client each
    time and can silently drop the confirmation).
    """
    date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
    text = (f"\u2705 <b>PRO activated</b> \u00b7 {tier} until {date}.\n"
            "Your watches resume automatically. Enjoy!")

    async def _send():
        await bot_app.bot.send_message(chat_id, text, parse_mode="HTML")

    try:
        _post_to_loop(_send)
    except Exception as exc:
        logger.warning("pro notify failed chat=%s: %s", chat_id, exc)


def start_webhook_server(service, bot_app):
    port = int(os.environ.get("PORT", "8080"))
    secret = config.stripe_webhook_secret()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            if self.path != "/webhook/stripe" or not secret:
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            payload = self.rfile.read(length)
            sig = self.headers.get("Stripe-Signature", "")
            event = billing.verify_stripe_event(payload, sig, secret)
            if event is None:
                logger.warning("stripe webhook: bad signature")
                self.send_response(400)
                self.end_headers()
                return
            # ack fast so Stripe never retries on our processing time
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            event_id = _ev(event, "id")
            if _ev(event, "type") != "checkout.session.completed":
                return
            if service.already_processed(event_id):
                logger.info("stripe webhook replay ignored id=%s", event_id)
                return
            result = None
            try:
                result = billing.fulfill_checkout_session(
                    _ev(event, "data", "object"), service, event_id=event_id)
            except Exception as exc:
                logger.exception("stripe fulfillment failed id=%s", event_id)
                admin_alert(bot_app,
                            f"Stripe payment <b>{event_id}</b> failed to "
                            f"activate PRO: {exc} \u2014 check manually.")
            if result:
                chat_id, tier, until = result
                logger.info("stripe PRO activated chat=%s tier=%s id=%s",
                            chat_id, tier, event_id)
                notify_pro_active(bot_app, chat_id, tier, until)

        def log_message(self, *args):
            pass

    # threaded: a slow webhook must never block Fly's health probe
    threading.Thread(
        target=ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever,
        daemon=True,
    ).start()


def main():
    config.load_dotenv()
    logging.basicConfig(
        level=config.log_level(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    token = config.telegram_token()
    if not token:
        print("=" * 60)
        print("TELEGRAM_BOT_TOKEN is not set.")
        print("1. Copy .env.example to .env")
        print("2. Put your token from @BotFather into .env")
        print("3. Run: python main.py")
        print("=" * 60)
        sys.exit(1)

    hub = DataHub(allow_demo=config.allow_demo_data())
    service = Service(hub, config.state_file(),
                      pro_ids=config.pro_access_ids(),
                      admin_id=config.admin_id())
    bot = Bot(token, hub, service, demo_ok=config.allow_demo_data(),
              pay_config={"usdt_address": config.usdt_address(),
                          "admin_id": config.admin_id(),
                          "stripe_key": config.stripe_api_key(),
                          "bot_username": config.bot_username()})

    start_webhook_server(service, bot.app)
    logger.info("starting EzyAi ...")

    async def _remember_loop(app):
        # webhook thread needs a handle on the running loop
        global _EZY_LOOP
        _EZY_LOOP = asyncio.get_running_loop()
        await bot.post_init_hook(app)

    bot.app.post_init = _remember_loop
    # drop_pending_updates stays False: a Stars payment confirmation queued
    # during a restart must still be delivered, or the user pays for nothing
    bot.app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()

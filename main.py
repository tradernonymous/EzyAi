import asyncio
import datetime
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import billing, config
from app.bot import Bot
from app.data.provider import DataHub
from app.signals.scheduler import Service

logger = logging.getLogger("ezyai")


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


def notify_pro_active(bot_app, chat_id, tier, until):
    date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")

    async def _send():
        await bot_app.bot.send_message(
            chat_id,
            f"\u2705 <b>PRO activated</b> \u00b7 {tier} until {date}.\n"
            "Your watches resume automatically. Enjoy!",
            parse_mode="HTML")

    try:
        asyncio.run(_send())
    except Exception as exc:
        logger.warning("pro notify failed: %s", exc)


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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            if _ev(event, "type") == "checkout.session.completed":
                result = billing.fulfill_checkout_session(
                    _ev(event, "data", "object"), service)
                if result:
                    chat_id, tier, until = result
                    logger.info("stripe PRO activated chat=%s tier=%s", chat_id, tier)
                    notify_pro_active(bot_app, chat_id, tier, until)

        def log_message(self, *args):
            pass

    threading.Thread(
        target=HTTPServer(("0.0.0.0", port), Handler).serve_forever,
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
    bot.app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

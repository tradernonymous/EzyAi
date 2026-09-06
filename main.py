import asyncio
import datetime
import importlib.util
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import billing, config, health
from app.bot import Bot
from app.data.provider import DataHub
from app.signals.scheduler import Service, StateError

logger = logging.getLogger("ezyai")

# The bot loop runs inside python-telegram-bot's Application, which uses
# __slots__ and rejects custom attributes. The webhook thread needs a handle
# on that loop to post confirmations, so we capture it here at startup.
_EZY_LOOP = None

# Stripe webhook bodies are a few KB; anything larger is not Stripe.
MAX_WEBHOOK_BODY = 64 * 1024


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
    """Best-effort notify the configured admin (payments, feeds, state)."""
    try:
        admin = config.admin_id()
        if not admin:
            return
        from html import escape

        async def _send():
            await bot_app.bot.send_message(
                admin, f"⚠️ {escape(str(text))}", parse_mode="HTML")
        _post_to_loop(_send)
    except Exception as exc:
        logger.warning("admin alert failed: %s", exc)


def _post_to_loop(coro_factory):
    """Run a coroutine on the bot's loop from another thread."""
    loop = _EZY_LOOP
    if loop is None or not loop.is_running():
        raise RuntimeError("bot loop is not running")
    try:
        # Already on the bot loop (alerts raised from a handler): schedule
        # instead of blocking on our own loop.
        if asyncio.get_running_loop() is loop:
            loop.create_task(coro_factory())
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(coro_factory(), loop).result(timeout=20)


def notify_pro_active(bot_app, chat_id, tier, until):
    """Send the PRO confirmation from the webhook thread.

    Schedules onto the bot's own running event loop instead of spinning up
    a fresh one per payment (asyncio.run would build a new HTTP client each
    time and can silently drop the confirmation).
    """
    date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
    text = (f"✅ <b>PRO activated</b> · {tier} until {date}.\n"
            "Your watches resume automatically. Enjoy!")

    async def _send():
        await bot_app.bot.send_message(chat_id, text, parse_mode="HTML")

    try:
        _post_to_loop(_send)
    except Exception as exc:
        logger.warning("pro notify failed chat=%s: %s", chat_id, exc)


def build_handler(service, bot_app, secret, stale_s=180):
    """HTTP handler class for the health probe and the Stripe webhook."""

    class Handler(BaseHTTPRequestHandler):
        timeout = 10  # seconds of socket inactivity before the thread exits

        def _respond(self, code, body=b"", content_type="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            # Fly probes GET /. Report the bot's real liveness so a wedged
            # poller or scheduler gets the machine restarted.
            ok, detail = health.status(stale_s)
            body = json.dumps({"ok": ok, **detail}).encode()
            self._respond(200 if ok else 503, body, "application/json")

        def do_POST(self):
            if self.path != "/webhook/stripe" or not secret:
                self._respond(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_WEBHOOK_BODY:
                logger.warning("stripe webhook: rejected body length %s from %s",
                               length, self.client_address[0])
                self._respond(413)
                return
            payload = self.rfile.read(length)
            sig = self.headers.get("Stripe-Signature", "")
            event = billing.verify_stripe_event(payload, sig, secret)
            if event is None:
                logger.warning("stripe webhook: bad signature from %s",
                               self.client_address[0])
                self._respond(400)
                return
            event_id = _ev(event, "id")
            if _ev(event, "type") != "checkout.session.completed":
                self._respond(200, b"ok")
                return
            if service.already_processed(event_id):
                logger.info("stripe webhook replay ignored id=%s", event_id)
                self._respond(200, b"ok")
                return
            # Fulfil BEFORE acknowledging. A crash between a 200 and the
            # activation would make Stripe consider the event delivered and
            # never retry, so the customer would have paid for nothing.
            # Fulfilment is a lock + dict write + fsync, well inside Stripe's
            # timeout; a failure returns 500 so Stripe retries later.
            try:
                result = billing.fulfill_checkout_session(
                    _ev(event, "data", "object"), service, event_id=event_id)
            except StateError as exc:
                logger.error("stripe fulfillment not persisted id=%s: %s", event_id, exc)
                self._respond(500)
                return
            except Exception as exc:
                logger.exception("stripe fulfillment failed id=%s", event_id)
                admin_alert(bot_app,
                            f"Stripe payment {event_id} failed to activate PRO: "
                            f"{exc} — check manually.")
                self._respond(500)
                return
            self._respond(200, b"ok")
            if result:
                chat_id, tier, until = result
                logger.info("stripe PRO activated chat=%s tier=%s id=%s",
                            chat_id, tier, event_id)
                notify_pro_active(bot_app, chat_id, tier, until)
            else:
                logger.warning("stripe event %s ignored: unpaid or bad metadata",
                               event_id)

        def log_request(self, code="-", size="-"):
            # Only failures are interesting; successes would drown the log.
            if str(code)[:1] in ("4", "5"):
                logger.warning("http %s %s -> %s", self.client_address[0],
                               self.requestline, code)

        def log_message(self, fmt, *args):
            pass

    return Handler


def start_webhook_server(service, bot_app):
    port = int(os.environ.get("PORT", "8080"))
    handler = build_handler(service, bot_app, config.stripe_webhook_secret(),
                            config.health_stale_s())
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    # threaded: a slow webhook must never block Fly's health probe
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def init_sentry():
    """Optional error reporting: on only when SENTRY_DSN is set."""
    dsn = config.sentry_dsn()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        sentry_sdk.init(
            dsn=dsn, send_default_pii=False, traces_sample_rate=0.0,
            integrations=[LoggingIntegration(level=logging.INFO,
                                             event_level=logging.ERROR)],
            release=os.environ.get("FLY_IMAGE_REF") or None,
            environment=os.environ.get("FLY_APP_NAME") or "local",
        )
        logger.info("sentry enabled")
    except Exception as exc:
        logger.warning("sentry init failed: %s", exc)


def check_optional_deps():
    """Fail at boot, not at the first payment, when a paid-path dependency
    is missing from the image."""
    missing = []
    if config.stripe_api_key() or config.stripe_webhook_secret():
        try:
            import stripe  # noqa: F401
        except Exception as exc:
            missing.append(f"stripe ({exc})")
    if importlib.util.find_spec("ccxt") is None:
        missing.append("ccxt")
    if missing:
        logger.critical("required dependencies missing: %s", ", ".join(missing))
        sys.exit(2)


def main():
    config.load_dotenv()
    level = str(config.log_level()).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = "INFO"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs every request URL at INFO, and Telegram Bot API URLs embed
    # the bot token. Keep those lines out of the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    init_sentry()

    token = config.telegram_token()
    if not token:
        print("=" * 60)
        print("TELEGRAM_BOT_TOKEN is not set.")
        print("1. Copy .env.example to .env")
        print("2. Put your token from @BotFather into .env")
        print("3. Run: python main.py")
        print("=" * 60)
        sys.exit(1)
    check_optional_deps()

    hub = DataHub(allow_demo=config.allow_demo_data())
    service = Service(hub, config.state_file(),
                      pro_ids=config.pro_access_ids(),
                      admin_id=config.admin_id())
    bot = Bot(token, hub, service, demo_ok=config.allow_demo_data(),
              pay_config={"usdt_address": config.usdt_address(),
                          "admin_id": config.admin_id(),
                          "stripe_key": config.stripe_api_key(),
                          "bot_username": config.bot_username(),
                          "site_url": config.site_url(),
                          "site_key": config.site_key(),
                          "site_username_match": config.site_username_match()})
    service.on_alert = lambda text: admin_alert(bot.app, text)

    start_webhook_server(service, bot.app)
    logger.info("starting EzyAi ...")

    async def _remember_loop(app):
        # webhook thread needs a handle on the running loop
        global _EZY_LOOP
        _EZY_LOOP = asyncio.get_running_loop()
        await bot.post_init_hook(app)
        if service.load_error:
            admin_alert(app, f"Bot started with saves DISABLED: {service.load_error}")

    bot.app.post_init = _remember_loop
    # drop_pending_updates stays False: a Stars payment confirmation queued
    # during a restart must still be delivered, or the user pays for nothing
    bot.app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()

import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import config
from app.bot import Bot
from app.data.provider import DataHub
from app.signals.scheduler import Service


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

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
    logger = logging.getLogger("ezyai")

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
    service = Service(hub, config.state_file())
    bot = Bot(token, hub, service, demo_ok=config.allow_demo_data())

    start_health_server()
    logger.info("starting EzyAi ...")
    bot.app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
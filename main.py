import logging
import sys

from app import config
from app.bot import Bot
from app.data.provider import DataHub
from app.signals.scheduler import Service


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

    logger.info("starting EzyAi ...")
    bot.app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
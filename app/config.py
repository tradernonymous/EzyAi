import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=None):
    path = Path(path or PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(key, default=None):
    return os.environ.get(key, default)


def telegram_token():
    return env("TELEGRAM_BOT_TOKEN", "")


def allow_demo_data():
    return env("EZYAI_DEMO_DATA", "false").lower() in ("1", "true", "yes", "on")


def state_file():
    return Path(env("EZYAI_STATE_FILE", str(PROJECT_ROOT / "state.json")))


def log_level():
    return env("EZYAI_LOG_LEVEL", "INFO")


def stripe_api_key():
    return env("STRIPE_API_KEY", "")


def stripe_webhook_secret():
    return env("STRIPE_WEBHOOK_SECRET", "")


def bot_username():
    return env("BOT_USERNAME", "ezytradeai_bot")


def usdt_address():
    return env("USDT_ADDRESS", "")


def admin_id():
    raw = env("ADMIN_TELEGRAM_ID", "")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def pro_access_ids():
    """Team comp list: always-PRO chats (admin + owner monitoring).
    Comma-separated IDs, editable anytime via env/secret (no redeploy)."""
    out = []
    for part in env("PRO_ACCESS_IDS", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out)
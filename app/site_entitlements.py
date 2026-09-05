"""PRO bought on the website (printezy.money, Stripe) is claimed here.

The site records one entitlement per paid EzyAi PRO checkout, keyed on the
Telegram *username* typed at checkout (it never sees a chat id). This module
pulls those rows over the site's bearer-key API, activates PRO locally and
posts the claim back so the row is never handed out twice.

Endpoint contract (see src/routes/api/public/ezyai/entitlements.ts):
  GET  {site}/api/public/ezyai/entitlements?username=<handle>  -> {entitlements: [...]}
  GET  {site}/api/public/ezyai/entitlements                    -> all unclaimed (sweep)
  POST {site}/api/public/ezyai/entitlements  {id, telegram_id} -> {claimed: bool}
Each row: {id, sku, months, telegram_username, stripe_session_id, ...}.
"""

import logging

logger = logging.getLogger(__name__)

PATH = "/api/public/ezyai/entitlements"


def normalize_handle(username):
    if not username:
        return None
    handle = str(username).strip().lstrip("@").lower()
    return handle or None


def event_id_for(row):
    """Idempotency key shared with the Stripe/Stars paths in Service."""
    return f"site:{row['stripe_session_id']}"


class SiteClient:
    """Thin requests wrapper; disabled (no-op) until EZYAI_SITE_KEY is set."""

    def __init__(self, base_url, key, timeout=8):
        self.base_url = (base_url or "").rstrip("/")
        self.key = key or ""
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.base_url and self.key)

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}

    def fetch(self, username=None):
        """Unclaimed rows for one handle, or every handle when username is None."""
        if not self.enabled:
            return []
        import requests  # lazy: keeps tests import-light
        params = {}
        handle = normalize_handle(username)
        if username is not None:
            if not handle:
                return []
            params["username"] = handle
        resp = requests.get(self.base_url + PATH, params=params,
                            headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        rows = (resp.json() or {}).get("entitlements") or []
        return [r for r in rows if _valid(r)]

    def claim(self, ent_id, chat_id):
        if not self.enabled:
            return False
        import requests
        resp = requests.post(self.base_url + PATH,
                             json={"id": ent_id, "telegram_id": int(chat_id)},
                             headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return bool((resp.json() or {}).get("claimed"))


def _valid(row):
    try:
        return (isinstance(row, dict) and row.get("id") and row.get("stripe_session_id")
                and int(row.get("months", 0)) > 0)
    except (TypeError, ValueError):
        return False


def redeem(client, service, chat_id, rows):
    """Activate PRO for each row and claim it on the site.

    Returns [(row, until)] for rows that granted time. Activation is
    idempotent on the Stripe session id, so a row the site failed to mark
    claimed (network blip) can be redeemed again without stacking months.
    """
    granted = []
    for row in rows:
        ev = event_id_for(row)
        already = service.already_processed(ev)
        until = service.activate_pro(chat_id, int(row["months"]), event_id=ev)
        try:
            client.claim(row["id"], chat_id)
        except Exception as exc:  # claim retried on the next fetch
            logger.warning("site claim failed id=%s: %s", row.get("id"), exc)
        if not already:
            granted.append((row, until))
    return granted


def redeem_for_user(client, service, chat_id, username):
    """/start, /plans, /account, PRO gates: pull this handle's rows."""
    if not client.enabled:
        return []
    handle = normalize_handle(username)
    if not handle:
        return []
    try:
        rows = client.fetch(handle)
    except Exception as exc:
        logger.warning("site entitlement fetch failed handle=%s: %s", handle, exc)
        return []
    return redeem(client, service, chat_id, rows)


def sweep(client, service):
    """Periodic: claim every unclaimed row whose handle we have seen before,
    so a buyer who already uses the bot gets PRO without touching it.
    Returns [(chat_id, row, until)]."""
    if not client.enabled:
        return []
    try:
        rows = client.fetch(None)
    except Exception as exc:
        logger.warning("site entitlement sweep failed: %s", exc)
        return []
    out = []
    for row in rows:
        chat_id = service.chat_for_username(row.get("telegram_username"))
        if chat_id is None:
            continue
        for r, until in redeem(client, service, chat_id, [row]):
            out.append((chat_id, r, until))
    return out

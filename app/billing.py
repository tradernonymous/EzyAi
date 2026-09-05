"""Pure billing helpers: payloads, tiers, formatting (offline-testable).

Invoice payload format: "ezypro:<tier>:<method>:<chat_id>".
"""

from . import constants

METHODS = ("stars", "card", "usdt")


def tier(tier_id):
    return constants.PLANS.get(tier_id)


def encode_payload(tier_id, method, chat_id):
    return f"ezypro:{tier_id}:{method}:{chat_id}"


def decode_payload(payload):
    try:
        kind, tier_id, method, chat = str(payload).split(":", 3)
    except ValueError:
        return None
    if kind != "ezypro" or tier_id not in constants.PLANS or method not in METHODS:
        return None
    try:
        chat_id = int(chat)
    except ValueError:
        return None
    return {"tier": tier_id, "method": method, "chat_id": chat_id}


def tier_line(tier_id):
    p = constants.PLANS[tier_id]
    line = f"{p['label']} \u2014 ${p['usd']:.2f}"
    if p["save"]:
        line += f" ({p['save']})"
    if p["badge"]:
        line += f" \u2b50 {p['badge']}"
    return line


def duration_days(tier_id):
    return constants.PLANS[tier_id]["months"] * 30


def stripe_session_params(tier_id, chat_id, success_url, cancel_url):
    """Checkout Session kwargs with dynamic price_data (no preset Price ID)."""
    plan = constants.PLANS[tier_id]
    return {
        "mode": "payment",
        "line_items": [{
            "price_data": {
                "currency": "usd",
                "unit_amount": int(round(plan["usd"] * 100)),
                "product_data": {"name": f"EzyAi PRO \u2014 {plan['label']}"},
            },
            "quantity": 1,
        }],
        "metadata": {"order": encode_payload(tier_id, "card", chat_id)},
        "success_url": success_url,
        "cancel_url": cancel_url,
    }


def verify_stripe_event(payload_bytes, sig_header, secret):
    """Return the Stripe event, or None when the signature is invalid."""
    try:
        import stripe  # lazy: billing stays import-light
        return stripe.Webhook.construct_event(payload_bytes, sig_header, secret)
    except Exception:
        return None


def fulfill_checkout_session(session, service, event_id=None):
    """Activate PRO from a checkout.session.completed object.

    session may be a stripe object or a plain dict (tests). event_id is the
    Stripe event id, passed through for idempotency so retried deliveries
    never grant a second period. Returns (chat_id, tier_id, until) or None.
    """
    if isinstance(session, dict):
        paid = session.get("payment_status")
        meta = session.get("metadata") or {}
    else:
        paid = getattr(session, "payment_status", None)
        try:
            meta = session.get("metadata") or {}
        except Exception:
            meta = {}
    if paid != "paid":
        return None
    order = meta.get("order", "") if isinstance(meta, dict) else ""
    info = decode_payload(order)
    if not info:
        return None
    months = constants.PLANS[info["tier"]]["months"]
    until = service.activate_pro(info["chat_id"], months, event_id=event_id)
    return info["chat_id"], info["tier"], until

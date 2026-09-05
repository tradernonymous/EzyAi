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

"""Pure Telegram UI helpers: keyboards, callback data, menu routing.

Everything here is free of network/clients so the interaction layer is
unit-testable offline (tests/test_interactive.py). bot.py only wires
these builders to handlers.

Callback scheme (all prefixed ``ezy:``, <=64 bytes):
  ezy:menu:<flow>[:<pair>]   jump to a flow, optional pair preset
  ezy:ppage:<flow>:<n>       pair-picker page
  ezy:pick:<flow>:<pair>     pair chosen (or "custom")
  ezy:style:<flow>:<style>   style chosen
  ezy:mode:<flow>:<mode>     mode chosen
  ezy:back:<flow>:<step>     back to pair|style|mode step
  ezy:watch_go               confirm adding the watch in flow state
  ezy:auto_go                confirm starting autopilot in flow state
  ezy:auto_stop / ezy:auto_stop_yes
  ezy:unwatch:<pair>         remove one watch
  ezy:dash                   refresh dashboard
  ezy:cancel                 abort flow
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from . import constants

MENU_ANALYZE = "\U0001f4ca Analyze"
MENU_WATCH = "\U0001f440 Watchlist"
MENU_AUTO = "\U0001f916 Autopilot"
MENU_QUOTE = "\U0001f4b9 Quote"
MENU_FUND = "\U0001f4da Fundamentals"
MENU_DASH = "\U0001f4cb Dashboard"
MENU_HELP = "\u2753 Help"

MENU_LABELS = (MENU_ANALYZE, MENU_WATCH, MENU_AUTO, MENU_QUOTE,
               MENU_FUND, MENU_DASH, MENU_HELP)

MENU_ROUTE = {
    MENU_ANALYZE: "analyze",
    MENU_WATCH: "watches",
    MENU_AUTO: "auto",
    MENU_QUOTE: "quote",
    MENU_FUND: "fund",
    MENU_DASH: "dash",
    MENU_HELP: "help",
}

# (command, description) for the BotFather menu button.
COMMANDS = (
    ("analyze", "Guided market analysis with entry/exit"),
    ("watch", "Live alerts for a pair"),
    ("watches", "Manage your watchlist"),
    ("quote", "Quick live price"),
    ("fundamentals", "Fundamentals + news + links"),
    ("autopilot", "Random-pair auto signals"),
    ("dashboard", "Status overview + shortcuts"),
    ("help", "How to use EzyAi"),
)

FLOWS_WITH_PAIR = ("analyze", "watch", "fund", "quote")
FLOWS_WITH_STYLE_MODE = ("analyze", "watch", "auto")
PAIR_PAGE_SIZE = 9

STYLE_EMOJI = {"scalping": "\u26a1", "intraday": "\U0001f4c8", "swing": "\U0001f558"}
MODE_EMOJI = {"safe": "\U0001f6e1", "normal": "\u2696", "aggressive": "\U0001f525"}

STYLE_HINT = {
    "scalping": "minutes, 5m candles",
    "intraday": "hours, 15m candles",
    "swing": "days, daily candles",
}
MODE_HINT = {
    "safe": "0.5% risk, fewer alerts",
    "normal": "1% risk, balanced",
    "aggressive": "2% risk, more alerts",
}


def route_menu(text):
    """Menu button label -> flow name (or None)."""
    return MENU_ROUTE.get((text or "").strip())


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU_ANALYZE, MENU_QUOTE],
         [MENU_WATCH, MENU_AUTO],
         [MENU_FUND, MENU_DASH],
         [MENU_HELP]],
        resize_keyboard=True,
        is_persistent=True,
    )


# -- callback data builders -------------------------------------------------

def cb_menu(flow, pair=None):
    return f"ezy:menu:{flow}:{pair}" if pair else f"ezy:menu:{flow}"


def cb_ppage(flow, n):
    return f"ezy:ppage:{flow}:{n}"


def cb_pick(flow, pair):
    return f"ezy:pick:{flow}:{pair}"


def cb_style(flow, style):
    return f"ezy:style:{flow}:{style}"


def cb_mode(flow, mode):
    return f"ezy:mode:{flow}:{mode}"


def cb_back(flow, step):
    return f"ezy:back:{flow}:{step}"


def cb_unwatch(pair):
    return f"ezy:unwatch:{pair}"


def parse_callback(data):
    """Parse callback data -> dict. Unknown -> {"a": "unknown"}."""
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "ezy":
        return {"a": "unknown"}
    kind = parts[1]
    if kind == "menu":
        return {"a": "menu", "flow": parts[2] if len(parts) > 2 else "",
                "pair": parts[3] if len(parts) > 3 else None}
    if kind == "ppage" and len(parts) == 4:
        try:
            return {"a": "ppage", "flow": parts[2], "page": int(parts[3])}
        except ValueError:
            return {"a": "unknown"}
    if kind == "pick" and len(parts) == 4:
        return {"a": "pick", "flow": parts[2], "pair": parts[3]}
    if kind == "style" and len(parts) == 4:
        return {"a": "style", "flow": parts[2], "style": parts[3]}
    if kind == "mode" and len(parts) == 4:
        return {"a": "mode", "flow": parts[2], "mode": parts[3]}
    if kind == "back" and len(parts) == 4:
        return {"a": "back", "flow": parts[2], "step": parts[3]}
    if kind == "unwatch" and len(parts) == 3:
        return {"a": "unwatch", "pair": parts[2]}
    if data in ("ezy:cancel", "ezy:watch_go", "ezy:auto_go",
                "ezy:auto_stop", "ezy:auto_stop_yes", "ezy:dash"):
        return {"a": parts[1]}
    return {"a": "unknown"}


# -- inline keyboards -------------------------------------------------------

def pair_keyboard(flow, page=0, pool=None):
    pool = list(pool if pool is not None else constants.ALL_UNIVERSE)
    size = PAIR_PAGE_SIZE
    pages = max(1, (len(pool) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    chunk = pool[page * size:page * size + size]
    rows = []
    for i in range(0, len(chunk), 3):
        rows.append([InlineKeyboardButton(p, callback_data=cb_pick(flow, p))
                     for p in chunk[i:i + 3]])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("\u276e Prev", callback_data=cb_ppage(flow, page - 1)))
    nav.append(InlineKeyboardButton("\U0001f50d Custom", callback_data=cb_pick(flow, "custom")))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(f"Next \u276f {page + 1}/{pages}",
                                        callback_data=cb_ppage(flow, page + 1)))
    rows.append(nav)
    rows.append([InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel")])
    return InlineKeyboardMarkup(rows)


def style_keyboard(flow):
    rows = [[InlineKeyboardButton(
        f"{STYLE_EMOJI[s]} {s.capitalize()} ({STYLE_HINT[s]})",
        callback_data=cb_style(flow, s))] for s in constants.STYLES]
    rows.append([InlineKeyboardButton("\u2039 Back", callback_data=cb_back(flow, "pair")),
                 InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel")])
    return InlineKeyboardMarkup(rows)


def mode_keyboard(flow):
    rows = [[InlineKeyboardButton(
        f"{MODE_EMOJI[m]} {m.capitalize()} ({MODE_HINT[m]})",
        callback_data=cb_mode(flow, m))] for m in constants.MODES]
    rows.append([InlineKeyboardButton("\u2039 Back", callback_data=cb_back(flow, "style")),
                 InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(go_cb, back_cb, go_label="\u2705 Confirm"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(go_label, callback_data=go_cb),
        InlineKeyboardButton("\u2039 Back", callback_data=back_cb),
        InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel"),
    ]])


def followup_keyboard(pair):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001f514 Watch", callback_data=cb_menu("watch", pair)),
        InlineKeyboardButton("\U0001f4da Fund.", callback_data=cb_menu("fund", pair)),
        InlineKeyboardButton("\U0001f4b9 Quote", callback_data=cb_menu("quote", pair)),
    ]])


def watches_keyboard(rows):
    kb = []
    for w in rows:
        kb.append([InlineKeyboardButton(f"\u274c {w['pair']} ({w['style']}/{w['mode']})",
                                        callback_data=cb_unwatch(w["pair"]))])
    kb.append([InlineKeyboardButton("\u2795 Add watch", callback_data=cb_menu("watch")),
               InlineKeyboardButton("\U0001f504 Refresh", callback_data="ezy:dash")])
    return InlineKeyboardMarkup(kb)


def dash_keyboard(auto_on):
    auto_label = "\u23f9 Stop autopilot" if auto_on else "\U0001f916 Start autopilot"
    auto_cb = "ezy:auto_stop" if auto_on else cb_menu("auto")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4ca Analyze", callback_data=cb_menu("analyze")),
         InlineKeyboardButton("\U0001f4b9 Quote", callback_data=cb_menu("quote"))],
        [InlineKeyboardButton("\U0001f440 Watchlist", callback_data=cb_menu("watches")),
         InlineKeyboardButton(auto_label, callback_data=auto_cb)],
        [InlineKeyboardButton("\U0001f4da Fundamentals", callback_data=cb_menu("fund")),
         InlineKeyboardButton("\u2753 Help", callback_data=cb_menu("help"))],
    ])


def help_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4ca Analyze a market", callback_data=cb_menu("analyze")),
         InlineKeyboardButton("\U0001f514 Watch a pair", callback_data=cb_menu("watch"))],
        [InlineKeyboardButton("\U0001f916 Autopilot", callback_data=cb_menu("auto")),
         InlineKeyboardButton("\U0001f4cb Dashboard", callback_data=cb_menu("dash"))],
    ])


def retry_pair_keyboard(flow):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001f50d Choose again", callback_data=cb_menu(flow)),
        InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel"),
    ]])


# -- flow prompt texts ------------------------------------------------------

FLOW_TITLE = {
    "analyze": "\U0001f4ca <b>Analyze</b>",
    "watch": "\U0001f514 <b>New watch</b>",
    "fund": "\U0001f4da <b>Fundamentals</b>",
    "quote": "\U0001f4b9 <b>Quote</b>",
    "auto": "\U0001f916 <b>Autopilot</b>",
}


def prompt_pair(flow):
    return (f"{FLOW_TITLE[flow]} \u2014 step 1/3\nChoose a market:",
            pair_keyboard(flow))


def prompt_style(flow, pair):
    return (f"{FLOW_TITLE[flow]} \u2014 step 2/3\n<b>{pair}</b>: pick a style:",
            style_keyboard(flow))


def prompt_mode(flow, pair, style):
    return (f"{FLOW_TITLE[flow]} \u2014 step 3/3\n<b>{pair}</b> \u00b7 {style}: pick risk mode:",
            mode_keyboard(flow))

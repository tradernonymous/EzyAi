import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import constants
from . import ui
from .analysis import sentiment as _sent
from .formatting import message as msg
from .fundamentals import Fundamentals
from .signals import engine as signal_engine

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, token, hub, service, demo_ok=False):
        self.hub = hub
        self.service = service
        self.fund = Fundamentals()
        self.demo_ok = demo_ok
        self.app = Application.builder().token(token).build()
        self._register()

    def _register(self):
        a = self.app
        a.post_init = self._post_init
        a.add_handler(CommandHandler("start", self.cmd_start))
        a.add_handler(CommandHandler("help", self.cmd_help))
        a.add_handler(CommandHandler("dashboard", self.cmd_dashboard))
        a.add_handler(CommandHandler("analyze", self.cmd_analyze))
        a.add_handler(CommandHandler("watch", self.cmd_watch))
        a.add_handler(CommandHandler("watches", self.cmd_watches))
        a.add_handler(CommandHandler("unwatch", self.cmd_unwatch))
        a.add_handler(CommandHandler("fundamentals", self.cmd_fundamentals))
        a.add_handler(CommandHandler("autopilot", self.cmd_autopilot))
        a.add_handler(CommandHandler("stopautopilot", self.cmd_stop_autopilot))
        a.add_handler(CommandHandler("quote", self.cmd_quote))
        a.add_handler(CallbackQueryHandler(self.cb_flow, pattern=r"^ezy:"))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.job_queue.run_repeating(self._job, interval=30, first=30)

    async def _post_init(self, app):
        try:
            from telegram import BotCommand
            await app.bot.set_my_commands(
                [BotCommand(cmd, desc) for cmd, desc in ui.COMMANDS])
        except Exception as exc:
            logger.warning("set_my_commands failed: %s", exc)

    async def _job(self, context):
        async def send(chat_id, signal, source="watch"):
            text = msg.signal_message(signal, source=source)
            try:
                await self.app.bot.send_message(
                    chat_id, text, parse_mode=ParseMode.HTML,
                    reply_markup=ui.followup_keyboard(signal["pair"]))
            except Exception as exc:
                logger.warning("deliver failed chat=%s: %s", chat_id, exc)
        try:
            await self.service.tick(send)
        except Exception as exc:
            logger.warning("tick failed: %s", exc)

    async def _reply(self, update, text, **kw):
        if not text:
            return
        # Actively keep the (removed) bottom reply-keyboard hidden: clients
        # cache the last keyboard until told otherwise, so every plain
        # message re-asserts its removal. Messages with inline buttons
        # can't carry a second markup and are unaffected either way.
        kw.setdefault("reply_markup", ReplyKeyboardRemove())
        r = await update.effective_chat.send_message(
            text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, **kw)
        return r

    async def _edit_or_send(self, query, text, reply_markup=None):
        try:
            await query.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True, reply_markup=reply_markup)
        except Exception:
            await query.message.reply_text(
                text, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True, reply_markup=reply_markup)

    @staticmethod
    def _flow_key():
        return "ezyai_flow"

    def _get_flow(self, ctx):
        return ctx.user_data.get(self._flow_key(), {})

    async def _typing(self, update, ctx):
        try:
            await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
        except Exception:
            pass

    # -- top-level commands -------------------------------------------------

    async def cmd_start(self, update, ctx):
        ctx.user_data.pop(self._flow_key(), None)
        await self._reply(
            update,
            "Welcome to <b>EzyAi</b> \u2014 live market analysis, "
            "alerts and auto signals.",
            reply_markup=ui.help_keyboard())

    async def cmd_help(self, update, ctx):
        await self._reply(update, msg.help_text(),
                          reply_markup=ui.help_keyboard())

    async def cmd_dashboard(self, update, ctx):
        await self._send_dashboard(update.effective_chat.id, update, ctx)

    async def _send_dashboard(self, chat_id, update, ctx):
        watches = self.service.list_watches(chat_id)
        pilot = self.service.autopilots.get(str(chat_id))
        text = msg.dashboard_view(watches, pilot, self.hub.mode)
        kb = ui.refresh_keyboard()
        if update.callback_query is not None:
            await self._edit_or_send(update.callback_query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    # -- analyze ------------------------------------------------------------

    async def cmd_analyze(self, update, ctx):
        args = ctx.args
        if args:
            pair = args[0].upper()
            if not self.hub.resolve_loose(pair):
                await self._reply(
                    update, f"Unknown symbol <b>{pair}</b>.",
                    reply_markup=ui.retry_pair_keyboard("analyze"))
                return
            ctx.user_data[self._flow_key()] = {"flow": "analyze", "pair": pair}
            text, kb = ui.prompt_style("analyze", pair)
            await self._reply(update, text, reply_markup=kb)
            return
        await self._start_flow(update, ctx, "analyze")

    async def _run_analyze(self, update, ctx, pair, style, mode, query=None):
        await self._typing(update, ctx)
        status = (f"\U0001f4c8 Scanning <b>{pair}</b> \u00b7 {style}/{mode}\u2026")
        if query is not None:
            await self._edit_or_send(query, status)
        else:
            await self._reply(update, status)
            try:
                headlines = self.fund.news(constants.base_asset(pair), limit=5)
            except Exception:
                headlines = []
        try:
            score = _sent.score_headlines(headlines)
        except Exception:
            score = None
        try:
            analysis = signal_engine.quick_analyze(
                pair, style, mode, self.hub, sentiment=score)[0]
        except Exception as exc:
            target = query.message if query is not None else None
            text = f"Analysis failed: {exc}"
            kb = ui.retry_pair_keyboard("analyze")
            if target is not None:
                await target.reply_text(text, parse_mode=ParseMode.HTML,
                                        reply_markup=kb)
            else:
                await self._reply(update, text, reply_markup=kb)
            return
        chat = update.effective_chat
        await chat.send_message(msg.analysis_report(analysis),
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=ui.followup_keyboard(pair))

    # -- quote --------------------------------------------------------------

    async def cmd_quote(self, update, ctx):
        if not ctx.args:
            await self._start_flow(update, ctx, "quote")
            return
        await self._send_quote(update, ctx.args[0].upper(), update, ctx)

    async def _send_quote(self, update, pair, ctx, query=None):
        await self._typing(update, ctx)
        try:
            tick = self.hub.fetch_ticker(pair)
            tick["mode"] = "demo" if self.hub.mode == "demo" else "live"
        except Exception:
            text = f"Could not fetch <b>{pair}</b>."
            kb = ui.retry_pair_keyboard("quote")
            if query is not None:
                await self._edit_or_send(query, text, kb)
            else:
                await self._reply(update, text, reply_markup=kb)
            return
        chat = update.effective_chat
        await chat.send_message(msg.quote_report(pair, tick),
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=ui.followup_keyboard(pair))

    # -- fundamentals -------------------------------------------------------

    async def cmd_fundamentals(self, update, ctx):
        if not ctx.args:
            await self._start_flow(update, ctx, "fund")
            return
        await self._send_fundamentals(update, ctx.args[0].upper(), update, ctx)

    def _fundamentals_text(self, pair):
        resolved = self.hub.resolve_loose(pair)
        if not resolved:
            return None
        kind, sym = resolved
        data = None
        try:
            if kind == constants.KIND_CRYPTO:
                data = self.fund.crypto(pair)
            elif kind == constants.KIND_STOCK:
                data = self.fund.stock(pair)
            elif kind == constants.KIND_FOREX:
                data = self.fund.forex(pair)
            elif kind == constants.KIND_CFD:
                data = self.fund.cfd(sym, tag=pair)
        except Exception:
            data = None
        text = msg.fundamentals_report(kind, pair, data, self.hub.mode)
        links = self.fund.links(kind, pair)
        try:
            news = self.fund.news(constants.base_asset(pair), limit=4)
        except Exception:
            news = []
        text += msg.related_reading(kind, pair, links, news)
        return text

    async def _send_fundamentals(self, update, pair, ctx, query=None):
        await self._typing(update, ctx)
        try:
            text = self._fundamentals_text(pair)
        except Exception:
            text = None
        if not text:
            kb = ui.retry_pair_keyboard("fund")
            if query is not None:
                await self._edit_or_send(query, f"Unknown symbol <b>{pair}</b>.", kb)
            else:
                await self._reply(update, f"Unknown symbol <b>{pair}</b>.",
                                  reply_markup=kb)
            return
        chat = update.effective_chat
        await chat.send_message(text, parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=ui.followup_keyboard(pair))

    # -- watch --------------------------------------------------------------

    async def cmd_watch(self, update, ctx):
        args = ctx.args
        if len(args) < 3:
            await self._start_flow(update, ctx, "watch")
            return
        pair, style, mode = args[0].upper(), args[1].lower(), args[2].lower()
        err = self._validate_pair_style_mode(pair, style, mode)
        if err:
            await self._reply(update, err,
                              reply_markup=ui.retry_pair_keyboard("watch"))
            return
        self._add_watch(update, pair, style, mode)
        await self._reply(update, msg.watch_added_text(pair, style, mode),
                          reply_markup=ui.watches_keyboard(
                              self.service.list_watches(update.effective_chat.id)))

    def _validate_pair_style_mode(self, pair, style, mode):
        if style not in constants.STYLES:
            return (f"Unknown style <b>{style}</b>. "
                    "Use the buttons: scalping, intraday or swing.")
        if mode not in constants.MODES:
            return (f"Unknown mode <b>{mode}</b>. "
                    "Use the buttons: safe, normal or aggressive.")
        if not self.hub.resolve_loose(pair):
            return f"Unknown symbol <b>{pair}</b>."
        return None

    def _add_watch(self, update, pair, style, mode):
        return self.service.add_watch(update.effective_chat.id, pair, style, mode)

    async def cmd_watches(self, update, ctx):
        rows = self.service.list_watches(update.effective_chat.id)
        if not rows:
            await self._reply(
                update, "No active watches yet.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("\u2795 Add watch",
                                         callback_data=ui.cb_menu("watch"))]]))
            return
        await self._reply(update, msg.watch_list(rows),
                          reply_markup=ui.watches_keyboard(rows))

    async def _send_watches(self, query, chat_id):
        rows = self.service.list_watches(chat_id)
        if not rows:
            await self._edit_or_send(
                query, "No active watches yet.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("\u2795 Add watch",
                                         callback_data=ui.cb_menu("watch"))]]))
            return
        await self._edit_or_send(query, msg.watch_list(rows),
                                 ui.watches_keyboard(rows))

    async def cmd_unwatch(self, update, ctx):
        if not ctx.args:
            await self.cmd_watches(update, ctx)
            return
        ok = self.service.remove_watch(update.effective_chat.id, ctx.args[0].upper())
        await self._reply(update, "Watch removed." if ok else "No such watch.")
        if ok:
            await self.cmd_watches(update, ctx)

    # -- autopilot ----------------------------------------------------------

    async def cmd_autopilot(self, update, ctx):
        args = ctx.args
        if len(args) < 2:
            await self._auto_entry(update, ctx)
            return
        style, mode = args[0].lower(), args[1].lower()
        if style not in constants.STYLES or mode not in constants.MODES:
            await self._reply(update, "Unknown style/mode \u2014 pick from the buttons:",
                              reply_markup=ui.style_keyboard("auto"))
            return
        self.service.start_autopilot(update.effective_chat.id, style, mode)
        await self._reply(update, msg.auto_started_text(style, mode))

    async def _auto_entry(self, update, ctx, query=None):
        """Autopilot button: running -> status + stop, else setup flow."""
        pilot = self.service.autopilots.get(str(update.effective_chat.id))
        if pilot is not None:
            text = (f"\U0001f916 Autopilot is <b>ON</b> \u00b7 {pilot.style}/{pilot.mode}\n"
                    "Scanning random pairs within your daily limit.")
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u23f9 Stop autopilot",
                                     callback_data="ezy:auto_stop"),
                InlineKeyboardButton("\U0001f3e0 Menu", callback_data=ui.cb_menu("dash")),
            ]])
            if query is not None:
                await self._edit_or_send(query, text, kb)
            else:
                await self._reply(update, text, reply_markup=kb)
            return
        ctx.user_data[self._flow_key()] = {"flow": "auto", "page": 0}
        text = f"{ui.FLOW_TITLE['auto']} \u2014 step 1/2\nPick a style:"
        kb = ui.style_keyboard("auto")
        if query is not None:
            await self._edit_or_send(query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    async def cmd_stop_autopilot(self, update, ctx):
        pilot = self.service.autopilots.get(str(update.effective_chat.id))
        if pilot is None:
            await self._reply(update, "No autopilot running.")
            return
        await self._reply(
            update,
            f"\U0001f916 Stop autopilot ({pilot.style}/{pilot.mode})?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("\u23f9 Yes, stop", callback_data="ezy:auto_stop_yes"),
                InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel"),
            ]]))

    # -- flow engine --------------------------------------------------------

    async def _start_flow(self, update, ctx, flow, pair=None):
        ctx.user_data[self._flow_key()] = {"flow": flow, "page": 0}
        if pair:
            ctx.user_data[self._flow_key()]["pair"] = pair
            await self._after_pair(update, ctx, flow, pair)
            return
        if flow == "auto":
            await self._reply(update, f"{ui.FLOW_TITLE['auto']} \u2014 step 1/2\nPick a style:",
                              reply_markup=ui.style_keyboard(flow))
            return
        if flow == "watches":
            await self.cmd_watches(update, ctx)
            return
        if flow == "dash":
            await self.cmd_dashboard(update, ctx)
            return
        if flow == "help":
            await self.cmd_help(update, ctx)
            return
        text, kb = ui.prompt_pair(flow)
        await self._reply(update, text, reply_markup=kb)

    async def _after_pair(self, update, ctx, flow, pair, query=None):
        """Pair known: route to style step or run directly."""
        if flow in ("analyze", "watch"):
            text, kb = ui.prompt_style(flow, pair)
            if query is not None:
                await self._edit_or_send(query, text, kb)
            else:
                await self._reply(update, text, reply_markup=kb)
        elif flow == "fund":
            ctx.user_data.pop(self._flow_key(), None)
            await self._send_fundamentals(update, pair, ctx, query)
        elif flow == "quote":
            ctx.user_data.pop(self._flow_key(), None)
            await self._send_quote(update, pair, ctx, query)

    async def cb_flow(self, update, ctx):
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        cb = ui.parse_callback(query.data)
        flow = self._get_flow(ctx)
        action = cb["a"]

        if action == "unknown":
            return

        if action == "cancel":
            ctx.user_data.pop(self._flow_key(), None)
            await self._edit_or_send(query, "Cancelled. Tap a button below to start again.")
            return

        if action == "menu":
            ctx.user_data.pop(self._flow_key(), None)
            target, pair = cb["flow"], cb.get("pair")
            if target == "dash":
                await self._send_dashboard(chat_id, update, ctx)
            elif target == "watches":
                await self._send_watches(query, chat_id)
            elif target == "help":
                await self._edit_or_send(query, msg.help_text(), ui.help_keyboard())
            elif target in ("analyze", "watch", "fund", "quote"):
                ctx.user_data[self._flow_key()] = {"flow": target, "page": 0}
                if pair:
                    if not self.hub.resolve_loose(pair):
                        await self._edit_or_send(
                            query, f"Unknown symbol <b>{pair}</b>.",
                            ui.retry_pair_keyboard(target))
                        return
                    ctx.user_data[self._flow_key()]["pair"] = pair
                    await self._after_pair(update, ctx, target, pair, query)
                else:
                    text, kb = ui.prompt_pair(target)
                    await self._edit_or_send(query, text, kb)
            elif target == "auto":
                await self._auto_entry(update, ctx, query)
            return

        if action == "ppage":
            flow_name, page = cb["flow"], cb["page"]
            ctx.user_data[self._flow_key()] = {"flow": flow_name, "page": page,
                                               **{k: v for k, v in flow.items()
                                                  if k in ("pair", "style")}}
            try:
                text, _ = ui.prompt_pair(flow_name)
                await query.edit_message_text(
                    text, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=ui.pair_keyboard(flow_name, page))
            except Exception:
                pass
            return

        if action == "pick":
            flow_name, pair = cb["flow"], cb["pair"]
            ctx.user_data[self._flow_key()] = {"flow": flow_name, "page": 0}
            if pair == "custom":
                ctx.user_data[self._flow_key()]["step"] = "custom_pair"
                await self._edit_or_send(
                    query, "Send the symbol (e.g. BTCUSD, EURUSD, AAPL):",
                    ui.custom_pair_keyboard(flow_name))
                return
            if not self.hub.resolve_loose(pair):
                await self._edit_or_send(query, f"Unknown symbol <b>{pair}</b>.",
                                         ui.retry_pair_keyboard(flow_name))
                return
            ctx.user_data[self._flow_key()]["pair"] = pair
            await self._after_pair(update, ctx, flow_name, pair, query)
            return

        if action == "style":
            flow_name, style = cb["flow"], cb["style"]
            pair = flow.get("pair")
            if style not in constants.STYLES or not pair:
                await self._restart(update, ctx, flow_name, query)
                return
            flow["style"] = style
            if flow_name == "auto":
                text = (f"{ui.FLOW_TITLE['auto']} \u2014 step 2/2\n"
                        f"{style}: pick risk mode:")
                await self._edit_or_send(query, text, ui.mode_keyboard(flow_name))
            else:
                text, kb = ui.prompt_mode(flow_name, pair, style)
                await self._edit_or_send(query, text, kb)
            return

        if action == "mode":
            flow_name, mode = cb["flow"], cb["mode"]
            pair, style = flow.get("pair"), flow.get("style")
            if mode not in constants.MODES:
                await self._restart(update, ctx, flow_name, query)
                return
            flow["mode"] = mode
            if flow_name == "analyze":
                if not pair or not style:
                    await self._restart(update, ctx, flow_name, query)
                    return
                ctx.user_data.pop(self._flow_key(), None)
                await self._run_analyze(update, ctx, pair, style, mode, query)
            elif flow_name == "watch":
                if not pair or not style:
                    await self._restart(update, ctx, flow_name, query)
                    return
                sp = constants.STYLE_PROFILE[style]
                mp = constants.MODE_PROFILE[mode]
                await self._edit_or_send(
                    query,
                    msg.confirm_watch_text(pair, style, mode,
                                           sp["check_interval_s"], mp["rr"],
                                           mp["risk_frac"] * 100.0),
                    ui.confirm_keyboard("ezy:watch_go",
                                        ui.cb_back(flow_name, "mode"),
                                        "\U0001f514 Add watch"))
            elif flow_name == "auto":
                if not style:
                    await self._restart(update, ctx, flow_name, query)
                    return
                mp = constants.MODE_PROFILE[mode]
                await self._edit_or_send(
                    query, msg.confirm_auto_text(style, mode, mp["daily_limit"]),
                    ui.confirm_keyboard("ezy:auto_go",
                                        ui.cb_back(flow_name, "style"),
                                        "\U0001f916 Start"))
            return

        if action == "back":
            flow_name, step = cb["flow"], cb["step"]
            if step == "pair" and flow_name != "auto":
                text, _ = ui.prompt_pair(flow_name)
                await self._edit_or_send(
                    query, text, ui.pair_keyboard(flow_name, flow.get("page", 0)))
            elif step == "style" and flow.get("pair"):
                text, kb = ui.prompt_style(flow_name, flow["pair"])
                await self._edit_or_send(query, text, kb)
            elif step == "mode" and flow.get("pair") and flow.get("style"):
                text, kb = ui.prompt_mode(flow_name, flow["pair"], flow["style"])
                await self._edit_or_send(query, text, kb)
            else:
                await self._restart(update, ctx, flow_name, query)
            return

        if action == "watch_go":
            pair, style, mode = flow.get("pair"), flow.get("style"), flow.get("mode")
            if not pair or not style or not mode:
                await self._restart(update, ctx, "watch", query)
                return
            ctx.user_data.pop(self._flow_key(), None)
            self._add_watch(update, pair, style, mode)
            await self._edit_or_send(
                query, msg.watch_added_text(pair, style, mode),
                ui.watches_keyboard(self.service.list_watches(chat_id)))
            return

        if action == "auto_go":
            style, mode = flow.get("style"), flow.get("mode")
            if not style or not mode:
                await self._restart(update, ctx, "auto", query)
                return
            ctx.user_data.pop(self._flow_key(), None)
            self.service.start_autopilot(chat_id, style, mode)
            await self._edit_or_send(query, msg.auto_started_text(style, mode),
                                     ui.help_keyboard())
            return

        if action == "auto_stop":
            pilot = self.service.autopilots.get(str(chat_id))
            if pilot is None:
                await self._edit_or_send(query, "No autopilot running.",
                                         ui.refresh_keyboard())
                return
            await self._edit_or_send(
                query, f"\U0001f916 Stop autopilot ({pilot.style}/{pilot.mode})?",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("\u23f9 Yes, stop",
                                         callback_data="ezy:auto_stop_yes"),
                    InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel"),
                ]]))
            return

        if action == "auto_stop_yes":
            ok = self.service.stop_autopilot(chat_id)
            ctx.user_data.pop(self._flow_key(), None)
            await self._edit_or_send(
                query, "Autopilot stopped." if ok else "No autopilot running.",
                ui.help_keyboard())
            return

        if action == "unwatch":
            ok = self.service.remove_watch(chat_id, cb["pair"])
            await self._send_watches(query, chat_id)
            if not ok:
                await query.message.reply_text("No such watch.")
            return

        if action == "dash":
            await self._send_dashboard(chat_id, update, ctx)
            return

    async def _restart(self, update, ctx, flow_name, query):
        ctx.user_data.pop(self._flow_key(), None)
        await self._edit_or_send(
            query, "Let's start over \u2014 pick a flow:",
            ui.help_keyboard())

    async def on_text(self, update, ctx):
        text = (update.message.text or "").strip()
        routed = ui.route_menu(text)
        if routed:
            ctx.user_data.pop(self._flow_key(), None)
            if routed == "dash":
                await self.cmd_dashboard(update, ctx)
            elif routed == "watches":
                await self.cmd_watches(update, ctx)
            elif routed == "help":
                await self.cmd_help(update, ctx)
            elif routed == "auto":
                await self.cmd_autopilot(update, ctx)
            else:
                await self._start_flow(update, ctx, routed)
            return
        flow = ctx.user_data.get(self._flow_key())
        if not flow or flow.get("step") != "custom_pair":
            return
        pair = text.upper()
        if not self.hub.resolve_loose(pair):
            await self._reply(update, f"Unknown symbol <b>{pair}</b>. Try again:",
                              reply_markup=ui.retry_pair_keyboard(flow.get("flow", "analyze")))
            return
        flow_name = flow.get("flow", "analyze")
        flow["pair"] = pair
        flow.pop("step", None)
        if flow_name == "auto":
            await self._reply(update, "Custom pair is not needed for autopilot.",
                              reply_markup=ui.style_keyboard("auto"))
            return
        await self._after_pair(update, ctx, flow_name, pair)


def build_bot(token, hub, service, demo_ok=False):
    return Bot(token, hub, service, demo_ok=demo_ok)

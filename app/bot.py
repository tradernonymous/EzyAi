import asyncio
import io
import logging
import time
from collections import deque
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    TypeHandler,
    filters,
)

from . import billing
from . import health
from . import site_entitlements
from . import constants
from . import ui
from .analysis import sentiment as _sent
from .formatting import message as msg
from .fundamentals import Fundamentals
from .signals import engine as signal_engine
from .signals.scheduler import StateError

logger = logging.getLogger(__name__)

# Telegram messages are capped at 4096 characters.
TG_MAX_LEN = 4096
# How often the job checks the Telegram API itself (in 30 s ticks).
TELEGRAM_CHECK_EVERY = 10


def _fmt_date(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%d %b %Y")


class Bot:
    def __init__(self, token, hub, service, demo_ok=False, pay_config=None):
        self.hub = hub
        self.service = service
        self.fund = Fundamentals()
        self.demo_ok = demo_ok
        self.pay = dict(pay_config or {})
        self.site = site_entitlements.SiteClient(
            self.pay.get("site_url"), self.pay.get("site_key"))
        self._sweep_tick = 0
        self._buckets = {}  # chat_id -> deque of recent command timestamps
        self._error_alert_ts = 0.0
        builder = (Application.builder().token(token)
                   # Handlers run concurrently so one slow feed never blocks
                   # other users' replies; Service is lock-protected.
                   .concurrent_updates(64)
                   .connect_timeout(10).read_timeout(20)
                   .write_timeout(20).pool_timeout(10))
        try:
            # Queues outbound calls under Telegram's flood limits instead of
            # dropping a paid user's alert with RetryAfter.
            builder = builder.rate_limiter(AIORateLimiter())
        except RuntimeError as exc:  # optional extra not installed
            logger.warning("rate limiter unavailable: %s", exc)
        self.app = builder.build()
        self._register()

    def _register(self):
        a = self.app
        a.post_init = self.post_init_hook
        # group -1 runs before every handler: remember handle -> chat id so
        # website PRO purchases (typed by username) can be matched later.
        a.add_handler(TypeHandler(Update, self._pre_update), group=-1)
        a.add_error_handler(self.on_error)
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
        a.add_handler(CommandHandler("plans", self.cmd_plans))
        a.add_handler(CommandHandler("account", self.cmd_account))
        a.add_handler(CommandHandler("export", self.cmd_export))
        a.add_handler(CallbackQueryHandler(self.cb_flow, pattern=r"^ezy:"))
        a.add_handler(PreCheckoutQueryHandler(self.on_precheckout))
        a.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.on_paid))
        a.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.job_queue.run_repeating(self._job, interval=30, first=30)

    async def post_init_hook(self, app):
        try:
            from telegram import BotCommand
            await app.bot.set_my_commands(
                [BotCommand(cmd, desc) for cmd, desc in ui.COMMANDS])
        except Exception as exc:
            logger.warning("set_my_commands failed: %s", exc)

    async def _send_safe(self, chat_id, text, **kw):
        """Deliver a background message to one chat. A blocked bot removes
        that chat's watches; a flood wait is honoured once; any other failure
        is logged and never stops the caller's loop. Returns True on send."""
        kw.setdefault("parse_mode", ParseMode.HTML)
        for attempt in (1, 2):
            try:
                await self.app.bot.send_message(chat_id, text[:TG_MAX_LEN], **kw)
                return True
            except Forbidden:
                logger.info("chat %s blocked the bot; dropping its watches", chat_id)
                self.service.forget_chat(chat_id)
                return False
            except RetryAfter as exc:
                if attempt == 2:
                    logger.warning("deliver flood-limited chat=%s", chat_id)
                    return False
                await asyncio.sleep(min(float(exc.retry_after) + 0.5, 30))
            except BadRequest as exc:
                if "chat not found" in str(exc).lower():
                    self.service.forget_chat(chat_id)
                logger.warning("deliver rejected chat=%s: %s", chat_id, exc)
                return False
            except Exception as exc:
                logger.warning("deliver failed chat=%s: %s", chat_id, exc)
                return False
        return False

    async def _job(self, context):
        async def send(chat_id, signal, source="watch"):
            await self._send_safe(
                chat_id, msg.signal_message(signal, source=source),
                reply_markup=ui.followup_keyboard(signal["pair"]))
        try:
            await self.service.tick(send)
        except Exception as exc:
            logger.exception("tick failed: %s", exc)
        health.beat("tick")
        self._sweep_tick += 1
        if self._sweep_tick % TELEGRAM_CHECK_EVERY == 1:
            try:
                await self.app.bot.get_me()
                health.beat("telegram")
            except Exception as exc:
                logger.warning("telegram api check failed: %s", exc)
        if self.site.enabled and self._sweep_tick % 4 == 0:
            try:
                granted = await asyncio.to_thread(
                    site_entitlements.sweep, self.site, self.service)
            except Exception as exc:
                logger.warning("site sweep failed: %s", exc)
                granted = []
            for chat_id, row, until in granted:
                await self._send_safe(chat_id, msg.site_pro_activated_text(row, until))
        try:
            nudges = self.service.expiry_nudges()
        except Exception as exc:
            logger.warning("nudge scan failed: %s", exc)
            nudges = []
        for chat_id in nudges:
            await self._send_safe(chat_id, msg.expiry_nudge_text())

    async def on_error(self, update, ctx):
        """Last line of defence: the user hears back, the operator is told."""
        err = ctx.error
        chat = getattr(update, "effective_chat", None) if update is not None else None
        chat_id = getattr(chat, "id", None)
        if isinstance(err, Forbidden):
            if chat_id is not None:
                self.service.forget_chat(chat_id)
            return
        if isinstance(err, (RetryAfter, BadRequest, TelegramError)):
            logger.warning("telegram error chat=%s: %s", chat_id, err)
        else:
            logger.exception("unhandled error chat=%s: %s", chat_id, err)
            now = time.time()
            if now - self._error_alert_ts > 300 and self.service.on_alert:
                self._error_alert_ts = now
                try:
                    self.service.on_alert(
                        f"Bot error for chat {chat_id}: {type(err).__name__}: {err}")
                except Exception:
                    pass
        if chat is None:
            return
        if isinstance(err, StateError):
            text = ("\u26a0\ufe0f Could not save that change right now. Nothing was "
                    "charged or lost \u2014 please try again in a minute.")
        elif isinstance(err, BadRequest):
            text = ("\U0001f9f9 That reply could not be rendered. Please try again "
                    "or pick a market from the buttons.")
        else:
            text = ("\U0001f9f9 Something went wrong on our side. Please try again "
                    "in a moment \u2014 the team has been notified.")
        try:
            await chat.send_message(text, parse_mode=ParseMode.HTML,
                                    reply_markup=ui.help_keyboard())
        except Exception:
            pass

    async def cmd_export(self, update, ctx):
        """Admin only: DM the current state file as a backup."""
        admin_id = (self.pay or {}).get("admin_id")
        sender = update.effective_user.id if update.effective_user else None
        if admin_id is None or sender != admin_id:
            return
        data = self.service.export_bytes()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        await update.effective_chat.send_document(
            io.BytesIO(data), filename=f"ezyai-state-{stamp}.json",
            caption=f"State export \u00b7 {len(data):,} bytes")

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

    async def _resolve(self, pair):
        """(kind, symbol) or None. Symbol lookup can hit several upstream
        hosts, so it always runs off the event loop."""
        if not pair or len(pair) > 24:
            return None
        return await asyncio.to_thread(self.hub.resolve_loose, pair)

    # -- top-level commands -------------------------------------------------

    async def cmd_start(self, update, ctx):
        ctx.user_data.pop(self._flow_key(), None)
        await self._reply(
            update,
            "Welcome to <b>EzyAi</b> \u2014 live market analysis, "
            "alerts and auto signals.\n\n"
            "\u26a0\ufe0f Educational research only, not financial advice. "
            "Markets carry risk \u2014 verify every level with your broker.",
            reply_markup=ui.help_keyboard())
        await self._redeem_site(update)

    # -- website purchases (printezy.money) ----------------------------------

    def _over_rate_limit(self, chat_id, now=None):
        """Sliding one-minute window per chat. Only a burst well beyond
        human speed trips it, so real users never notice."""
        now = now or time.time()
        q = self._buckets.get(chat_id)
        if q is None:
            q = self._buckets[chat_id] = deque()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= constants.RATE_LIMIT_PER_MINUTE:
            return True
        q.append(now)
        if len(self._buckets) > 5000:  # bound memory on a long-running process
            for cid in [c for c, d in self._buckets.items() if not d or now - d[-1] > 120]:
                self._buckets.pop(cid, None)
        return False

    async def _pre_update(self, update, ctx):
        """Runs before every handler (group -1): liveness beat, handle
        bookkeeping for website purchases, and the per-chat rate limit."""
        health.beat("update")
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        if user is not None and chat is not None and getattr(user, "username", None):
            try:
                self.service.remember_user(chat.id, user.username)
            except Exception as exc:
                logger.debug("remember_user failed: %s", exc)
        if chat is None:
            return
        billable = (update.callback_query is not None
                    or (update.message is not None and update.message.text))
        if billable and self._over_rate_limit(chat.id):
            q = self._buckets[chat.id]
            # tell them once per burst, then stay silent
            if len(q) == constants.RATE_LIMIT_PER_MINUTE:
                q.append(time.time())
                try:
                    if update.callback_query is not None:
                        await update.callback_query.answer(
                            "Slow down a little \u2014 try again in a minute.",
                            show_alert=True)
                    else:
                        await chat.send_message(
                            "\u23f3 Slow down a little \u2014 try again in a minute.")
                except Exception:
                    pass
            raise ApplicationHandlerStop

    async def _redeem_site(self, update):
        """Claim PRO bought on the website for this user's handle. Returns
        True when new PRO time was granted (and the user was told)."""
        if not self.site.enabled:
            return False
        user = update.effective_user
        chat_id = update.effective_chat.id
        username = getattr(user, "username", None) if user else None
        try:
            granted = await asyncio.to_thread(
                site_entitlements.redeem_for_user,
                self.site, self.service, chat_id, username)
        except Exception as exc:
            logger.warning("site redeem failed chat=%s: %s", chat_id, exc)
            return False
        for row, until in granted:
            await self._reply(update, msg.site_pro_activated_text(row, until))
        return bool(granted)

    async def cmd_help(self, update, ctx):
        await self._reply(update, msg.help_text(),
                          reply_markup=ui.help_keyboard())

    async def cmd_dashboard(self, update, ctx):
        await self._send_dashboard(update.effective_chat.id, update, ctx)

    async def _send_dashboard(self, chat_id, update, ctx):
        watches = self.service.list_watches(chat_id)
        pilot = self.service.autopilots.get(str(chat_id))
        text = msg.dashboard_view(watches, pilot, self.hub.mode)
        if not self.service.is_pro(chat_id):
            text += "\nPlan: <b>Free</b> \u2014 unlock alerts + research: /plans"
        kb = ui.refresh_keyboard()
        if update.callback_query is not None:
            await self._edit_or_send(update.callback_query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    # -- analyze ------------------------------------------------------------

    async def cmd_analyze(self, update, ctx):
        args = ctx.args
        if args:
            pair = args[0].upper()[:24]
            if await self._resolve(pair) is None:
                await self._reply(
                    update, f"Unknown symbol <b>{escape(pair)}</b>.",
                    reply_markup=ui.retry_pair_keyboard("analyze"))
                return
            ctx.user_data[self._flow_key()] = {"flow": "analyze", "pair": pair}
            text, kb = ui.prompt_style("analyze", pair)
            await self._reply(update, text, reply_markup=kb)
            return
        await self._start_flow(update, ctx, "analyze")

    async def _run_analyze(self, update, ctx, pair, style, mode, query=None):
        await self._typing(update, ctx)
        status = (f"\U0001f4c8 Scanning <b>{escape(pair)}</b> \u00b7 "
                  f"{escape(style)}/{escape(mode)}\u2026")
        if query is not None:
            await self._edit_or_send(query, status)
        else:
            await self._reply(update, status)
        try:
            headlines = await asyncio.to_thread(
                self.fund.news, constants.base_asset(pair), 5)
        except Exception:
            headlines = []
        try:
            score = _sent.score_headlines(headlines)
        except Exception:
            score = None
        try:
            # feeds are blocking requests: run them off the event loop so
            # other users keep getting replies while this scan runs
            analysis = (await asyncio.to_thread(
                signal_engine.quick_analyze,
                pair, style, mode, self.hub, None, score))[0]
        except Exception as exc:
            target = query.message if query is not None else None
            logger.warning("analyze failed pair=%s: %s: %s", pair,
                           type(exc).__name__, exc)
            text = (f"\U0001f9f9 Analysis hiccup for <b>{escape(pair)}</b> \u2014 "
                    "the data feed stumbled. Tap retry in a few seconds.")
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
            tick = await asyncio.to_thread(self.hub.fetch_ticker, pair)
            tick.setdefault("mode", "live")
        except Exception as exc:
            logger.warning("quote failed pair=%s: %s: %s", pair,
                           type(exc).__name__, exc)
            text = (f"Could not fetch <b>{escape(pair)}</b> \u2014 feed hiccup. "
                    "Retry in a few seconds.")
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

    def _fundamentals_text(self, pair, pro=True):
        resolved = self.hub.resolve_loose(pair)
        if resolved is None:
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
        text = msg.fundamentals_report(kind, pair, data, "live", pro=pro)
        links = self.fund.links(kind, pair)
        try:
            news = self.fund.news(constants.base_asset(pair), limit=4)
        except Exception:
            news = []
        text += msg.related_reading(kind, pair, links, news)
        if not pro:
            text += msg.pro_upsell_note()
        return text

    async def _send_fundamentals(self, update, pair, ctx, query=None):
        await self._typing(update, ctx)
        pro = self.service.is_pro(update.effective_chat.id)
        try:
            # EDGAR/Coingecko/COT fetches are slow synchronous requests;
            # run off the loop so other users keep getting replies
            text = await asyncio.to_thread(self._fundamentals_text, pair, pro)
        except Exception as exc:
            logger.warning("fundamentals failed pair=%s: %s: %s", pair,
                           type(exc).__name__, exc)
            text = None
        if not text:
            kb = ui.retry_pair_keyboard("fund")
            if query is not None:
                await self._edit_or_send(query, f"Unknown symbol <b>{escape(pair)}</b>.", kb)
            else:
                await self._reply(update, f"Unknown symbol <b>{escape(pair)}</b>.",
                                  reply_markup=kb)
            return
        chat = update.effective_chat
        await chat.send_message(text, parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=ui.followup_keyboard(pair))

    # -- watch --------------------------------------------------------------

    async def cmd_watch(self, update, ctx):
        if not self.service.is_pro(update.effective_chat.id):
            await self._send_pro_gate(update, "Live watch alerts")
            return
        args = ctx.args
        if len(args) < 3:
            await self._start_flow(update, ctx, "watch")
            return
        pair, style, mode = args[0].upper()[:24], args[1].lower(), args[2].lower()
        err = await self._validate_pair_style_mode(pair, style, mode)
        if err:
            await self._reply(update, err,
                              reply_markup=ui.retry_pair_keyboard("watch"))
            return
        if self._add_watch(update, pair, style, mode) is None:
            await self._reply(update, msg.watch_cap_text(constants.MAX_WATCHES),
                              reply_markup=ui.watches_keyboard(
                                  self.service.list_watches(update.effective_chat.id)))
            return
        await self._reply(update, msg.watch_added_text(pair, style, mode),
                          reply_markup=ui.watches_keyboard(
                              self.service.list_watches(update.effective_chat.id)))

    async def _validate_pair_style_mode(self, pair, style, mode):
        if style not in constants.STYLES:
            return (f"Unknown style <b>{escape(style[:24])}</b>. "
                    "Use the buttons: scalping, intraday or swing.")
        if mode not in constants.MODES:
            return (f"Unknown mode <b>{escape(mode[:24])}</b>. "
                    "Use the buttons: safe, normal or aggressive.")
        if await self._resolve(pair) is None:
            return f"Unknown symbol <b>{escape(pair)}</b>."
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
        if not self.service.is_pro(update.effective_chat.id):
            await self._send_pro_gate(update, "Autopilot signals")
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
        if not self.service.is_pro(update.effective_chat.id):
            await self._send_pro_gate(update, "Autopilot signals", query)
            return
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

    # -- monetization -------------------------------------------------------

    def _trial_eligible(self, chat_id):
        return not self.service.plan_status(chat_id).get("trial_used", True)

    async def _send_pro_gate(self, update, feature, query=None):
        chat_id = update.effective_chat.id
        # A website buyer hitting a gate before /start: claim first, and if
        # that unlocked PRO there is nothing to gate.
        if await self._redeem_site(update) and self.service.is_pro(chat_id):
            return
        text = msg.pro_gate(feature, self._trial_eligible(chat_id))
        kb = ui.plans_keyboard(self._trial_eligible(chat_id))
        if query is not None:
            await self._edit_or_send(query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    async def cmd_plans(self, update, ctx):
        chat_id = update.effective_chat.id
        if await self._redeem_site(update) and self.service.is_pro(chat_id):
            return
        eligible = self._trial_eligible(chat_id)
        await self._reply(update, msg.plans_text(eligible),
                          reply_markup=ui.plans_keyboard(eligible))

    async def _send_plans(self, query, chat_id):
        eligible = self._trial_eligible(chat_id)
        await self._edit_or_send(query, msg.plans_text(eligible),
                                 ui.plans_keyboard(eligible))

    async def cmd_account(self, update, ctx):
        chat_id = update.effective_chat.id
        await self._redeem_site(update)
        status = self.service.plan_status(chat_id)
        comped = self.service.is_comped(chat_id)
        watches = self.service.list_watches(chat_id)
        pilot = self.service.autopilots.get(str(chat_id))
        text = msg.account_text(status, len(watches), pilot is not None,
                                comped=comped)
        if status["plan"] == "free" and not comped:
            await self._reply(update, text, reply_markup=ui.plans_keyboard(
                self._trial_eligible(chat_id)))
        else:
            await self._reply(update, text)

    async def _send_account(self, query, chat_id):
        status = self.service.plan_status(chat_id)
        comped = self.service.is_comped(chat_id)
        watches = self.service.list_watches(chat_id)
        pilot = self.service.autopilots.get(str(chat_id))
        text = msg.account_text(status, len(watches), pilot is not None,
                                comped=comped)
        if status["plan"] == "free" and not comped:
            await self._edit_or_send(
                query, text, ui.plans_keyboard(self._trial_eligible(chat_id)))
        else:
            await self._edit_or_send(query, text)

    async def _begin_pay(self, update, ctx, tier, query=None):
        plan = billing.tier(tier)
        if not plan:
            return
        chat_id = update.effective_chat.id
        text = (f"\U0001f48e <b>EzyAi PRO \u2014 {plan['label']}</b> "
                f"${plan['usd']:.2f}\nHow would you like to pay?")
        kb = ui.pay_methods_keyboard(tier)
        if query is not None:
            await self._edit_or_send(query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    async def _pay_stars(self, update, ctx, tier, query=None):
        from telegram import LabeledPrice
        plan = billing.tier(tier)
        chat_id = update.effective_chat.id
        try:
            await ctx.bot.send_invoice(
                chat_id, f"EzyAi PRO \u2014 {plan['label']}",
                "Live alerts, autopilot signals, deep research.",
                billing.encode_payload(tier, "stars", chat_id),
                provider_token="", currency="XTR",
                prices=[LabeledPrice(plan["label"], plan["stars"])])
        except Exception as exc:
            logger.warning("stars invoice failed: %s", exc)
            text = ("Stars checkout hiccup \u2014 please try again or use card/USDT.")
            if query is not None:
                await self._edit_or_send(query, text)
            else:
                await self._reply(update, text)

    async def _pay_card(self, update, ctx, tier, query=None):
        plan = billing.tier(tier)
        key = (self.pay or {}).get("stripe_key") or ""
        username = (self.pay or {}).get("bot_username") or "ezytradeai_bot"
        chat_id = update.effective_chat.id
        if not key:
            text = ("Card payments are being wired up \u2014 use Stars "
                    "for instant PRO, or USDT below.")
            if query is not None:
                await self._edit_or_send(query, text)
            else:
                await self._reply(update, text)
            return
        try:
            import stripe  # lazy: only card payers touch it
            stripe.api_key = key
            session = stripe.checkout.Session.create(**billing.stripe_session_params(
                tier, chat_id,
                success_url=f"https://t.me/{username}?start=paid",
                cancel_url=f"https://t.me/{username}?start=cancelled"))
            url = session.get("url") if isinstance(session, dict) else session.url
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"\U0001f4b3 Pay ${plan['usd']:.2f} now", url=url)],
                [InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel")],
            ])
            text = (f"\U0001f4b3 <b>EzyAi PRO \u2014 {plan['label']}</b> "
                    f"${plan['usd']:.2f}\nTap below to check out securely with Stripe. "
                    "PRO activates automatically.")
            if query is not None:
                await self._edit_or_send(query, text, kb)
            else:
                await self._reply(update, text, reply_markup=kb)
        except Exception as exc:
            logger.warning("stripe session failed: %s", exc)
            text = ("Card checkout hiccup \u2014 please try again or use Stars/USDT.")
            if query is not None:
                await self._edit_or_send(query, text)
            else:
                await self._reply(update, text)

    async def _pay_usdt(self, update, ctx, tier, query=None):
        plan = billing.tier(tier)
        address = (self.pay or {}).get("usdt_address") or ""
        admin_id = (self.pay or {}).get("admin_id")
        if not address or not admin_id:
            text = ("Manual USDT is coming online soon \u2014 use Stars "
                    "for instant PRO.")
            if query is not None:
                await self._edit_or_send(query, text)
            else:
                await self._reply(update, text)
            return
        text = (f"\u20ae Send exactly <b>${plan['usd']:.2f} USDT</b> (TRC-20) to:\n"
                f"<code>{address}</code>\n"
                "Then tap below \u2014 an admin approves within a few hours.")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("\u2705 I've paid", callback_data=ui.cb_paid(tier)),
            InlineKeyboardButton("\u2715 Cancel", callback_data="ezy:cancel"),
        ]])
        if query is not None:
            await self._edit_or_send(query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    async def _submit_usdt_claim(self, update, ctx, txid):
        flow = self._get_flow(ctx)
        tier = (flow or {}).get("usdt_tier")
        name = (flow or {}).get("usdt_name") or "user"
        ctx.user_data.pop(self._flow_key(), None)
        if not tier:
            return
        plan = billing.tier(tier)
        admin_id = (self.pay or {}).get("admin_id")
        if not plan or not admin_id:
            await self._reply(update, "Payment desk offline \u2014 use Stars for instant PRO.")
            return
        txid = (txid or "").strip()
        if len(txid) < 8 or len(txid) > 200:
            await self._reply(update, "That doesn't look like a valid TXID. "
                                      "It's the long alphanumeric string from your "
                                      "transfer confirmation \u2014 try again, or "
                                      "/cancel to stop.")
            return
        chat_id = update.effective_chat.id
        try:
            await ctx.bot.send_message(
                admin_id,
                f"\U0001f4b0 USDT claim: <b>{escape(name)}</b> (id <code>{chat_id}</code>)\n"
                f"claims <b>{plan['label']}</b> \u2014 ${plan['usd']:.2f}\n"
                f"TXID: <code>{escape(txid)}</code>\n"
                "Approve after checking the network.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "\u2705 Approve", callback_data=ui.cb_admin_ok(chat_id, tier)),
                    InlineKeyboardButton(
                        "\u274c Reject", callback_data=ui.cb_admin_no(chat_id)),
                ]]))
            await self._reply(update, "Claim sent with your TXID \u2014 an admin "
                                      "verifies within a few hours. You'll get PRO "
                                      "automatically.")
        except Exception as exc:
            logger.warning("usdt claim forward failed: %s", exc)
            await self._reply(update, "Could not reach the payment desk \u2014 try Stars.")

    async def on_precheckout(self, update, ctx):
        q = update.pre_checkout_query
        ok = billing.decode_payload(q.invoice_payload) is not None
        try:
            await q.answer(ok=ok, error_message=None if ok else "Order expired \u2014 start again from /plans.")
        except Exception:
            pass

    async def on_paid(self, update, ctx):
        sp = update.message.successful_payment
        info = billing.decode_payload(sp.invoice_payload)
        if not info:
            return
        months = constants.PLANS[info["tier"]]["months"]
        # charge id makes Telegram redeliveries (e.g. after a restart)
        # idempotent: the same payment can never grant two periods
        charge_id = (getattr(sp, "telegram_payment_charge_id", None)
                     or getattr(sp, "provider_payment_charge_id", None)
                     or f"tg_update:{update.update_id}")
        try:
            until = self.service.activate_pro(info["chat_id"], months,
                                              event_id=charge_id)
        except StateError:
            logger.error("stars payment not persisted charge=%s chat=%s",
                         charge_id, info["chat_id"])
            await self._reply(update, "\u2705 Payment received. Activation is "
                                      "taking a moment \u2014 the team has been "
                                      "notified and will confirm shortly.")
            return
        logger.info("stars PRO activated chat=%s tier=%s charge=%s",
                    info["chat_id"], info["tier"], charge_id)
        date = _fmt_date(until)
        try:
            await ctx.bot.send_message(
                info["chat_id"],
                f"\u2705 <b>PRO activated</b> \u00b7 {info['tier']} until {date}.\n"
                "Your watches resume automatically. Enjoy!",
                parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.warning("pro confirm failed: %s", exc)
        if update.effective_chat.id != info["chat_id"]:
            await self._reply(update, "Payment received \u2014 PRO activated. Enjoy!")

    # -- flow engine --------------------------------------------------------

    async def _start_flow(self, update, ctx, flow, pair=None):
        if flow == "watch" and not self.service.is_pro(update.effective_chat.id):
            await self._send_pro_gate(update, "Live watch alerts")
            return
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
        try:
            await query.answer()
        except BadRequest as exc:
            # Stale queries (older than ~48h or from before a restart) cannot
            # be answered, but the tap itself is still worth serving.
            logger.debug("callback answer failed: %s", exc)
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
                if target == "watch" and not self.service.is_pro(chat_id):
                    await self._send_pro_gate(update, "Live watch alerts", query)
                    return
                ctx.user_data[self._flow_key()] = {"flow": target, "page": 0}
                if pair:
                    if await self._resolve(pair) is None:
                        await self._edit_or_send(
                            query, f"Unknown symbol <b>{escape(pair)}</b>.",
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
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    logger.warning("pair page edit failed: %s", exc)
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
            if await self._resolve(pair) is None:
                await self._edit_or_send(query, f"Unknown symbol <b>{escape(pair)}</b>.",
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
            if not self.service.is_pro(chat_id):
                await self._send_pro_gate(update, "Live watch alerts", query)
                return
            pair, style, mode = flow.get("pair"), flow.get("style"), flow.get("mode")
            if not pair or not style or not mode:
                await self._restart(update, ctx, "watch", query)
                return
            ctx.user_data.pop(self._flow_key(), None)
            if self._add_watch(update, pair, style, mode) is None:
                await self._edit_or_send(
                    query, msg.watch_cap_text(constants.MAX_WATCHES),
                    ui.watches_keyboard(self.service.list_watches(chat_id)))
                return
            await self._edit_or_send(
                query, msg.watch_added_text(pair, style, mode),
                ui.watches_keyboard(self.service.list_watches(chat_id)))
            return

        if action == "auto_go":
            if not self.service.is_pro(chat_id):
                await self._send_pro_gate(update, "Autopilot signals", query)
                return
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

        if action == "plans":
            await self._send_plans(query, chat_id)
            return

        if action == "trial":
            until = self.service.start_trial(chat_id)
            ctx.user_data.pop(self._flow_key(), None)
            if until is None:
                await self._edit_or_send(
                    query, "Trial already used on this account \u2014 pick a plan:",
                    ui.plans_keyboard(False))
                return
            date = _fmt_date(until)
            await self._edit_or_send(
                query,
                f"\U0001f381 <b>Trial on!</b> Full PRO until {date}.\n"
                "Try a watch, autopilot and deep fundamentals \u2014 on the house.",
                ui.help_keyboard())
            return

        if action == "tier":
            tier = cb["tier"]
            if tier not in constants.PLANS:
                return
            await self._begin_pay(update, ctx, tier, query)
            return

        if action == "pay":
            tier, method = cb["tier"], cb["method"]
            if tier not in constants.PLANS or method not in billing.METHODS:
                return
            if method == "stars":
                await self._pay_stars(update, ctx, tier, query)
            elif method == "card":
                await self._pay_card(update, ctx, tier, query)
            elif method == "usdt":
                await self._pay_usdt(update, ctx, tier, query)
            return

        if action == "paid":
            tier = cb["tier"]
            plan = billing.tier(tier)
            admin_id = (self.pay or {}).get("admin_id")
            if not plan or not admin_id:
                await self._edit_or_send(query, "Payment desk offline \u2014 use Stars for instant PRO.")
                return
            user = update.effective_user
            name = (user.full_name if user else f"user {chat_id}") or f"user {chat_id}"
            flow = self._get_flow(ctx)
            flow.update({"step": "usdt_proof", "usdt_tier": tier,
                         "usdt_name": name})
            ctx.user_data[self._flow_key()] = flow
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                "\u2715 Cancel", callback_data="ezy:cancel")]])
            await self._edit_or_send(
                query,
                "Almost done \u2014 send a <b>screenshot</b> of your "
                f"{plan['label']} transfer confirmation so the desk can verify it.\n\n"
                "(A photo of the wallet \u201csent\u201d screen is perfect. "
                "You can also just paste the transaction hash as text.)",
                kb)
            return

        if action in ("admin_ok", "admin_no"):
            admin_id = (self.pay or {}).get("admin_id")
            sender = update.effective_user.id if update.effective_user else None
            logger.info("admin action=%s sender=%s target=%s tier=%s (admin_id=%s)",
                        action, sender, cb.get("chat"), cb.get("tier"), admin_id)
            if admin_id is None or sender != admin_id:
                logger.warning("admin action denied: sender=%s admin_id=%s",
                               sender, admin_id)
                # the query was already answered above; reply instead
                await query.message.reply_text("Admins only.")
                return
            target = cb["chat"]
            if action == "admin_ok":
                plan = constants.PLANS.get(cb.get("tier"))
                if not plan:
                    await self._edit_or_send(query, "Malformed approval button.")
                    return
                # One grant per approval message: a double tap on the same
                # button replays instead of stacking a second period.
                claim_id = f"usdt:{query.message.chat_id}:{query.message.message_id}"
                try:
                    until = self.service.activate_pro(target, plan["months"],
                                                      event_id=claim_id)
                except StateError:
                    await self._edit_or_send(
                        query, "Could not save the activation \u2014 state file "
                               "problem. Fix storage and tap Approve again.")
                    return
                logger.info("admin approved PRO %s for %s until %s",
                            cb["tier"], target, until)
                date = _fmt_date(until)
                try:
                    await ctx.bot.send_message(
                        target,
                        f"\u2705 <b>PRO activated</b> \u00b7 {cb['tier']} until {date}.\n"
                        "Your watches resume automatically. Enjoy!",
                        parse_mode=ParseMode.HTML)
                except Exception as exc:
                    logger.warning("admin-ok notify failed: %s", exc)
                await self._edit_or_send(query, f"Approved PRO {cb['tier']} for {target}.")
            else:
                try:
                    await ctx.bot.send_message(
                        target, "Your USDT claim was not approved. "
                                "Check the amount/address and tap \u201cI've paid\u201d again, "
                                "or pay instantly with Stars via /plans.",
                        parse_mode=ParseMode.HTML)
                except Exception as exc:
                    logger.warning("admin-no notify failed: %s", exc)
                await self._edit_or_send(query, f"Rejected claim from {target}.")
            return

    async def _restart(self, update, ctx, flow_name, query):
        ctx.user_data.pop(self._flow_key(), None)
        await self._edit_or_send(
            query, "Let's start over \u2014 pick a flow:",
            ui.help_keyboard())

    async def on_photo(self, update, ctx):
        """USDT proof by screenshot: forward the photo to the admin desk."""
        flow = self._get_flow(ctx)
        if not flow or flow.get("step") != "usdt_proof":
            return
        tier = flow.get("usdt_tier")
        name = flow.get("usdt_name") or "user"
        ctx.user_data.pop(self._flow_key(), None)
        plan = billing.tier(tier) if tier else None
        admin_id = (self.pay or {}).get("admin_id")
        if not plan or not admin_id:
            await self._reply(update, "Payment desk offline \u2014 use Stars for instant PRO.")
            return
        photos = update.message.photo or []
        if not photos:
            return
        file_id = photos[-1].file_id  # largest size
        chat_id = update.effective_chat.id
        try:
            await ctx.bot.send_photo(
                admin_id, file_id,
                caption=(f"\U0001f4b0 USDT claim: <b>{escape(name)}</b> (id <code>{chat_id}</code>)\n"
                         f"claims <b>{plan['label']}</b> \u2014 ${plan['usd']:.2f}\n"
                         "Proof screenshot below. Approve after checking."),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "\u2705 Approve", callback_data=ui.cb_admin_ok(chat_id, tier)),
                    InlineKeyboardButton(
                        "\u274c Reject", callback_data=ui.cb_admin_no(chat_id)),
                ]]))
            await self._reply(update, "Screenshot received \u2014 an admin verifies "
                                      "within a few hours. You'll get PRO automatically.")
        except Exception as exc:
            logger.warning("usdt photo forward failed: %s", exc)
            await self._reply(update, "Could not reach the payment desk \u2014 try Stars.")

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
        if flow and flow.get("step") == "usdt_proof":
            await self._submit_usdt_claim(update, ctx, text)
            return
        if not flow or flow.get("step") != "custom_pair":
            return
        pair = text.upper()[:24]
        if not pair.isalnum() or await self._resolve(pair) is None:
            await self._reply(update, f"Unknown symbol <b>{escape(pair)}</b>. Try again:",
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

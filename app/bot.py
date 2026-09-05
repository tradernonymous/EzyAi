import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from . import billing
from . import constants
from . import ui
from .analysis import sentiment as _sent
from .formatting import message as msg
from .fundamentals import Fundamentals
from .signals import engine as signal_engine

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, token, hub, service, demo_ok=False, pay_config=None):
        self.hub = hub
        self.service = service
        self.fund = Fundamentals()
        self.demo_ok = demo_ok
        self.pay = dict(pay_config or {})
        self.app = Application.builder().token(token).build()
        self._register()

    def _register(self):
        a = self.app
        a.post_init = self.post_init_hook
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
        a.add_handler(CallbackQueryHandler(self.cb_flow, pattern=r"^ezy:"))
        a.add_handler(PreCheckoutQueryHandler(self.on_precheckout))
        a.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.on_paid))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.job_queue.run_repeating(self._job, interval=30, first=30)

    async def post_init_hook(self, app):
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
        try:
            for chat_id in self.service.expiry_nudges():
                await self.app.bot.send_message(
                    chat_id, msg.expiry_nudge_text(),
                    parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.warning("nudge failed: %s", exc)

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
            tick = await asyncio.to_thread(self.hub.fetch_ticker, pair)
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

    def _fundamentals_text(self, pair, pro=True):
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
        text = msg.fundamentals_report(kind, pair, data, self.hub.mode, pro=pro)
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
        if not self.service.is_pro(update.effective_chat.id):
            await self._send_pro_gate(update, "Live watch alerts")
            return
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
        text = msg.pro_gate(feature, self._trial_eligible(chat_id))
        kb = ui.plans_keyboard(self._trial_eligible(chat_id))
        if query is not None:
            await self._edit_or_send(query, text, kb)
        else:
            await self._reply(update, text, reply_markup=kb)

    async def cmd_plans(self, update, ctx):
        chat_id = update.effective_chat.id
        eligible = self._trial_eligible(chat_id)
        await self._reply(update, msg.plans_text(eligible),
                          reply_markup=ui.plans_keyboard(eligible))

    async def _send_plans(self, query, chat_id):
        eligible = self._trial_eligible(chat_id)
        await self._edit_or_send(query, msg.plans_text(eligible),
                                 ui.plans_keyboard(eligible))

    async def cmd_account(self, update, ctx):
        chat_id = update.effective_chat.id
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
                f"\U0001f4b0 USDT claim: <b>{name}</b> (id <code>{chat_id}</code>)\n"
                f"claims <b>{plan['label']}</b> \u2014 ${plan['usd']:.2f}\n"
                f"TXID: <code>{txid}</code>\n"
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
                     or getattr(sp, "provider_payment_charge_id", None))
        until = self.service.activate_pro(info["chat_id"], months,
                                          event_id=charge_id)
        import datetime
        date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
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
                if target == "watch" and not self.service.is_pro(chat_id):
                    await self._send_pro_gate(update, "Live watch alerts", query)
                    return
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
            if not self.service.is_pro(chat_id):
                await self._send_pro_gate(update, "Live watch alerts", query)
                return
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
            import datetime
            date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b")
            await self._edit_or_send(
                query,
                f"\U0001f381 <b>Trial on!</b> Full PRO until {date}.\n"
                "Try a watch, autopilot and deep fundamentals \u2014 on the house.",
                ui.help_keyboard())
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
        flow.update({"step": "usdt_txid", "usdt_tier": tier,
                     "usdt_name": name})
        ctx.user_data[self._flow_key()] = flow
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "\u2715 Cancel", callback_data="ezy:cancel")]])
        await self._edit_or_send(
            query,
            "Almost done \u2014 send me the <b>transaction hash (TXID)</b> of your "
            f"{plan['label']} transfer so the desk can verify it.\n\n"
            "(It's the long string that starts with a few letters/digits "
            "on your wallet's transfer confirmation.)",
            kb)
        return

        if action in ("admin_ok", "admin_no"):
            admin_id = (self.pay or {}).get("admin_id")
            if admin_id is None or update.effective_user.id != admin_id:
                await query.answer("Admins only.", show_alert=True)
                return
            target = cb["chat"]
            if action == "admin_ok":
                months = constants.PLANS[cb["tier"]]["months"]
                until = self.service.activate_pro(target, months)
                import datetime
                date = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%d %b %Y")
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
        if flow and flow.get("step") == "usdt_txid":
            await self._submit_usdt_claim(update, ctx, text)
            return
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

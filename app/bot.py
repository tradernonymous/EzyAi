import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import constants
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
        a.add_handler(CommandHandler("start", self.cmd_start))
        a.add_handler(CommandHandler("help", self.cmd_help))
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

    async def _job(self, context):
        async def send(chat_id, signal, source="watch"):
            text = msg.signal_message(signal, source=source)
            try:
                await self.app.bot.send_message(
                    chat_id, text, parse_mode=ParseMode.HTML)
            except Exception as exc:
                logger.warning("deliver failed chat=%s: %s", chat_id, exc)
        try:
            await self.service.tick(send)
        except Exception as exc:
            logger.warning("tick failed: %s", exc)

    async def _reply(self, update, text, **kw):
        if not text:
            return
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

    async def cmd_start(self, update, ctx):
        await self._reply(update, msg.help_text())

    async def cmd_help(self, update, ctx):
        await self._reply(update, msg.help_text())

    def _pair_keyboard(self, page=0):
        pool = constants.ALL_UNIVERSE
        size = 9
        start = page * size
        page_list = pool[start:start + size]
        rows = []
        for i in range(0, len(page_list), 3):
            row = [InlineKeyboardButton(p, callback_data=f"ezy:pair:{p}")
                   for p in page_list[i:i + 3]]
            rows.append(row)
        nav = []
        if start > 0:
            nav.append(InlineKeyboardButton("\u276e prev", callback_data=f"ezy:page:{page - 1}"))
        nav.append(InlineKeyboardButton("\u2713 Custom", callback_data="ezy:pair:custom"))
        if start + size < len(pool):
            nav.append(InlineKeyboardButton("next \u276f", callback_data=f"ezy:page:{page + 1}"))
        rows.append(nav)
        return InlineKeyboardMarkup(rows)

    def _tf_keyboard(self, pair):
        rows = []
        items = [("1m", "1m"), ("5m", "5m"), ("15m", "15m"),
                 ("30m", "30m"), ("1h", "1h"), ("4h", "4h"), ("1d", "1d")]
        for i in range(0, len(items), 3):
            rows.append([InlineKeyboardButton(s, callback_data=f"ezy:tf:{pair}:{t}")
                         for s, t in items[i:i + 3]])
        rows.append([InlineKeyboardButton("\u2715 cancel", callback_data="ezy:cancel")])
        return InlineKeyboardMarkup(rows)

    async def cmd_analyze(self, update, ctx):
        args = ctx.args
        if args:
            pair = args[0].upper()
            resolved = self.hub.resolve_loose(pair)
            if not resolved:
                await self._reply(update, f"Unknown symbol <b>{pair}</b>. Try e.g. BTCUSDT, EURUSD, AAPL.")
                return
            await self._reply(update, f"Pick a timeframe for <b>{pair}</b> analysis:",
                              reply_markup=self._tf_keyboard(pair))
            return
        ctx.user_data[self._flow_key()] = {"step": "pick_pair", "page": 0}
        await self._reply(update, "Choose a market to analyze:",
                          reply_markup=self._pair_keyboard())

    async def cmd_quote(self, update, ctx):
        if not ctx.args:
            await self._reply(update, "Usage: /quote PAIR  (e.g. /quote BTCUSDT)")
            return
        pair = ctx.args[0].upper()
        try:
            tick = self.hub.fetch_ticker(pair)
            tick["mode"] = "demo" if self.hub.mode == "demo" else "live"
            await self._reply(update, msg.quote_report(pair, tick))
        except Exception:
            await self._reply(update, f"Could not fetch <b>{pair}</b>. Check the symbol and try again.")

    async def cmd_watch(self, update, ctx):
        args = ctx.args
        if len(args) < 3:
            await self._reply(update,
                              "Usage: /watch PAIR STYLE MODE\n"
                              "e.g.  /watch BTCUSDT intraday normal\n"
                              "Styles: scalping | intraday | swing \u00b7 Modes: safe | normal | aggressive")
            return
        pair, style, mode = args[0].upper(), args[1].lower(), args[2].lower()
        if style not in constants.STYLES:
            await self._reply(update, f"Unknown style <b>{style}</b>. Use scalping, intraday or swing.")
            return
        if mode not in constants.MODES:
            await self._reply(update, f"Unknown mode <b>{mode}</b>. Use safe, normal or aggressive.")
            return
        if not self.hub.resolve_loose(pair):
            await self._reply(update, f"Unknown symbol <b>{pair}</b>.")
            return
        w = self.service.add_watch(update.effective_chat.id, pair, style, mode)
        sp = constants.STYLE_PROFILE[style]
        await self._reply(update,
                          f"\U0001f4cb Watch added: <b>{w['pair']}</b> \u00b7 {style}/{mode}\n"
                          f"Checks every {sp['check_interval_s']}s on {sp['base_tf']} candles; "
                          f"alerts respect mode risk rules.")

    async def cmd_watches(self, update, ctx):
        rows = self.service.list_watches(update.effective_chat.id)
        await self._reply(update, msg.watch_list(rows))

    async def cmd_unwatch(self, update, ctx):
        if not ctx.args:
            await self._reply(update, "Usage: /unwatch PAIR")
            return
        ok = self.service.remove_watch(update.effective_chat.id, ctx.args[0].upper())
        await self._reply(update, "Watch removed." if ok else "No such watch.")

    async def cmd_autopilot(self, update, ctx):
        args = ctx.args
        if len(args) < 2:
            await self._reply(update,
                              "Usage: /autopilot STYLE MODE\n"
                              "e.g.  /autopilot intraday aggressive\n"
                              "The bot picks a random pair and releases signals from these two settings only.")
            return
        style, mode = args[0].lower(), args[1].lower()
        if style not in constants.STYLES:
            await self._reply(update, f"Unknown style <b>{style}</b>.")
            return
        if mode not in constants.MODES:
            await self._reply(update, f"Unknown mode <b>{mode}</b>.")
            return
        self.service.start_autopilot(update.effective_chat.id, style, mode)
        await self._reply(update,
                          f"\U0001f680 Autopilot on \u00b7 {style}/{mode}\n"
                          "Random pairs, capped by the mode daily signal limit.")

    async def cmd_stop_autopilot(self, update, ctx):
        ok = self.service.stop_autopilot(update.effective_chat.id)
        await self._reply(update, "Autopilot stopped." if ok else "No autopilot running.")

    async def cmd_fundamentals(self, update, ctx):
        if not ctx.args:
            await self._reply(update, "Usage: /fundamentals PAIR  (e.g. /fundamentals BTCUSDT)")
            return
        pair = ctx.args[0].upper()
        resolved = self.hub.resolve_loose(pair)
        if not resolved:
            await self._reply(update, f"Unknown symbol <b>{pair}</b>.")
            return
        kind, sym = resolved
        data = None
        if kind == constants.KIND_CRYPTO:
            try:
                data = self.fund.crypto(pair)
            except Exception:
                data = None
        elif kind == constants.KIND_STOCK:
            try:
                data = self.fund.stock(pair)
            except Exception:
                data = None
        elif kind == constants.KIND_FOREX:
            try:
                data = self.fund.forex(pair)
            except Exception:
                data = None
        elif kind == constants.KIND_CFD:
            try:
                data = self.fund.cfd(resolved[1])
            except Exception:
                data = None
        text = msg.fundamentals_report(kind, pair, data, self.hub.mode)
        links = self.fund.links(kind, pair)
        text += "\n" + msg.links_block(links)
        news = self.fund.news(pair.replace("USDT", ""))
        if news:
            text += "\n\n<b>Recent headlines</b>\n" + msg.news_block(news)
        await self._reply(update, text)

    async def cb_flow(self, update, ctx):
        query = update.callback_query
        await query.answer()
        data = query.data
        chat_id = update.effective_chat.id
        ctx.user_data[self._flow_key()] = ctx.user_data.get(self._flow_key(), {})
        flow = ctx.user_data[self._flow_key()]

        if data.startswith("ezy:page:"):
            page = int(data.split(":")[2])
            flow["page"] = page
            try:
                await query.edit_message_text("Choose a market to analyze:",
                                              reply_markup=self._pair_keyboard(page))
            except Exception:
                pass
            return

        if data == "ezy:cancel":
            ctx.user_data.pop(self._flow_key(), None)
            await self._edit_or_send(query, "Cancelled.")
            return

        if data.startswith("ezy:pair:"):
            pair = data.split(":", 2)[2]
            if pair == "custom":
                flow["step"] = "custom_pair"
                await self._edit_or_send(query, "Send the symbol (e.g. BTCUSDT, EURUSD, AAPL):")
                return
            flow["step"] = "tf"
            await self._edit_or_send(query, f"Pick a timeframe for <b>{pair}</b>:",
                                     reply_markup=self._tf_keyboard(pair))
            return

        if data.startswith("ezy:tf:"):
            _, _, pair, tf = data.split(":", 3)
            ctx.user_data.pop(self._flow_key(), None)
            await self._edit_or_send(query, "\U0001f4c8 Generating analysis\u2026"
                                            f"\nPair <b>{pair}</b> \u00b7 TF {tf}")
            try:
                analysis = signal_engine.quick_analyze(
                    pair, "intraday", "normal", self.hub, interval=tf)[0]
            except Exception as exc:
                await query.message.reply_text(f"Analysis failed: {exc}")
                return
            await query.message.reply_text(msg.analysis_report(analysis),
                                           parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)

    async def on_text(self, update, ctx):
        flow = ctx.user_data.get(self._flow_key())
        if not flow or flow.get("step") != "custom_pair":
            return
        pair = update.message.text.strip().upper()
        resolved = self.hub.resolve_loose(pair)
        if not resolved:
            await self._reply(update, f"Unknown symbol <b>{pair}</b>.")
            return
        ctx.user_data.pop(self._flow_key(), None)
        await self._reply(update, f"Pick a timeframe for <b>{pair}</b> analysis:",
                          reply_markup=self._tf_keyboard(pair))


def build_bot(token, hub, service, demo_ok=False):
    return Bot(token, hub, service, demo_ok=demo_ok)
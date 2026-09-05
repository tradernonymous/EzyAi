# EzyAi

A Telegram bot that turns free market data into trading confluence: detailed
technical analysis with entry/exit levels, live per-pair alerts, fundamentals
with links, and randomized auto-signals — all driven by *style* (scalping /
intraday / swing) and *risk mode* (safe / normal / aggressive).

## Features

Everything below works two ways: tap the inline shortcut buttons attached
to bot messages, or type the commands (also in the Telegram Menu button).
Every flow is guided (pair → style → risk mode) with Back/Cancel,
confirmations, and one-tap follow-ups (Watch / Fundamentals / Quote) on
every result.

- **`/analyze`** — on-demand market analysis: trend, support/resistance,
  entry zone, stop loss, TP1/TP2, risk-reward, confidence score, exit rules,
  plus pattern / sentiment / volatility / session context notes.
- **`/watch PAIR STYLE MODE`** — live trade alerts for a specific pair.
  Style sets the timeframe and check frequency; mode sets risk per trade,
  R/R targets, confirmation strictness and daily signal limits.
- **`/fundamentals PAIR`** — fundamentals + source links for the selected
  pair (CoinGecko for crypto, derived stats for stocks & forex) plus recent
  headlines.
- **`/autopilot STYLE MODE`** — auto signals: the bot randomly picks a pair
  from the universe and releases signals based only on those two settings,
  capped by the mode daily limit.
- **`/dashboard`** — status overview (watches, autopilot, feed) with a
  refresh button; all actions live in the persistent button menu.
- Also: `/watches` (per-row remove buttons), `/unwatch`, `/quote`, `/help`.

## Monetization (Free vs PRO)

- **Free**: Analyze (+Quote, Dashboard preview). Everything else upsells.
- **PRO**: live Watch alerts, Autopilot signals, deep Fundamentals
  (scores, DCF fair value, COT positioning, macro verdict + outlook).
- **Plans**: 1 month $14.99 · **6 months $44.99 (MOST POPULAR, save 50%)** ·
  12 months $99.99 (save 44%). 3-day free trial, once per user.
- **Payments**: Telegram Stars auto-approve via invoices; card via native
  Stripe Checkout (dynamic prices, no preset Price IDs) with webhook
  auto-activation at `/webhook/stripe`; USDT manual (TRC-20) with admin
  approve/deny buttons.
- **Team access**: `PRO_ACCESS_IDS` (comma-separated chat ids, always PRO,
  editable via secret); `ADMIN_TELEGRAM_ID` approves USDT claims.
  The two lists are independent.
- **Website card checkout**: PRO can also be bought on
  [printezy.money/ezyai](https://printezy.money/ezyai) (the site's own
  Stripe checkout, typed Telegram username). The site records an
  entitlement; this bot claims it for that handle on `/start`, `/plans`,
  `/account`, any PRO gate, and a 2-minute background sweep over handles
  it has already seen — activation is idempotent on the Stripe session id.
  Env: `EZYAI_SITE_URL` (default `https://printezy.money`) and
  `EZYAI_SITE_KEY` (must equal the site's `EZYAI_ENTITLEMENT_KEY`; empty
  disables the feature). See `app/site_entitlements.py`.
- Expiry auto-downgrades; watches stay stored and resume on PRO.
  Env: `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `BOT_USERNAME`,
  `USDT_ADDRESS`, `ADMIN_TELEGRAM_ID`, `PRO_ACCESS_IDS`
  (see `.env.example`). Register `https://<app>.fly.dev/webhook/stripe`
  as the Stripe webhook endpoint (event: `checkout.session.completed`).

## Markets & data sources (free, no API keys)

| Market | Provider | Examples |
| --- | --- | --- |
| Crypto | Binance public API (multi-host) → ccxt fallback (Kraken/Coinbase, no key) | `BTCUSD`, `ETHUSD`, `SOLUSD` |
| Forex | Yahoo Finance chart API | `EURUSD`, `GBPUSD`, `USDJPY` |
| Stocks / ETFs | Yahoo Finance chart API | `AAPL`, `TSLA`, `SPY`, `QQQ` |
| Metals / energy / indices (CFD-style) | Yahoo Finance chart API | `XAUUSD`, `XAGUSD`, `WTI`, `US30`, `NAS100`, `SPX500` |

`EZYAI_DEMO_DATA=true` enables a deterministic synthetic data fallback when a
live feed fails (useful for offline demos/tests).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env            # fill TELEGRAM_BOT_TOKEN from @BotFather
python main.py
```

## Usage examples

```
/analyze BTCUSD        → then pick style + risk mode (buttons guide you)
/watch EURUSD intraday safe
/watch AAPL swing aggressive
/unwatch EURUSD
/fundamentals SOLUSD
/autopilot scalping aggressive
/stopautopilot
/quote ETHUSD
```

## Project layout

```
app/
  bot.py                 Telegram handlers + inline flows
  constants.py           styles, modes, TFs, pair universes, source links
  config.py              env/state/infra config
  analysis/              pure-python indicators, S/R levels, strategy engine
  data/                  Binance / Yahoo / synthetic providers (unified candles)
  signals/               signal engine, autopilot, watch/autopilot scheduler
  risk/                  position sizing helpers
  fundamentals/          CoinGecko, derived stats, link + news builders
  formatting/            HTML message renderers
main.py                  entry point
tests/                   pytest suite (engine, indicators, risk, constants)
```

## Deploy to Fly.io (free, always-on)

The repo ships a `Dockerfile`, `fly.toml`, and a health endpoint so the bot
stays awake 24/7 on Fly.io's free allowance.

```bash
# 1. install flyctl
winget install fly-io.flyctl        # Windows

# 2. login
fly auth login

# 3. create the persistent state volume
fly launch --no-deploy             # follow prompts; app name in fly.toml can be changed
fly volumes create ezyai_state --size 1

# 4. inject the secret (never commit it)
fly secrets set TELEGRAM_BOT_TOKEN="your_token_from_botfather"

# 5. deploy
fly deploy

# 6. watch logs
fly logs
```

Then `/start` your bot in Telegram. State (watches/autopilots) is kept on the
`/data` volume and survives redeploys.

## Notes

- Signals are rule-based confluence (EMA alignment, ADX, RSI, MACD,
  Bollinger, stochastic, swing levels) — deterministic and explainable.
- Educational confluence only. Nothing here is financial advice; verify
  prices with your broker before acting.
- Monetization flow (channels, plans, payment gates) is designed for later
  — the service layer already separates delivery from commands.
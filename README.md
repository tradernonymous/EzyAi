# EzyAi

A Telegram bot that turns free market data into trading confluence: detailed
technical analysis with entry/exit levels, live per-pair alerts, fundamentals
with links, and randomized auto-signals — all driven by *style* (scalping /
intraday / swing) and *risk mode* (safe / normal / aggressive).

## Features

- **`/analyze`** — on-demand market analysis on a chosen pair + timeframe:
  trend, support/resistance, entry zone, stop loss, TP1/TP2, risk-reward,
  confidence score and exit rules.
- **`/watch PAIR STYLE MODE`** — live trade alerts for a specific pair.
  Style sets the timeframe and check frequency; mode sets risk per trade,
  R/R targets, confirmation strictness and daily signal limits.
- **`/fundamentals PAIR`** — fundamentals + source links for the selected
  pair (CoinGecko for crypto, derived stats for stocks & forex) plus recent
  headlines.
- **`/autopilot STYLE MODE`** — auto signals: the bot randomly picks a pair
  from the universe and releases signals based only on those two settings,
  capped by the mode daily limit.
- Also: `/watches`, `/unwatch`, `/quote`, `/help`.

## Markets & data sources (free, no API keys)

| Market | Provider | Examples |
| --- | --- | --- |
| Crypto | Binance public API (multi-host fallback) | `BTCUSDT`, `ETHUSDT`, `SOLUSDT` |
| Forex | Yahoo Finance chart API | `EURUSD`, `GBPUSD`, `USDJPY` |
| Stocks / ETFs | Yahoo Finance chart API | `AAPL`, `TSLA`, `SPY`, `QQQ` |

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
/analyze BTCUSDT        → then pick a timeframe
/watch EURUSD intraday safe
/watch AAPL swing aggressive
/unwatch EURUSD
/fundamentals SOLUSDT
/autopilot scalping aggressive
/stopautopilot
/quote ETHUSDT
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
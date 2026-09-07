"""
app/data/quality.py

Single source of truth for data-quality tiering and the scalping restriction.

RULE:
  Nothing here needs a paid feed. Crypto is priced from real exchange data
  (Binance, ccxt as fallback), and so is spot gold, via Binance's tokenized
  gold (constants.CFD_SPOT); FX, silver, oil, indices and stocks come from
  Yahoo's public chart API, which quotes mid prices only. Yahoo is therefore
  tiered DELAYED, but delayed is not the same as untradeable: what makes a
  scalp fictional is a stale bar, a shut session or a spread that eats the
  target, and each of those has its own gate:

    data/freshness.check()      last bar older than 4x its timeframe
    regime.scalp_session()      outside the pair's London/NY window
    constants.spread_bps()      static per-class spread, widens the stop
    strategy._spec()            drops a setup whose R:R the spread ate

  So the style restriction here is about the INSTRUMENT, not the feed:
  scalping is offered where a liquidity window exists (crypto around the
  clock, FX, metals, oil and the indices inside London/NY) and refused on
  stocks, where the cash session, the 15-minute quote lag and the overnight
  gap make scalp-level levels meaningless. constants.scalp_class() is that
  router and returns None for exactly those instruments.

  Synthetic data is the one absolute block: a demo series must never back a
  live signal for any style.

  Enforcement differs by surface:
    /watch, /autopilot   -> reject with a clear message
    universe scan        -> silently exclude
    /analyze             -> still runs, carries a warning line

DO NOT duplicate the pair->tier logic anywhere else. Every caller imports
from here. The static tier reuses DataHub.classify() as the pair->venue
router; the runtime tier reads the provider stamp that
DataHub.fetch_klines_ex() writes onto every candle it returns.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .. import constants
from . import provider as _prov


class Tier(str, Enum):
    """Data quality tier for a pair's feed."""

    REALTIME = "realtime"    # real exchange order-book data (Binance, ccxt)
    DELAYED = "delayed"      # Yahoo chart API — mid prices, no bid/ask
    SYNTHETIC = "synthetic"  # demo/fallback data — must never back a live signal


# Styles that need a live, tradeable instrument (see the module docstring).
STRICT_STYLES = {"scalping"}


# --- static classification ---------------------------------------------------

def static_tier(pair: str) -> Tier:
    """
    Tier a pair by its CONFIGURED provider, without touching the network.

    Uses DataHub.classify() as the single pair->venue router, so this can
    never disagree with what the fetch path actually does: crypto resolves
    to Binance/ccxt and is realtime; every other venue is served by Yahoo
    and is delayed.
    """
    kind = _prov.DataHub.classify(pair)
    if kind is None:
        return Tier.DELAYED  # conservative: an unknown pair is not realtime
    if kind == constants.KIND_CRYPTO:
        return Tier.REALTIME
    return Tier.DELAYED


def style_allowed(pair: str, style: str) -> bool:
    """Is this style permitted on this pair, by static config?

    Scalping needs an instrument with a liquidity window the bot can gate
    on. constants.scalp_class() returns that window's class for crypto, FX,
    metals, oil and the indices, and None for stocks and unknown symbols.
    """
    if style not in STRICT_STYLES:
        return True
    return constants.scalp_class(pair) is not None


def allowed_styles(pair: str, all_styles: list[str]) -> list[str]:
    """Styles to render as buttons for this pair. Prevention beats rejection."""
    return [s for s in all_styles if style_allowed(pair, s)]


# --- runtime classification --------------------------------------------------

def runtime_tier(source: Optional[str], data_mode: Optional[str] = None) -> Tier:
    """
    Tier the data that was ACTUALLY returned, after fetching.

    `source` is the provider stamp DataHub writes onto the candles it serves
    ("binance", "ccxt", "yahoo", "synthetic"). `data_mode` is the legacy
    live/demo flag. A pair configured as realtime that fell through to the
    synthetic provider is no longer realtime, and static_tier() cannot know
    that -- this check is the one that catches it.

    Unknown provenance is treated as DELAYED: usable, but never mistaken for
    exchange data.
    """
    if data_mode == "demo" or source == "synthetic":
        return Tier.SYNTHETIC
    if source in ("binance", "ccxt"):
        return Tier.REALTIME
    return Tier.DELAYED


def may_emit(pair: str, style: str, candles=None,
             source: Optional[str] = None,
             data_mode: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """
    Final gate before a signal goes out. Returns (ok, reason_if_blocked).

    `candles` may be the list the feed returned (its last element carries the
    provider stamp) or a single candle dict; `source`/`data_mode` accept the
    tags recorded on the analysis dict instead. Call this in the scheduler
    immediately before emission, for BOTH watch alerts and autopilot signals.
    """
    if candles is not None:
        if isinstance(candles, list):
            if candles:
                source = candles[-1].get("source", source)
        elif isinstance(candles, dict):
            source = candles.get("source", source)

    tier = runtime_tier(source, data_mode)

    if tier is Tier.SYNTHETIC:
        return False, f"{pair}: feed degraded to synthetic data — signal suppressed"

    if style in STRICT_STYLES and not style_allowed(pair, style):
        return False, f"{pair}: {style} is not offered on this instrument"

    return True, None


# --- user-facing copy --------------------------------------------------------

def rejection_message(pair: str, style: str) -> str:
    """Shown when a user tries to set up a blocked combination."""
    return (
        f"⚠️ <b>{style.capitalize()} not available on {pair}</b>\n\n"
        f"Scalping needs an instrument that trades in a deep, continuous "
        f"session. {pair} does not: its quotes stop overnight and reopen with "
        f"a gap, so scalp-level entries and stops would not match your "
        f"broker.\n\n"
        f"Scalping is available on crypto (BTCUSD, ETHUSD, SOLUSD and "
        f"others) around the clock, and on FX, metals, oil and the indices "
        f"(XAUUSD, WTI, US30 and others) inside the London/NY window.\n\n"
        f"For {pair}, try <b>intraday</b> or <b>swing</b> instead."
    )


def quality_warning(pair: str, style: str) -> Optional[str]:
    """
    Warning line appended to /analyze output. Returns None when not needed.

    Crypto is exchange data and needs no caveat. Everything else is Yahoo's
    mid price with an assumed spread, and the user must be able to see that
    from the message alone.
    """
    if static_tier(pair) is Tier.REALTIME:
        return None

    if style in STRICT_STYLES:
        return (
            "<i>Data note: this pair is priced from a delayed mid feed with "
            "an assumed spread. Scalp entries and stops may differ from your "
            "broker — check the quote before acting.</i>"
        )

    return (
        "<i>Data note: prices are from a delayed feed — verify levels with "
        "your broker before acting.</i>"
    )

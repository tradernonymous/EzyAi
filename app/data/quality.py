"""
app/data/quality.py

Single source of truth for data-quality tiering and the scalping restriction.

RULE (3C):
  Scalping is only available on pairs served by real exchange data (crypto via
  Binance / ccxt). Yahoo-backed pairs are delayed proxies with gaps and bad
  prints, which makes scalp-timeframe entry/SL/TP levels fiction.

  Enforcement differs by surface:
    /watch, /autopilot   -> reject with a clear message
    universe scan        -> silently exclude
    /analyze             -> still runs, carries a warning line

  Other styles are unchanged on all pairs.

DO NOT duplicate the pair->tier logic anywhere else. Every caller imports
from here. The static tier reuses DataHub.classify() as the router; the
runtime tier reads the provider stamp that DataHub.fetch_klines_ex() writes
onto every candle it returns.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .. import constants
from . import provider as _prov


class Tier(str, Enum):
    """Data quality tier for a pair's feed."""

    REALTIME = "realtime"    # real exchange order-book data (Binance, ccxt)
    DELAYED = "delayed"      # Yahoo chart API — delayed proxy, gaps, bad prints
    SYNTHETIC = "synthetic"  # demo/fallback data — must never back a live signal


# Styles that require REALTIME data.
STRICT_STYLES = {"scalping"}


# --- static classification ---------------------------------------------------

def static_tier(pair: str) -> Tier:
    """
    Tier a pair by its CONFIGURED provider, without touching the network.

    Uses DataHub.classify() as the single pair->venue router, so this can
    never disagree with what the fetch path actually does: crypto resolves to
    Binance/ccxt, every other venue to Yahoo.
    """
    kind = _prov.DataHub.classify(pair)
    if kind is None:
        return Tier.DELAYED  # conservative: an unknown pair is not realtime
    if kind == constants.KIND_CRYPTO:
        return Tier.REALTIME
    return Tier.DELAYED


def style_allowed(pair: str, style: str) -> bool:
    """Is this style permitted on this pair, by static config?"""
    if style not in STRICT_STYLES:
        return True
    return static_tier(pair) is Tier.REALTIME


def allowed_styles(pair: str, all_styles: list[str]) -> list[str]:
    """Styles to render as buttons for this pair. Prevention beats rejection."""
    return [s for s in all_styles if style_allowed(pair, s)]


# --- runtime classification --------------------------------------------------

def runtime_tier(source: Optional[str], data_mode: Optional[str] = None) -> Tier:
    """
    Tier the data that was ACTUALLY returned, after fetching.

    `source` is the provider stamp DataHub writes onto the candles it serves
    ("binance", "ccxt", "yahoo", "synthetic"). `data_mode` is the legacy
    live/demo flag. A pair configured as Binance that fell through to the
    synthetic provider is no longer realtime, and static_tier() cannot know
    that -- this check is the one that catches it.

    Unknown provenance is treated as DELAYED: usable for permissive styles,
    never enough for scalping.
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

    if style in STRICT_STYLES and tier is not Tier.REALTIME:
        return False, f"{pair}: {style} requires real-time data, got {tier.value}"

    return True, None


# --- user-facing copy --------------------------------------------------------

def rejection_message(pair: str, style: str) -> str:
    """Shown when a user tries to set up a blocked combination."""
    return (
        f"⚠️ <b>{style.capitalize()} not available on {pair}</b>\n\n"
        f"{pair} is priced from a delayed feed with gaps, so scalp-timeframe "
        f"entries and stops would not match your broker.\n\n"
        f"Scalping is available on crypto pairs (BTCUSD, ETHUSD, SOLUSD and "
        f"others) which use live exchange data.\n\n"
        f"For {pair}, try <b>intraday</b> or <b>swing</b> instead."
    )


def quality_warning(pair: str, style: str) -> Optional[str]:
    """
    Warning line appended to /analyze output. Returns None when not needed.

    /analyze is informational, so it still runs on delayed data — but the
    user must be able to see that from the message alone.
    """
    if static_tier(pair) is Tier.REALTIME:
        return None

    if style in STRICT_STYLES:
        return (
            "⚠️ <i>Data note: this pair uses a delayed feed. Scalp-level "
            "entries and stops may differ materially from your broker. "
            "Live scalp alerts are not offered on this pair.</i>"
        )

    return (
        "<i>Data note: prices are from a delayed feed — verify levels with "
        "your broker before acting.</i>"
    )
"""Signal outcome tracking (Phase 1, brief 1.1-1.9).

Every delivered signal (watch + autopilot) is written to a real table
(signals.db next to state.json), resolved first-touch by the Resolver,
and reported to the admin with /stats.

Nothing here is imported by the telegram message path itself; the bot
records after a successful send and the scheduler runs the resolver in
its background tick, so outcome tracking can never break signal delivery.
"""
from .resolver import Resolver
from .store import OutcomeStore

__all__ = ["OutcomeStore", "Resolver"]
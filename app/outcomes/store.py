"""SQLite store for delivered signals and their resolved outcomes.

Schema mirrors the Phase-1 brief (engine fields named per the live bot):
  status:   'open' until the resolver decides
            'sl'/'tp1'/'tp2'/'expired' afterwards
  r_multiple: signed R for 'sl'/'tp1'/'tp2'; price-based R at expiry.
  same_candle_ambig: the SL and a TP both printed in one bar; resolved
            conservatively to SL and flagged (brief 1.8).
  component_scores: JSON snapshot of the confidence inputs at emission
            time, the calibration fuel for Phase 4.
  data_source: 'live' or 'demo' as stamped on the signal (brief 1.4).

One connection per operation (WAL, default busy timeout) so it is safe
from the asyncio bot loop and any worker thread without sharing a
connection object.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'watch',
    created_at REAL NOT NULL,
    pair TEXT NOT NULL,
    style TEXT NOT NULL,
    mode TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    rr_target REAL NOT NULL,
    confidence REAL NOT NULL,
    component_scores TEXT NOT NULL DEFAULT '{}',
    data_source TEXT NOT NULL DEFAULT 'live',
    spread_estimate REAL,
    status TEXT NOT NULL DEFAULT 'open',
    same_candle_ambig INTEGER NOT NULL DEFAULT 0,
    resolved_at REAL,
    exit_price REAL,
    r_multiple REAL
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_style ON signals(style);
"""


class OutcomeStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
        finally:
            con.close()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def record(self, chat_id, signal, source="watch"):
        """Insert one delivered signal. Returns the new row id."""
        ind = signal.get("component_scores") or {}
        with self._conn() as db:
            cur = db.execute(
                "INSERT INTO signals (chat_id, source, created_at, pair, style, "
                "mode, direction, entry, stop_loss, tp1, tp2, rr_target, "
                "confidence, component_scores, data_source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(chat_id), source, float(signal["ts"]),
                 str(signal["pair"]).upper(), signal["style"],
                 signal["mode"], signal["side"], float(signal["entry"]),
                 float(signal["sl"]), float(signal["tp1"]), float(signal["tp2"]),
                 float(signal["rr"]), float(signal["confidence"]),
                 json.dumps({k: v for k, v in ind.items() if v is not None},
                            default=str),
                 str(signal.get("data_mode", "live"))))
            return cur.lastrowid

    def open_signals(self):
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM signals WHERE status='open' "
                "ORDER BY created_at, id").fetchall()
            return [dict(r) for r in rows]

    def mark_resolved(self, sig_id, status, exit_price, r_multiple, ambig=0):
        """Idempotent: only an open row can be resolved."""
        with self._conn() as db:
            db.execute(
                "UPDATE signals SET status=?, resolved_at=?, exit_price=?, "
                "r_multiple=?, same_candle_ambig=? "
                "WHERE id=? AND status='open'",
                (status, time.time(), exit_price, r_multiple, int(bool(ambig)),
                 int(sig_id)))

    def count(self, where="1=1", *params):
        with self._conn() as db:
            return db.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where}", params
            ).fetchone()[0]

    def stats(self):
        """One admin/ready summary of everything tracked so far."""
        out = {"total": 0, "open": 0, "resolved": 0, "win_rate_pct": None,
               "avg_r": None, "total_r": None, "worst_streak": 0,
               "by_style": [], "by_mode": [], "by_pair": [], "conf_buckets": []}
        with self._conn() as db:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(status='open'),0), "
                "COALESCE(SUM(status!='open'),0) FROM signals").fetchone()
            if not row or not row[0]:
                return out
            out.update(total=row[0], open=row[1], resolved=row[2])
            agg = db.execute(
                "SELECT COUNT(*), AVG(r_multiple), SUM(r_multiple), "
                "COALESCE(SUM(r_multiple>0.0),0) "
                "FROM signals WHERE status!='open' AND r_multiple IS NOT NULL"
            ).fetchone()
            n, avg_r, tot_r, wins = agg
            if n:
                out["avg_r"] = avg_r
                out["total_r"] = tot_r
                out["win_rate_pct"] = 100.0 * wins / n
            streak = best = 0
            for r in db.execute(
                    "SELECT r_multiple FROM signals WHERE status!='open' "
                    "AND r_multiple IS NOT NULL ORDER BY created_at, id"):
                streak = 0 if r[0] > 0 else streak + 1
                best = max(best, streak)
            out["worst_streak"] = best

            def group(col):
                return [
                    {"k": row0[0], "n": row0[1], "resolved": row0[2],
                     "wins": row0[3],
                     "win_pct": 100.0 * row0[3] / row0[2] if row0[2] else None,
                     "avg_r": row0[4]}
                    for row0 in db.execute(
                        f"SELECT {col}, COUNT(*), "
                        "COALESCE(SUM(status!='open'),0), "
                        "COALESCE(SUM(status!='open' AND r_multiple>0.0),0), "
                        "AVG(CASE WHEN status!='open' AND r_multiple IS NOT NULL "
                        "THEN r_multiple END) "
                        f"FROM signals GROUP BY {col} ORDER BY 4 DESC").fetchall()
                ]

            out["by_style"] = group("style")
            out["by_mode"] = group("mode")
            out["by_pair"] = group("pair")[:8]
            for lo, n, resolved, wins in db.execute(
                    "SELECT CAST(confidence/20 AS INT)*20, COUNT(*), "
                    "COALESCE(SUM(status!='open'),0), "
                    "COALESCE(SUM(status!='open' AND r_multiple>0.0),0) "
                    "FROM signals GROUP BY 1 ORDER BY 1"):
                out["conf_buckets"].append({
                    "lo": lo, "n": n, "resolved": resolved, "wins": wins,
                    "win_pct": 100.0 * wins / resolved if resolved else None,
                })
        return out
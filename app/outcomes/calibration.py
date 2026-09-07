"""Phase-4 calibration: realised hit rate & expectancy per confidence bucket.

Reads only resolved live-data signals (data_source='live', status != 'open',
r_multiple IS NOT NULL). The confidence score is a 0-100 composite, so a
bucket is treated as the instrument's own predicted payoff band and compared
against what first-touch outcomes actually returned (R-weighted).

A bucket is only *credible* once MIN_RESOLVED outcomes pile up. With enough
data, recommend() proposes raising the emission gate for a style/mode when a
credible tier that is actually being emitted pays less than MIN_EXPECTANCY.
Nothing is ever auto-applied: the report is input for a human decision, and
demo/synthetic source rows can never influence production tuning.
"""
from .. import constants

# A confidence bucket needs this many resolved live signals before it counts.
MIN_RESOLVED = 30
# A tier must at least beat this many R on average or the gate gets raised.
MIN_EXPECTANCY_R = 0.15
# Broadening step for the confidence x-axis (10-point bands).
BUCKET_STEP = 10

# Resolved, live-data rows bucketed by style/mode and confidence band.
CURVE_SQL = """
SELECT style, mode,
       CAST((confidence / {step}) AS INT) * {step} AS lo,
       COUNT(*) AS n,
       COALESCE(SUM(status != 'open'), 0) AS resolved,
       COALESCE(SUM(status != 'open' AND r_multiple > 0), 0) AS wins,
       AVG(CASE WHEN r_multiple IS NOT NULL THEN r_multiple END) AS avg_r,
       AVG(CASE WHEN r_multiple > 0 THEN r_multiple END) AS avg_win_r,
       AVG(CASE WHEN r_multiple <= 0 THEN r_multiple END) AS avg_loss_r
FROM signals
WHERE data_source = 'live' AND status != 'open' AND r_multiple IS NOT NULL
GROUP BY style, mode, CAST((confidence / {step}) AS INT) * {step}
ORDER BY style, mode, lo
""".format(step=BUCKET_STEP)


def bucket_lo(confidence):
    return int(confidence // BUCKET_STEP) * BUCKET_STEP


def _expectancy(row):
    """R-weighted expected payoff = p_win*avg_win_r + p_loss*avg_loss_r.

    Handles buckets that only ever won (avg_loss_r NULL) or only ever lost
    (avg_win_r NULL): the missing side contributes its fair probability.
    """
    resolved = row["resolved"]
    if not resolved:
        return None
    wins = row["wins"]
    p_win = wins / resolved
    exp = 0.0
    if p_win > 0 and row.get("avg_win_r") is not None:
        exp += p_win * row["avg_win_r"]
    if p_win < 1 and row.get("avg_loss_r") is not None:
        exp += (1 - p_win) * row["avg_loss_r"]
    return exp


class Calibration:
    def __init__(self, store):
        self.store = store

    def curve(self):
        """Bucketed calibration curve for resolved live signals."""
        rows = self.store.query(CURVE_SQL)
        out = []
        for r in rows:
            r = dict(r)
            r["win_pct"] = (100.0 * r["wins"] / r["resolved"]
                            if r["resolved"] else None)
            r["expectancy"] = _expectancy(r)
            r["credible"] = r["resolved"] >= MIN_RESOLVED
            out.append(r)
        return out

    def overall(self):
        sql = ("SELECT COUNT(*) AS n, "
               "COALESCE(SUM(status != 'open'), 0) AS resolved, "
               "COALESCE(SUM(status != 'open' AND r_multiple > 0), 0) AS wins, "
               "AVG(CASE WHEN r_multiple IS NOT NULL THEN r_multiple END) "
               "AS avg_r "
               "FROM signals WHERE data_source='live' AND status != 'open' "
               "AND r_multiple IS NOT NULL")
        rows = self.store.query(sql)
        if not rows or not rows[0]["resolved"]:
            return {"n": 0, "resolved": 0, "wins": 0, "win_pct": None,
                    "avg_r": None}
        r = rows[0]
        r["win_pct"] = 100.0 * r["wins"] / r["resolved"]
        return r

    def recommend(self):
        """Suggest gate raises where a credible emitted tier under-pays."""
        by_grp = {}
        for row in self.curve():
            by_grp.setdefault((row["style"], row["mode"]), []).append(row)
        out = []
        for (style, mode), buckets in sorted(by_grp.items()):
            gate = constants.SIGNAL_THRESHOLDS[style][mode]
            glo = bucket_lo(gate)
            emitted = [b for b in buckets if b["lo"] >= glo]
            credible = [b for b in emitted if b["credible"]]
            if not credible:
                continue  # not enough data to judge this gate yet
            for b in sorted(credible, key=lambda x: x["lo"]):
                if b["expectancy"] is None:
                    continue
                if b["expectancy"] >= MIN_EXPECTANCY_R:
                    continue
                out.append({
                    "style": style, "mode": mode,
                    "current_gate": gate, "proposed_gate": b["lo"],
                    "n": b["resolved"], "win_pct": b["win_pct"],
                    "avg_r": b["avg_r"], "expectancy": b["expectancy"],
                    "verdict": ("suspend" if b["lo"] >= 90
                                else "raise"),
                })
                break
        return out
"""Tiny VADER-style headline sentiment scorer (no downloads, no models).

Scores English financial headlines in [-1, 1] from a hand-built lexicon
with negation handling and punctuation/caps emphasis. Used only as a
small bounded confluence input (+/- a few confidence points); it cannot
be backtested (no historical headline archive), so its weight stays
small and every adjustment is disclosed in the reasons.
"""

BULLISH = {
    "surge": 2.0, "soar": 2.0, "rally": 1.8, "bullish": 2.0,
    "bull": 1.5, "gain": 1.2, "gains": 1.2, "jump": 1.5, "climb": 1.2,
    "record": 1.5, "high": 0.8, "breakout": 1.8, "breaks": 1.0,
    "upgrade": 1.5, "upgraded": 1.5, "beat": 1.5, "beats": 1.5,
    "profit": 1.2, "profits": 1.2, "growth": 1.2, "boom": 1.8,
    "optimism": 1.2, "optimistic": 1.2, "strong": 1.0, "strength": 1.0,
    "buy": 1.2, "bulls": 1.5, "rebound": 1.2, "recovery": 1.2,
    "support": 0.6, "backs": 0.6, "approv": 1.0, "approve": 1.0,
    "approved": 1.0, "deal": 0.8, "partnership": 0.8, "launch": 0.8,
    "win": 1.0, "outperform": 1.5, "overweight": 1.0, "accumulate": 1.0,
}

BEARISH = {
    "plunge": -2.0, "crash": -2.2, "bearish": -2.0, "bear": -1.5,
    "fall": -1.2, "falls": -1.2, "drop": -1.2, "drops": -1.2,
    "slide": -1.2, "slump": -1.5, "tumble": -1.5, "low": -0.8,
    "breakdown": -1.8, "downgrade": -1.5, "downgraded": -1.5, "miss": -1.5,
    "misses": -1.5, "loss": -1.2, "losses": -1.2, "fear": -1.2,
    "panic": -1.8, "weak": -1.0, "weakness": -1.0, "sell": -1.2,
    "bears": -1.5, "selloff": -1.8, "sell-off": -1.8, "warning": -1.2,
    "warns": -1.2, "risk": -0.8, "risks": -0.8, "probe": -0.6,
    "lawsuit": -1.5, "fraud": -2.0, "hack": -1.8, "ban": -1.5,
    "underperform": -1.5, "underweight": -1.0, "cut": -1.0, "cuts": -1.0,
    "layoff": -1.2, "layoffs": -1.2, "recession": -1.8, "inflation": -0.8,
    "default": -1.8, "bankrupt": -2.0,
}

NEGATIONS = {"not", "no", "never", "n't", "without", "fails", "failed",
             "denies", "denied", "rejects", "rejected", "lack", "lacks"}

AMPLIFIERS = {"very": 0.3, "strongly": 0.4, "sharply": 0.4, "massively": 0.5,
              "slightly": -0.4, "mildly": -0.3, "barely": -0.5}


def _tokens(text):
    return __import__("re").findall(r"[a-zA-Z']+", text.lower())


def score_text(text):
    """Compound sentiment of one headline in [-1, 1]."""
    if not text:
        return 0.0
    toks = _tokens(text)
    total, hits = 0.0, 0
    for i, tok in enumerate(toks):
        val = BULLISH.get(tok, BEARISH.get(tok, 0.0))
        if val == 0.0:
            continue
        window = toks[max(0, i - 3):i]
        if any(w in NEGATIONS or w.endswith("n't") for w in window):
            val = -val * 0.8
        if i > 0 and toks[i - 1] in AMPLIFIERS:
            val = val * (1.0 + AMPLIFIERS[toks[i - 1]])
        total += val
        hits += 1
    if hits == 0:
        return 0.0
    avg = total / hits
    compound = avg / 2.0
    if "!" in text:
        compound *= 1.2
    if sum(1 for c in text if c.isupper()) > len(text) * 0.4 and len(text) > 10:
        compound *= 1.1
    return max(-1.0, min(1.0, compound))


def score_headlines(headlines):
    """Mean compound over headline dicts with 'title' keys. None if empty."""
    scores = [score_text(h.get("title", "")) for h in (headlines or [])]
    scores = [s for s in scores if s != 0.0]
    if not scores:
        return None
    return sum(scores) / len(scores)


def describe(compound):
    if compound is None:
        return None
    if compound >= 0.25:
        return f"Headline sentiment leans bullish ({compound:+.2f})"
    if compound <= -0.25:
        return f"Headline sentiment leans bearish ({compound:+.2f})"
    return f"Headline sentiment mixed ({compound:+.2f})"

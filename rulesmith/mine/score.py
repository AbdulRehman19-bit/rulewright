"""Score step: weight each signal's detector confidence by recency, drop weak ones."""

from datetime import datetime

from rulesmith.mine.signals import Signal

HALF_LIFE_DAYS = 180.0
THRESHOLD = 0.2


def recency_weight(ts: datetime, now: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age_days = max((now - ts).total_seconds() / 86400, 0.0)
    return float(0.5 ** (age_days / half_life_days))


def score_signals(
    signals: list[Signal],
    now: datetime,
    threshold: float = THRESHOLD,
    half_life_days: float = HALF_LIFE_DAYS,
) -> list[Signal]:
    """Detectors set `score` to their confidence; apply this once per pipeline run."""
    out = []
    for s in signals:
        weighted = s.score * recency_weight(s.ts, now, half_life_days)
        if weighted >= threshold:
            out.append(s.model_copy(update={"score": weighted}))
    return out

"""Evaluate historical predictions and cautiously recalibrate future scores."""

import math


def adjusted_probability(probability: float, offset: float) -> float:
    probability = min(max(probability, 1e-6), 1 - 1e-6)
    log_odds = math.log(probability / (1 - probability)) + offset
    if log_odds >= 0:
        return 1 / (1 + math.exp(-log_odds))
    e = math.exp(log_odds)
    return e / (1 + e)


def brier_score(rows, offset=0.0):
    return sum((adjusted_probability(p, offset) - y) ** 2 for _, p, y in rows) / len(rows)


def calibration_offset(db, target="profit") -> float:
    """Fit one score correction on old outcomes; accept only on later hours.

    All markets from a given hour stay in the same train or validation group.
    A week of outcomes is required before any automatic adjustment is possible.
    """
    if target == "profit":
        query = ("SELECT bar_time,probability,actual_profit FROM profit_predictions "
                 "WHERE actual_profit IS NOT NULL ORDER BY bar_time DESC,product LIMIT 100000")
    elif target == "direction":
        query = ("SELECT bar_time,probability,actual_up FROM predictions "
                 "WHERE actual_up IS NOT NULL ORDER BY bar_time DESC,product LIMIT 100000")
    else:
        raise ValueError(f"Unknown forecast target: {target}")
    rows = [tuple(row) for row in db.execute(query)][::-1]
    hours = sorted({int(row[0]) for row in rows})
    if len(rows) < 1000 or len(hours) < 168:
        return 0.0
    cutoff = hours[int(len(hours) * 0.8)]
    train = [(t, p, y) for t, p, y in rows if int(t) < cutoff]
    validation = [(t, p, y) for t, p, y in rows if int(t) >= cutoff]
    if len(train) < 500 or len(validation) < 200 or len({t for t, _, _ in validation}) < 24:
        return 0.0
    offset = 0.0
    for _ in range(20):
        predicted = [adjusted_probability(p, offset) for _, p, _ in train]
        gradient = sum(q - y for q, (_, _, y) in zip(predicted, train))
        curvature = sum(q * (1 - q) for q in predicted)
        if curvature < 1e-9:
            return 0.0
        step = gradient / curvature
        offset = max(-0.5, min(0.5, offset - step))
        if abs(step) < 1e-6:
            break
    if brier_score(validation, offset) <= brier_score(validation) * 0.98:
        return offset
    return 0.0

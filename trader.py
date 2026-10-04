#!/usr/bin/env python3
"""Offline crypto signal model and chronological paper backtest.

CSV columns: timestamp,open,high,low,close,volume (oldest first).
No exchange credentials or order placement are used.
"""

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Bar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def read_bars(path: Path) -> list[Bar]:
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV must contain: {', '.join(sorted(required))}")
        bars = []
        previous = None
        for row in reader:
            timestamp = row["timestamp"].strip()
            if not timestamp or (previous is not None and timestamp <= previous):
                raise ValueError("Timestamps must be nonempty, unique, and sorted oldest first")
            values = [float(row[name]) for name in ("open", "high", "low", "close", "volume")]
            o, h, l, c, v = values
            if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0:
                raise ValueError(f"Invalid price or volume at {timestamp}")
            if h < max(o, c, l) or l > min(o, c, h):
                raise ValueError(f"Inconsistent OHLC at {timestamp}")
            bars.append(Bar(timestamp, o, h, l, c, v))
            previous = timestamp
    if len(bars) < 60:
        raise ValueError("At least 60 bars are required")
    return bars


def features(bars: list[Bar], t: int) -> list[float]:
    """Use data available at the close of bar t only."""
    close = bars[t].close
    returns = [math.log(bars[i].close / bars[i - 1].close) for i in range(t - 4, t + 1)]
    mean_volume = sum(b.volume for b in bars[t - 19:t + 1]) / 20
    return [
        returns[-1],
        sum(returns),
        math.log(close / bars[t - 10].close),
        math.log(close / bars[t - 20].close),
        (bars[t].high - bars[t].low) / close,
        (bars[t].close - bars[t].open) / bars[t].open,
        math.log1p(bars[t].volume) - math.log1p(mean_volume),
    ]


def standardize(train: list[list[float]], test: list[list[float]]):
    means = [sum(row[j] for row in train) / len(train) for j in range(len(train[0]))]
    scales = [
        max(math.sqrt(sum((row[j] - means[j]) ** 2 for row in train) / len(train)), 1e-12)
        for j in range(len(means))
    ]
    transform = lambda rows: [
        [(row[j] - means[j]) / scales[j] for j in range(len(means))] for row in rows
    ]
    return transform(train), transform(test)


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def fit_logistic(x: list[list[float]], y: list[int], epochs: int = 600) -> list[float]:
    weights = [0.0] * (len(x[0]) + 1)
    for step in range(epochs):
        gradient = [0.0] * len(weights)
        for row, target in zip(x, y):
            error = sigmoid(weights[0] + sum(a * b for a, b in zip(weights[1:], row))) - target
            gradient[0] += error
            for j, value in enumerate(row, 1):
                gradient[j] += error * value
        rate = 0.15 / (1 + step / 200)
        weights[0] -= rate * gradient[0] / len(x)
        for j in range(1, len(weights)):
            weights[j] -= rate * (gradient[j] / len(x) + 0.01 * weights[j])
    return weights


def predict(weights: list[float], row: list[float]) -> float:
    return sigmoid(weights[0] + sum(a * b for a, b in zip(weights[1:], row)))


def net_bar_return(bar: Bar, side_cost: float) -> float:
    """Estimated return for buying at the open and selling at the close."""
    return bar.close * (1 - side_cost) / (bar.open * (1 + side_cost)) - 1


def backtest(bars: list[Bar], train_fraction: float, fee_bps: float, slippage_bps: float,
             threshold: float, allocation: float, assumed_spread_bps: float = 20) -> dict:
    # A row at t predicts the following bar's open-to-close move. The order is
    # assumed to fill at that open, so the model never sees the traded bar.
    indices = list(range(20, len(bars) - 1))
    split = int(len(indices) * train_fraction)
    if split < 30 or len(indices) - split < 10:
        raise ValueError("Need at least 30 training and 10 test examples")
    train_ids, test_ids = indices[:split], indices[split:]
    train_x, test_x = standardize(
        [features(bars, t) for t in train_ids],
        [features(bars, t) for t in test_ids],
    )
    cost = (fee_bps + slippage_bps + assumed_spread_bps / 2) / 10_000
    train_y = [int(net_bar_return(bars[t + 1], cost) > 0) for t in train_ids]
    weights = fit_logistic(train_x, train_y)
    probabilities = [predict(weights, row) for row in test_x]
    test_returns = [net_bar_return(bars[t + 1], cost) for t in test_ids]
    profitable_rate = sum(value > 0 for value in test_returns) / len(test_returns)
    accuracy = sum((p >= 0.5) == (value > 0)
                   for p, value in zip(probabilities, test_returns)) / len(test_ids)
    equity = 1.0
    always_buy_equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    trades = 0
    for p, realized in zip(probabilities, test_returns):
        always_buy_equity *= 1 + allocation * realized
        if p >= threshold:
            equity *= 1 + allocation * realized
            trades += 1
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    return {
        "train_bars": len(train_ids), "test_bars": len(test_ids),
        "test_start": bars[test_ids[0] + 1].timestamp,
        "test_end": bars[test_ids[-1] + 1].timestamp,
        "profitable_bar_rate": profitable_rate,
        "profitability_accuracy": accuracy,
        "always_skip_accuracy": 1 - profitable_rate,
        "trades": trades,
        "return_pct": (equity - 1) * 100,
        "always_buy_return_pct": (always_buy_equity - 1) * 100,
        "max_drawdown_pct": max_drawdown * 100,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="OHLCV CSV sorted oldest first")
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--assumed-spread-bps", type=float, default=20)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--allocation", type=float, default=0.1,
                        help="Fraction of equity used for each signal")
    args = parser.parse_args()
    if not 0.5 <= args.train_fraction <= 0.9:
        parser.error("--train-fraction must be between 0.5 and 0.9")
    if args.fee_bps < 0 or args.slippage_bps < 0 or args.assumed_spread_bps < 0:
        parser.error("Costs must be nonnegative")
    if not 0.5 <= args.threshold <= 1 or not 0 < args.allocation <= 1:
        parser.error("Threshold must be 0.5–1 and allocation must be >0–1")
    try:
        result = backtest(read_bars(args.csv), args.train_fraction, args.fee_bps,
                          args.slippage_bps, args.threshold, args.allocation,
                          args.assumed_spread_bps)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for key, value in result.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")


if __name__ == "__main__":
    main()

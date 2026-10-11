#!/usr/bin/env python3
"""Chronological replay of recorded next-candle forecasts after estimated costs."""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path


def replay(hours, strategy, threshold=0.55, allocation=0.1, max_positions=5,
           min_dollar_volume=100_000):
    equity = 1.0
    entries = 0
    for hour in hours:
        eligible = [row for row in hour if row["dollar_volume"] >= min_dollar_volume]
        if strategy == "liquid":
            chosen = sorted(eligible, key=lambda row: row["dollar_volume"], reverse=True)
        elif strategy in ("direction", "profit"):
            key = strategy + "_probability"
            chosen = sorted((row for row in eligible if row[key] >= threshold),
                            key=lambda row: row[key], reverse=True)
        elif strategy == "cash":
            chosen = []
        else:
            raise ValueError(strategy)
        chosen = chosen[:max_positions]
        equity *= 1 + allocation * sum(row["net_return"] for row in chosen)
        entries += len(chosen)
    return equity - 1, entries


def load_hours(db):
    rows = db.execute("""
        SELECT p.bar_time, p.product, p.probability AS direction_probability,
               q.probability AS profit_probability,
               p.dollar_volume, q.realized_return AS net_return
        FROM predictions AS p
        JOIN profit_predictions AS q
          ON q.product=p.product AND q.bar_time=p.bar_time
        WHERE p.actual_up IS NOT NULL AND q.actual_profit IS NOT NULL
        ORDER BY p.bar_time, p.product
    """)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["bar_time"]].append(row)
    return [grouped[hour] for hour in sorted(grouped)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("paper.sqlite3"))
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"Wallet does not exist: {args.db}")
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        hours = load_hours(db)
        account = db.execute("SELECT start_cash FROM account WHERE id=1").fetchone()
        cycles = list(db.execute("SELECT time,equity FROM cycles ORDER BY time"))
    if len(hours) < 10:
        parser.error("Need at least 10 distinct resolved forecast hours")
    if account and len(cycles) >= 2:
        cycle_split = max(1, int(len(cycles) * 0.7))
        earlier = cycles[cycle_split - 1]["equity"] / account["start_cash"] - 1
        later = cycles[-1]["equity"] / cycles[cycle_split - 1]["equity"] - 1
        print(f"Actual paper wallet, {len(cycles)} scans, after quoted spread and configured costs:")
        print(f"  Earlier return {earlier:+.2%}; later return {later:+.2%}; cash +0.00%")
        print("  Equity uses the bot's sale-price estimate for open positions.")
    split = int(len(hours) * 0.7)
    print(f"Signal diagnostic: {len(hours)} resolved forecast hours; earlier: {split}; later: {len(hours)-split}")
    print("Hypothetical entry is the next hourly candle open; the actual scan often occurred later.")
    print("Returns include estimated fees, slippage, and spread, but are not executable ticker fills.")
    for label, sample in (("Earlier", hours[:split]), ("Later holdout", hours[split:])):
        print(f"{label} ({len(sample)} observed hours):")
        for strategy in ("cash", "liquid", "direction", "profit"):
            result, entries = replay(sample, strategy)
            print(f"  {strategy:9} return {result:+.2%}; entries {entries}")


if __name__ == "__main__":
    main()

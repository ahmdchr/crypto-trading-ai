#!/usr/bin/env python3
"""Compare shadow baskets entered and exited at real scan-time public quotes."""

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

STRATEGIES = ("liquid", "direction", "profit")
ALLOCATION = 0.1


def paired_scans(scans, entries):
    """Keep scan groups only when every selected quote has a later exit quote."""
    grouped = defaultdict(list)
    for row in entries:
        grouped[row["cycle_id"]].append(row)
    complete = []
    for scan in scans:
        rows = grouped[scan["cycle_id"]]
        if all(row["net_return"] is not None for row in rows):
            complete.append(rows)
    return complete


def print_sample(label, samples):
    print(f"{label}: {len(samples)} comparable scans")
    for strategy in ("cash",) + STRATEGIES:
        baskets = [[row for row in sample if row["strategy"] == strategy]
                   for sample in samples]
        returns = [ALLOCATION * sum(row["net_return"] for row in basket)
                   for basket in baskets]
        entries = [row for basket in baskets for row in basket]
        holding = [
            (datetime.fromisoformat(row["exit_time"]) -
             datetime.fromisoformat(row["entry_time"])).total_seconds() / 3600
            for row in entries
        ]
        average = mean(returns) if returns else 0.0
        duration = f"; mean hold {mean(holding):.1f}h" if holding else ""
        print(f"  {strategy:9} mean basket return {average:+.2%}; "
              f"entries {len(entries)}{duration}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("paper.sqlite3"))
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"Wallet does not exist: {args.db}")
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='shadow_scans'").fetchone():
            parser.error("This wallet predates scan-time quote tracking")
        scans = list(db.execute("SELECT * FROM shadow_scans ORDER BY cycle_id"))
        entries = list(db.execute("SELECT * FROM shadow_entries ORDER BY cycle_id,"
                                  "strategy,rank"))
    complete = paired_scans(scans, entries)
    pending = sum(row["net_return"] is None for row in entries)
    print(f"Scans: {len(scans)}; comparable: {len(complete)}; "
          f"excluded while exits pending: {len(scans)-len(complete)}; "
          f"pending entries: {pending}")
    print("Entry at recorded ask, exit at next available scan's bid; configured fees "
          "and slippage included on both sides.")
    print("Public quotes do not guarantee full fills. A basket return allocates 10% "
          "per selected market; overlapping pending baskets are not compounded.")
    print("Incomplete scans are excluded; missing exit quotes can affect the sample.")
    if not complete:
        print("No resolved comparison yet; wait for a later scan with valid exit quotes.")
    elif len(complete) < 20:
        print_sample("Preliminary", complete)
    else:
        split = int(len(complete) * 0.7)
        print_sample("Earlier", complete[:split])
        print_sample("Later holdout", complete[split:])


if __name__ == "__main__":
    main()

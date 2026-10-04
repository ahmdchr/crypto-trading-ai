#!/usr/bin/env python3
"""Print the persistent paper wallet and most recent simulated trades."""

import argparse
import sqlite3
from pathlib import Path

from learning import calibration_offset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("paper.sqlite3"))
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"Wallet does not exist: {args.db}")
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
    latest = db.execute("SELECT * FROM cycles ORDER BY time DESC LIMIT 1").fetchone()
    print(f"Mock cash: ${account['cash']:,.2f} (started with ${account['start_cash']:,.2f})")
    if latest:
        print(f"Last scan: {latest['time']} | {latest['scanned']} markets | "
              f"{latest['candidates']} signals | equity ${latest['equity']:,.2f}")
    else:
        print("First scan is still pending")
    print("Open positions:")
    positions = list(db.execute("SELECT * FROM positions ORDER BY product"))
    for row in positions:
        print(f"  {row['product']}: {row['quantity']:.8f} units, entry ${row['entry_price']:.6f}")
    if not positions:
        print("  none")
    print("Recent paper trades:")
    trades = list(db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 10"))
    for row in trades:
        print(f"  {row['time']} {row['side'].upper()} {row['product']} "
              f"{row['quantity']:.8f} @ ${row['price']:.6f}")
    if not trades:
        print("  none")
    prediction_stats = db.execute(
        "SELECT COUNT(*) AS n,"
        "AVG((probability-actual_up)*(probability-actual_up)) AS brier,"
        "AVG((probability>=0.5)=actual_up) AS accuracy "
        "FROM predictions WHERE actual_up IS NOT NULL"
    ).fetchone()
    pending = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE actual_up IS NULL"
    ).fetchone()[0]
    print(f"Legacy direction forecasts: {prediction_stats['n']} resolved, {pending} pending")
    if prediction_stats["n"]:
        print(f"  Direction accuracy: {prediction_stats['accuracy']:.1%}; "
              f"Brier score: {prediction_stats['brier']:.3f}")
    profit_stats = db.execute(
        "SELECT COUNT(*) AS n,"
        "AVG((probability-actual_profit)*(probability-actual_profit)) AS brier,"
        "AVG((probability>=0.5)=actual_profit) AS accuracy,"
        "AVG(actual_profit) AS base_rate "
        "FROM profit_predictions WHERE actual_profit IS NOT NULL"
    ).fetchone()
    pending_profit = db.execute(
        "SELECT COUNT(*) FROM profit_predictions WHERE actual_profit IS NULL"
    ).fetchone()[0]
    print(f"After-cost forecasts: {profit_stats['n']} resolved, {pending_profit} pending")
    if profit_stats["n"]:
        print(f"  Accuracy: {profit_stats['accuracy']:.1%}; "
              f"profitable candle rate: {profit_stats['base_rate']:.1%}; "
              f"Brier score: {profit_stats['brier']:.3f}")
        shadow = db.execute(
            "SELECT COUNT(*) AS n, AVG(realized_return) AS mean_return, "
            "SUM(realized_return) AS total_return FROM profit_predictions "
            "WHERE actual_profit IS NOT NULL AND used_probability>=0.55"
        ).fetchone()
        if shadow["n"]:
            print(f"  Shadow entries at 0.55 threshold: {shadow['n']}; "
                  f"mean estimated net candle return {shadow['mean_return']:.2%}")
        else:
            print("  Shadow entries at 0.55 threshold: none")
    closed = list(db.execute(
        "SELECT model_version,execution_version, COUNT(*) AS n, SUM(dollar_pnl) AS pnl, "
        "AVG(net_return>0) AS win_rate FROM paper_outcomes "
        "GROUP BY model_version,execution_version ORDER BY model_version,execution_version"
    ))
    if not closed:
        print("Closed paper trades: none yet")
    for row in closed:
        print(f"Closed paper trades ({row['model_version']}, {row['execution_version']}): "
              f"{row['n']}; "
              f"win rate {row['win_rate']:.1%}; net P&L ${row['pnl']:,.2f}")
    offset = calibration_offset(db)
    print(f"Validated score adjustment: {offset:+.3f}" if offset else
          "Validated score adjustment: inactive (more history needed or no improvement)")


if __name__ == "__main__":
    main()

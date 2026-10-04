#!/usr/bin/env python3
"""Autonomous Coinbase USD spot scanner with a persistent paper wallet."""

import argparse
import fcntl
import json
import logging
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from learning import adjusted_probability, calibration_offset
from trader import Bar, features, fit_logistic, net_bar_return, predict, standardize

BASE_URL = "https://api.exchange.coinbase.com"
INTERVAL = 3600
LOGGER = logging.getLogger("paper_bot")


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def spread_bps(self):
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 10_000


class Exchange:
    def __init__(self, max_quote_age_seconds=300):
        self.last_request = 0.0
        self.max_quote_age_seconds = max_quote_age_seconds

    def get(self, path: str, params: dict | None = None):
        elapsed = time.monotonic() - self.last_request
        if elapsed < 0.15:  # Below Coinbase's documented public rate limit.
            time.sleep(0.15 - elapsed)
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "crypto-paper-bot/0.1"})
        for attempt in range(3):
            try:
                self.last_request = time.monotonic()
                with urllib.request.urlopen(request, timeout=15) as response:
                    return json.load(response)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt == 2:
                    raise RuntimeError(f"GET {path} failed: {error}") from error
                time.sleep(2 ** attempt)

    def products(self):
        return sorted(
            p["id"] for p in self.get("/products")
            if p.get("quote_currency") == "USD"
            and p.get("status") == "online"
            and not any(p.get(flag) for flag in
                        ("trading_disabled", "cancel_only", "post_only", "limit_only", "auction_mode"))
            and p.get("base_currency") not in {"USD", "USDC", "USDT", "DAI", "EURC"}
        )

    def candles(self, product: str, now: int):
        end = (now // INTERVAL) * INTERVAL
        start = end - 299 * INTERVAL
        raw = self.get(f"/products/{urllib.parse.quote(product)}/candles", {
            "granularity": INTERVAL,
            "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        })
        result = []
        for row in sorted(raw, key=lambda item: item[0]):
            stamp, low, high, opening, closing, volume = row
            if stamp >= end:
                continue  # Ignore the currently forming candle.
            result.append(Bar(str(stamp), float(opening), float(high), float(low),
                              float(closing), float(volume)))
        return result

    def quote(self, product: str) -> Quote:
        ticker = self.get(f"/products/{urllib.parse.quote(product)}/ticker")
        bid, ask = float(ticker["bid"]), float(ticker["ask"])
        if not all(math.isfinite(x) and x > 0 for x in (bid, ask)) or bid > ask:
            raise ValueError(f"Invalid bid/ask for {product}")
        tick_time = datetime.fromisoformat(ticker["time"].replace("Z", "+00:00"))
        if tick_time.tzinfo is None:
            raise ValueError(f"Ticker time has no timezone for {product}")
        age = (datetime.now(timezone.utc) - tick_time).total_seconds()
        if age < -30 or age > self.max_quote_age_seconds:
            raise ValueError(f"Stale ticker for {product}: {age:.0f}s old")
        return Quote(bid, ask)


def signal(bars: list[Bar], side_cost: float = 0.0025, target="profit"):
    """Predict either direction or profitability of the next candle."""
    if len(bars) < 100 or any(
        int(bars[i].timestamp) - int(bars[i - 1].timestamp) != INTERVAL
        for i in range(1, len(bars))
    ):
        return None
    indices = list(range(20, len(bars) - 1))
    train = [features(bars, t) for t in indices]
    current = features(bars, len(bars) - 1)
    scaled_train, scaled_current = standardize(train, [current])
    if target == "profit":
        labels = [int(net_bar_return(bars[t + 1], side_cost) > 0) for t in indices]
    elif target == "direction":
        labels = [int(bars[t + 1].close > bars[t + 1].open) for t in indices]
    else:
        raise ValueError(f"Unknown forecast target: {target}")
    weights = fit_logistic(scaled_train, labels, epochs=120)
    probability = predict(weights, scaled_current[0])
    dollar_volume = sum(b.close * b.volume for b in bars[-24:])
    return probability, dollar_volume, int(bars[-1].timestamp)


def connect(path: Path, starting_cash: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL,
            start_cash REAL NOT NULL, last_cycle INTEGER
        );
        CREATE TABLE IF NOT EXISTS positions (
            product TEXT PRIMARY KEY, quantity REAL NOT NULL,
            entry_price REAL NOT NULL, entry_time TEXT NOT NULL,
            execution_version TEXT NOT NULL DEFAULT 'last_trade_v1',
            model_version TEXT NOT NULL DEFAULT 'direction_v1'
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
            product TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL,
            quantity REAL NOT NULL, fee REAL NOT NULL, probability REAL
        );
        CREATE TABLE IF NOT EXISTS cycles (
            time TEXT PRIMARY KEY, scanned INTEGER NOT NULL,
            candidates INTEGER NOT NULL, equity REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS predictions (
            product TEXT NOT NULL, bar_time INTEGER NOT NULL,
            probability REAL NOT NULL, used_probability REAL NOT NULL,
            dollar_volume REAL NOT NULL, actual_up INTEGER,
            realized_return REAL,
            PRIMARY KEY(product,bar_time)
        );
        CREATE TABLE IF NOT EXISTS profit_predictions (
            product TEXT NOT NULL, bar_time INTEGER NOT NULL,
            probability REAL NOT NULL, used_probability REAL NOT NULL,
            dollar_volume REAL NOT NULL, side_cost REAL NOT NULL,
            actual_profit INTEGER, realized_return REAL,
            PRIMARY KEY(product,bar_time)
        );
        CREATE TABLE IF NOT EXISTS paper_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL, entry_time TEXT NOT NULL,
            exit_time TEXT NOT NULL, entry_probability REAL,
            net_return REAL NOT NULL, dollar_pnl REAL NOT NULL,
            execution_version TEXT NOT NULL DEFAULT 'last_trade_v1',
            model_version TEXT NOT NULL DEFAULT 'direction_v1'
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(paper_outcomes)")}
    if "execution_version" not in columns:
        db.execute("ALTER TABLE paper_outcomes ADD COLUMN execution_version TEXT "
                   "NOT NULL DEFAULT 'last_trade_v1'")
    if "model_version" not in columns:
        db.execute("ALTER TABLE paper_outcomes ADD COLUMN model_version TEXT "
                   "NOT NULL DEFAULT 'direction_v1'")
    columns = {row[1] for row in db.execute("PRAGMA table_info(positions)")}
    if "execution_version" not in columns:
        db.execute("ALTER TABLE positions ADD COLUMN execution_version TEXT "
                   "NOT NULL DEFAULT 'last_trade_v1'")
    if "model_version" not in columns:
        db.execute("ALTER TABLE positions ADD COLUMN model_version TEXT "
                   "NOT NULL DEFAULT 'direction_v1'")
    db.execute("INSERT OR IGNORE INTO account(id,cash,start_cash) VALUES (1,?,?)",
               (starting_cash, starting_cash))
    db.commit()
    return db


def trade(db, now: str, product: str, side: str, price: float, quantity: float,
          cost_rate: float, probability=None, model_version="direction_v1"):
    effective = price * (1 + cost_rate if side == "buy" else 1 - cost_rate)
    cash_change = -quantity * effective if side == "buy" else quantity * effective
    fee = quantity * price * cost_rate
    db.execute("UPDATE account SET cash=cash+? WHERE id=1", (cash_change,))
    if side == "buy":
        db.execute("INSERT INTO positions(product,quantity,entry_price,entry_time,"
                   "execution_version,model_version) VALUES (?,?,?,?,?,?)",
                   (product, quantity, effective, now, "bid_ask_v2", model_version))
    else:
        db.execute("DELETE FROM positions WHERE product=?", (product,))
    db.execute("INSERT INTO trades(time,product,side,price,quantity,fee,probability) "
               "VALUES (?,?,?,?,?,?,?)",
               (now, product, side, price, quantity, fee, probability))


def cycle(exchange: Exchange, db, max_markets: int, max_positions: int,
          allocation: float, threshold: float, fee_bps: float,
          slippage_bps: float, min_dollar_volume: float, only_products=None,
          max_spread_bps=50, assumed_spread_bps=20, strategy="direction"):
    now = int(time.time())
    cycle_id = now // INTERVAL
    account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
    if account["last_cycle"] == cycle_id:
        LOGGER.info("This hourly cycle has already completed")
        return
    products = exchange.products()
    if only_products:
        products = [p for p in products if p in only_products]
        if not products:
            raise RuntimeError("None of the requested products are eligible USD spot pairs")
    if max_markets:
        products = products[:max_markets]
    LOGGER.info("Scanning %d eligible USD spot pairs", len(products))
    offset = calibration_offset(db, strategy)
    if offset:
        LOGGER.info("Using validated %s calibration offset %.3f", strategy, offset)
    training_side_cost = (fee_bps + slippage_bps + assumed_spread_bps / 2) / 10_000
    candidates = []
    new_predictions = []
    new_direction_predictions = []
    resolved_profit = []
    resolved_direction = []
    scanned = 0
    for number, product in enumerate(products, 1):
        if number % 25 == 0:
            LOGGER.info("Processed %d/%d pairs", number, len(products))
        try:
            bars = exchange.candles(product, now)
            direction_result = signal(bars, training_side_cost, "direction")
            profit_result = signal(bars, training_side_cost, "profit")
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("Skipping %s: %s", product, error)
            continue
        available = {int(bar.timestamp): bar for bar in bars}
        for pending in db.execute(
            "SELECT bar_time FROM predictions WHERE product=? AND actual_up IS NULL", (product,)
        ):
            future = available.get(pending["bar_time"] + INTERVAL)
            if future:
                realized_return = future.close / future.open - 1
                resolved_direction.append((int(realized_return > 0), realized_return,
                                           product, pending["bar_time"]))
        for pending in db.execute(
            "SELECT bar_time,side_cost FROM profit_predictions "
            "WHERE product=? AND actual_profit IS NULL", (product,)
        ):
            future = available.get(pending["bar_time"] + INTERVAL)
            if future:
                net_return = net_bar_return(future, pending["side_cost"])
                resolved_profit.append((int(net_return > 0), net_return,
                                        product, pending["bar_time"]))
        if direction_result is None or profit_result is None:
            continue
        scanned += 1
        direction_probability, dollar_volume, bar_time = direction_result
        profit_probability = profit_result[0]
        direction_used = adjusted_probability(
            direction_probability, offset if strategy == "direction" else 0
        )
        profit_used = adjusted_probability(
            profit_probability, offset if strategy == "profit" else 0
        )
        new_direction_predictions.append((product, bar_time, direction_probability,
                                          direction_used, dollar_volume))
        new_predictions.append((product, bar_time, profit_probability,
                                profit_used, dollar_volume, training_side_cost))
        used_probability = direction_used if strategy == "direction" else profit_used
        if dollar_volume >= min_dollar_volume and used_probability >= threshold:
            candidates.append((used_probability, product))
    candidates.sort(reverse=True)
    open_positions = list(db.execute("SELECT * FROM positions"))
    quotes = {}
    for product in sorted({row["product"] for row in open_positions}):
        try:
            quotes[product] = exchange.quote(product)
        except (RuntimeError, ValueError, KeyError) as error:
            LOGGER.warning("No executable price for %s: %s", product, error)
    held = {row["product"] for row in open_positions if row["product"] not in quotes}
    slots = max(0, max_positions - len(held))
    selected = []
    for probability, product in candidates:
        if len(selected) >= slots:
            break
        if product in held:
            continue
        if product not in quotes:
            try:
                quotes[product] = exchange.quote(product)
            except (RuntimeError, ValueError, KeyError) as error:
                LOGGER.warning("No executable price for %s: %s", product, error)
                continue
        if quotes[product].spread_bps > max_spread_bps:
            LOGGER.info("Skipping %s: %.1f bps spread", product,
                        quotes[product].spread_bps)
            continue
        selected.append((probability, product))
    if not scanned:
        raise RuntimeError("No markets had usable candles; wallet was not changed")
    when = datetime.now(timezone.utc).isoformat()
    cost_rate = (fee_bps + slippage_bps) / 10_000
    with db:
        for row in open_positions:
            product = row["product"]
            if product in quotes:
                entry = db.execute(
                    "SELECT probability FROM trades WHERE product=? AND side='buy' "
                    "ORDER BY id DESC LIMIT 1", (product,)
                ).fetchone()
                exit_value = row["quantity"] * quotes[product].bid * (1 - cost_rate)
                entry_value = row["quantity"] * row["entry_price"]
                db.execute(
                    "INSERT INTO paper_outcomes(product,entry_time,exit_time,entry_probability,"
                    "net_return,dollar_pnl,execution_version,model_version) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (product, row["entry_time"], when, entry[0] if entry else None,
                     exit_value / entry_value - 1, exit_value - entry_value,
                     "bid_ask_v2" if row["execution_version"] == "bid_ask_v2"
                     else "mixed_v1_v2", row["model_version"]),
                )
                trade(db, when, product, "sell", quotes[product].bid,
                      row["quantity"], cost_rate)
        cash = db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
        slot_budget = min(cash * allocation, cash / max(1, len(selected)))
        for probability, product in selected:
            if db.execute(
                "SELECT 1 FROM positions WHERE product=?", (product,)
            ).fetchone():
                continue
            quantity = slot_budget / (quotes[product].ask * (1 + cost_rate))
            if quantity * quotes[product].ask < 10:
                continue
            trade(db, when, product, "buy", quotes[product].ask, quantity,
                  cost_rate, probability,
                  "direction_v1" if strategy == "direction" else "profit_v2")
        cash = db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
        equity = cash + sum(
            row["quantity"] * quotes[row["product"]].bid * (1 - cost_rate)
            if row["product"] in quotes else row["quantity"] * row["entry_price"]
            for row in db.execute("SELECT * FROM positions")
        )
        db.executemany(
            "UPDATE predictions SET actual_up=?,realized_return=? "
            "WHERE product=? AND bar_time=?", resolved_direction,
        )
        db.executemany(
            "UPDATE profit_predictions SET actual_profit=?,realized_return=? "
            "WHERE product=? AND bar_time=?", resolved_profit,
        )
        db.executemany(
            "INSERT OR IGNORE INTO profit_predictions "
            "(product,bar_time,probability,used_probability,dollar_volume,side_cost) "
            "VALUES (?,?,?,?,?,?)", new_predictions,
        )
        db.executemany(
            "INSERT OR IGNORE INTO predictions "
            "(product,bar_time,probability,used_probability,dollar_volume) "
            "VALUES (?,?,?,?,?)", new_direction_predictions,
        )
        db.execute("UPDATE account SET last_cycle=? WHERE id=1", (cycle_id,))
        db.execute("INSERT INTO cycles VALUES (?,?,?,?)",
                   (when, scanned, len(candidates), equity))
    LOGGER.info("Scanned %d/%d USD markets; %d signals; equity $%.2f; cash $%.2f",
                scanned, len(products), len(candidates), equity, cash)
    LOGGER.info("Stored %d direction and profit forecasts; resolved %d profit and %d direction forecasts",
                len(new_predictions), len(resolved_profit), len(resolved_direction))
    for row in db.execute("SELECT product,quantity,entry_price FROM positions"):
        LOGGER.info("Paper position %s: %.8f at $%.6f", *row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("paper.sqlite3"))
    parser.add_argument("--starting-cash", type=float, default=10_000)
    parser.add_argument("--max-markets", type=int, default=0,
                        help="0 scans all eligible Coinbase USD spot pairs")
    parser.add_argument("--products", help="Comma-separated USD pairs, e.g. BTC-USD,ETH-USD")
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--allocation", type=float, default=0.1)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--strategy", choices=("direction", "profit"), default="direction",
                        help="Model controlling the paper wallet; both models are always scored")
    parser.add_argument("--min-dollar-volume", type=float, default=100_000)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--max-spread-bps", type=float, default=50)
    parser.add_argument("--assumed-spread-bps", type=float, default=20,
                        help="Historical spread estimate used only for training and backtesting")
    parser.add_argument("--max-quote-age-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="Log errors only; useful for public CI logs")
    args = parser.parse_args()
    if (args.starting_cash <= 0 or args.max_markets < 0 or args.max_positions < 1
            or not 0 < args.allocation <= 1 or not 0.5 <= args.threshold <= 1
            or args.min_dollar_volume < 0 or args.fee_bps < 0 or args.slippage_bps < 0
            or args.max_spread_bps <= 0 or args.max_quote_age_seconds <= 0
            or args.assumed_spread_bps < 0
            or args.max_positions * args.allocation > 1):
        parser.error("Invalid risk, threshold, cost, or market settings")
    logging.basicConfig(level=logging.ERROR if args.quiet else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    lock = (args.db.parent / (args.db.name + ".lock")).open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error(f"Another paper bot is already using {args.db}")
    db = connect(args.db, args.starting_cash)
    exchange = Exchange(args.max_quote_age_seconds)
    only_products = {p.strip().upper() for p in args.products.split(",")} if args.products else None
    while True:
        try:
            cycle(exchange, db, args.max_markets, args.max_positions,
                  args.allocation, args.threshold, args.fee_bps,
                  args.slippage_bps, args.min_dollar_volume, only_products,
                  args.max_spread_bps, args.assumed_spread_bps, args.strategy)
        except (RuntimeError, urllib.error.URLError) as error:
            LOGGER.error("Cycle failed: %s", error)
            if args.once:
                raise SystemExit(1) from error
        if args.once:
            break
        time.sleep(max(60, (int(time.time()) // INTERVAL + 1) * INTERVAL - time.time() + 30))


if __name__ == "__main__":
    main()

import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from learning import adjusted_probability, calibration_offset
from paper_bot import INTERVAL, Exchange, Quote, connect, cycle, signal
from trader import Bar, net_bar_return


class FakeExchange:
    def products(self):
        return ["BTC-USD", "ETH-USD"]

    def candles(self, product, now):
        return []

    def quote(self, product):
        price = {"BTC-USD": 100.0, "ETH-USD": 50.0}[product]
        return Quote(price, price)


class CandleExchange(FakeExchange):
    def candles(self, product, now):
        last = (now // INTERVAL) * INTERVAL - INTERVAL
        return [Bar(str(last - i * INTERVAL), 100, 102, 99, 100.2, 1000)
                for i in range(109, -1, -1)]


class SpreadExchange(FakeExchange):
    def quote(self, product):
        return {"BTC-USD": Quote(99, 101), "ETH-USD": Quote(49, 51)}[product]


class PaperBotTests(unittest.TestCase):
    def test_signal_needs_contiguous_completed_bars(self):
        bars = []
        for i in range(110):
            opening = 100 + i * 0.1
            closing = opening + (0.2 if i % 3 else -0.1)
            bars.append(Bar(str(i * INTERVAL), opening, closing + 0.1,
                            opening - 0.1, closing, 1000))
        result = signal(bars)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result[0], 0)
        self.assertLessEqual(result[0], 1)
        self.assertIsNone(signal(bars[:60]))
        self.assertIsNone(signal(bars[:50] + bars[51:]))

    def test_wallet_persists_and_cycle_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.sqlite3"
            db = connect(db_path, 1000)
            with patch("paper_bot.time.time", return_value=1_800_000_000), \
                 patch("paper_bot.signal", return_value=(0.8, 1_000_000, 1_799_996_400)):
                cycle(FakeExchange(), db, 0, 2, 0.1, 0.55, 10, 5, 0)
                first = db.execute("SELECT cash FROM account").fetchone()[0]
                self.assertAlmostEqual(first, 800)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM profit_predictions").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 2)
                cycle(FakeExchange(), db, 0, 2, 0.1, 0.55, 10, 5, 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 2)
            db.close()
            reopened = connect(db_path, 5000)
            self.assertAlmostEqual(reopened.execute("SELECT cash FROM account").fetchone()[0], first)
            self.assertEqual(reopened.execute("SELECT start_cash FROM account").fetchone()[0], 1000)
            reopened.close()

    def test_calibration_waits_for_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(Path(directory) / "wallet.sqlite3", 1000)
            db.execute("INSERT INTO profit_predictions(product,bar_time,probability,"
                       "used_probability,dollar_volume,side_cost,actual_profit,realized_return) "
                       "VALUES ('BTC-USD',0,0.2,0.2,1000,0.0025,1,0.01)")
            self.assertEqual(calibration_offset(db), 0)
            self.assertGreater(adjusted_probability(0.2, 0.5), 0.2)
            db.close()

    def test_next_candle_and_closed_paper_trades_become_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(Path(directory) / "wallet.sqlite3", 1000)
            exchange = CandleExchange()
            forecast = lambda bars, cost, target: (0.8, 1_000_000, int(bars[-1].timestamp))
            with patch("paper_bot.signal", side_effect=forecast):
                with patch("paper_bot.time.time", return_value=1_800_000_000):
                    cycle(exchange, db, 0, 2, 0.1, 0.55, 10, 5, 0)
                with patch("paper_bot.time.time", return_value=1_800_003_600):
                    cycle(exchange, db, 0, 2, 0.1, 0.55, 10, 5, 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_outcomes").fetchone()[0], 2)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM profit_predictions WHERE actual_profit=0"
            ).fetchone()[0], 2)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM predictions WHERE actual_up=1"
            ).fetchone()[0], 2)
            self.assertLess(db.execute("SELECT net_return FROM paper_outcomes LIMIT 1").fetchone()[0], 0)
            db.close()

    def test_profit_model_is_shadow_only_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(Path(directory) / "wallet.sqlite3", 1000)
            forecast = lambda bars, cost, target: (
                0.8 if target == "direction" else 0.1,
                1_000_000, 1_799_996_400
            )
            with patch("paper_bot.time.time", return_value=1_800_000_000), \
                 patch("paper_bot.signal", side_effect=forecast):
                cycle(FakeExchange(), db, 0, 2, 0.1, 0.55, 10, 5, 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 2)
            self.assertEqual(db.execute(
                "SELECT DISTINCT model_version FROM positions"
            ).fetchone()[0], "direction_v1")
            self.assertEqual(db.execute(
                "SELECT DISTINCT probability FROM profit_predictions"
            ).fetchone()[0], 0.1)
            db.close()

    def test_calibration_uses_later_hours_as_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(Path(directory) / "wallet.sqlite3", 1000)
            rows = [
                (f"COIN{i}-USD", hour * INTERVAL, 0.2, 0.2, 1_000_000, 0.0025,
                 int(i < 7), 0.01 if i < 7 else -0.01)
                for hour in range(180) for i in range(10)
            ]
            db.executemany(
                "INSERT INTO profit_predictions(product,bar_time,probability,used_probability,"
                "dollar_volume,side_cost,actual_profit,realized_return) "
                "VALUES (?,?,?,?,?,?,?,?)", rows
            )
            self.assertGreater(calibration_offset(db), 0)
            db.close()

    def test_spread_filter_and_ask_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(Path(directory) / "wallet.sqlite3", 1000)
            with patch("paper_bot.time.time", return_value=1_800_000_000), \
                 patch("paper_bot.signal", return_value=(0.8, 1_000_000, 1_799_996_400)):
                cycle(SpreadExchange(), db, 0, 1, 0.1, 0.55, 10, 5, 0,
                      max_spread_bps=300)
            trades = list(db.execute("SELECT product,side,price FROM trades"))
            self.assertEqual([tuple(row) for row in trades], [("BTC-USD", "buy", 101.0)])
            db.close()

    def test_stale_ticker_is_rejected(self):
        exchange = Exchange(max_quote_age_seconds=10)
        with patch.object(exchange, "get", return_value={
            "bid": "99", "ask": "101", "time": "2020-01-01T00:00:00Z"
        }):
            with self.assertRaisesRegex(ValueError, "Stale ticker"):
                exchange.quote("BTC-USD")

    def test_existing_wallet_schema_is_migrated_without_losing_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wallet.sqlite3"
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE positions(product TEXT PRIMARY KEY, quantity REAL NOT NULL,
                    entry_price REAL NOT NULL, entry_time TEXT NOT NULL);
                INSERT INTO positions VALUES ('BTC-USD',1,100,'2026-01-01T00:00:00+00:00');
                CREATE TABLE paper_outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT NOT NULL, entry_time TEXT NOT NULL, exit_time TEXT NOT NULL,
                    entry_probability REAL, net_return REAL NOT NULL,
                    dollar_pnl REAL NOT NULL);
            """)
            old.close()
            db = connect(path, 1000)
            position = db.execute(
                "SELECT product,execution_version FROM positions"
            ).fetchone()
            self.assertEqual(tuple(position), ("BTC-USD", "last_trade_v1"))
            self.assertEqual(db.execute(
                "SELECT model_version FROM positions"
            ).fetchone()[0], "direction_v1")
            db.close()

    def test_training_target_requires_move_to_clear_costs(self):
        bar = Bar("0", 100.0, 100.3, 99.9, 100.2, 1000)
        self.assertGreater(bar.close, bar.open)
        self.assertLess(net_bar_return(bar, 0.0025), 0)
        bars = [Bar(str(i * INTERVAL), 100.0, 100.3, 99.9, 100.2, 1000)
                for i in range(110)]
        direction = signal(bars, 0.0025, "direction")[0]
        profit = signal(bars, 0.0025, "profit")[0]
        self.assertGreater(direction, profit)


if __name__ == "__main__":
    unittest.main()

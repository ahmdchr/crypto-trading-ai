# Crypto trading AI prototype

An offline Python prototype for testing whether a simple machine learning signal has any historical value. It trains a logistic regression model on OHLCV bar features, evaluates later bars, and simulates long only trades at the next bar's open with fees and slippage. It places no orders and needs no API keys.

## Run

Use Python 3.10 or newer. Supply a CSV with `timestamp,open,high,low,close,volume`, sorted from oldest to newest. ISO 8601 UTC timestamps work well. Use uniformly spaced bars from one market and timeframe, with at least 60 rows.

```bash
python3 trader.py candles.csv --fee-bps 10 --slippage-bps 5 --threshold 0.55 --allocation 0.1
```

The first 70% of examples train the model; the remaining 30% are a chronological holdout. The model predicts whether the next candle's open-to-close move would clear estimated buying and selling costs. `--assumed-spread-bps 20` adds a historical spread estimate because candle data does not include quotes. Each signal uses only completed bars and is filled at the next bar's open in this offline simulation. `allocation` is the fraction of current equity used per trade. The output reports the profitable candle rate, classification accuracy alongside an always-skip baseline, number of trades, compounded return alongside an always-buy baseline, and maximum drawdown.

This is a research baseline. The result depends strongly on the data, fee assumptions, spread, and market regime. It does not model order book depth, outages, taxes, or exchange specific rules. Evaluate several separate time periods before considering live trading.

## Autonomous paper bot

`paper_bot.py` scans all eligible **Coinbase USD spot pairs** every hour. It uses the most recent completed hourly candles for model scores, selects up to five pairs, and buys with a mock cash balance. At the next scan it sells those positions and picks again. Prices come from Coinbase's public ticker. The wallet and full simulated trade history live in `paper.sqlite3`. It never connects to private account APIs and cannot place real orders.

## How it learns

The bot retrains two separate logistic models for each usable pair from its latest completed hourly candles on every scan. The existing **direction** model still controls the paper wallet. A new **profit** model estimates whether the next candle's move would cover fees, slippage, and an assumed spread. It runs in shadow mode: it records forecasts and their later net candle returns without choosing wallet trades. This keeps the experiment separate while its performance is measured. `python3 status.py` reports both forecast streams and completed paper trade results.

After at least 168 distinct hours and 1,000 resolved forecasts for the active strategy, the bot may adjust its scores with one calibration value. It fits that value on older hours, tests it on later hours, and uses it only if the later hours' Brier score improves by at least 2%. The adjustment is capped and stays inactive until those conditions are met. This changes score calibration, not the underlying strategy or risk limits. The profit model can control a separate experiment with `--strategy profit --db another-wallet.sqlite3`; the running service remains on `direction`. Keep evaluating net paper returns across different market periods before trusting it with real money.

```bash
python3 paper_bot.py --once --products BTC-USD,ETH-USD --db /tmp/crypto-test.sqlite3
# For a new, separate continuous wallet: python3 paper_bot.py --db another-wallet.sqlite3
```

The second command runs continuously. It starts with $10,000 mock cash, scans all eligible Coinbase USD spot pairs, and uses at most 10% of equity per position. `--max-markets 20` limits scan size while testing; `--products` selects specific pairs. `--db path/to/file.sqlite3` chooses a wallet file. The bot locks its wallet so a second process cannot trade the same paper account. Since the service below is already running, pass a separate `--db` path for manual experiments.

On this machine the `crypto-paper-bot.service` user service is enabled and user lingering is on, so the bot can continue after logout and start after reboot. Check it with `systemctl --user status crypto-paper-bot.service`, read activity with `journalctl --user -u crypto-paper-bot.service -f`, and view the wallet with `python3 status.py`. Stop it with `systemctl --user stop crypto-paper-bot.service`; disable automatic startup with `systemctl --user disable crypto-paper-bot.service`. `loginctl disable-linger cookie` turns user lingering off if no other user services need it.

To keep scanning while this computer sleeps, use the [GitHub Actions setup](cloud/github-actions.md) for free scheduled scans, or the [cloud VM guide](cloud/README.md) for a continuously running machine. Both guides explain how to move the existing paper wallet without running two copies of the bot.

View the paper account and recent trades with Python's SQLite shell or a SQLite browser. For example:

```bash
python3 -c 'import sqlite3; d=sqlite3.connect("paper.sqlite3"); print(d.execute("select * from account").fetchall()); print(d.execute("select * from positions").fetchall()); print(d.execute("select * from trades order by id desc limit 10").fetchall())'
```

The market scope is Coinbase USD spot pairs, not every cryptocurrency across every exchange. The bot skips products with missing hourly candles, uses a minimum $100,000 daily dollar volume, and assumes a 0.10% fee plus 0.05% slippage on both buys and sells. New paper buys fill at the public ask, sells at the public bid, and it rejects new buys when the quoted spread exceeds 50 basis points. Tickers with a last trade more than five minutes old are rejected. Open positions are valued at estimated sale proceeds. Change these checks with `--max-spread-bps` and `--max-quote-age-seconds`. The wallet keeps earlier positions and reports results by execution version so old and new fill rules can be compared separately. Public top-of-book quotes still do not model order book depth or partial fills. Model scores are experimental; paper results are not evidence of future profitability.

Coinbase's [products](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs), [candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles), [ticker bid and ask](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker), and [rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits) are documented publicly.

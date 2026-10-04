# Run the paper bot on an Ubuntu VM

The bot needs outbound HTTPS access and persistent local disk. It exposes no web port and uses no exchange credentials. A small Ubuntu VM can run the Python standard-library application as a system service.

## Prepare the VM

For a zero-cost option, use an [Oracle Cloud Always Free instance](oracle-always-free.md). Otherwise, create an Ubuntu VM with at least 1 GiB RAM and 10 GiB disk. Allow SSH only from an address you control; no public application port is needed. Install Python 3.10 or newer. Copy the Python source files and `cloud/` directory to the VM, excluding `paper.sqlite3`, `paper.sqlite3.lock`, `__pycache__`, and other temporary files.

## Transfer the existing paper wallet

First verify the VM is ready and the project files are uploaded. Then stop the **local** service so only one bot can trade the wallet:

```bash
systemctl --user stop crypto-paper-bot.service
cd /home/cookie/crypto-trading-ai
python3 cloud/export-wallet.py /tmp/crypto-paper-wallet.sqlite3
```

Transfer that snapshot to the VM with `scp`, then run there from the copied project directory:

```bash
sudo bash cloud/bootstrap-ubuntu.sh /path/to/crypto-paper-wallet.sqlite3
sudo -u crypto-paper python3 /opt/crypto-trading-ai/status.py --db /var/lib/crypto-paper-bot/paper.sqlite3
sudo systemctl status crypto-paper-bot.service
```

Verify that the cloud wallet's starting cash, current cash, latest scan, and positions match the local snapshot. Once the cloud service is confirmed, disable local startup with `systemctl --user disable crypto-paper-bot.service`. Do not run both bot services with copies of the same wallet. If cloud setup fails, keep the local service stopped until you decide whether to resume it locally or try the migration again.

On the VM, use `sudo journalctl -u crypto-paper-bot.service -f` for logs. Back up `/var/lib/crypto-paper-bot/paper.sqlite3` with SQLite's backup API before major changes. Check your provider's limits and billing before creating resources; only explicitly free-tier resources avoid charges.

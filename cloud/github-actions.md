# Run the paper bot on GitHub Actions without a payment card

The [workflow](../.github/workflows/paper-trade.yml) runs one full Coinbase USD spot scan each hour on a standard GitHub runner. It decrypts the paper wallet, runs `paper_bot.py --once --quiet`, then encrypts and commits the updated wallet. The repository can be public because only the encrypted wallet is committed, and public workflow logs omit positions and balances. GitHub says standard runners are [free for public repositories](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). The bot still uses mock money and no exchange keys.

## Set up the repository

1. Create a free [GitHub account](https://docs.github.com/en/account-and-profile/how-tos/account-management/creating-an-account-on-github) and authenticate `gh` on this computer with `gh auth login -h github.com`. Check `gh auth status` succeeds. The CLI also needs the `workflow` scope to push `.github/workflows/paper-trade.yml`; if GitHub rejects that file, run `gh auth refresh -h github.com -s workflow` and approve the device authorization.
2. Create a **public** repository from this project directory. The local database is ignored by Git. For example:

   ```bash
   cd /home/cookie/crypto-trading-ai
   git init -b main
   git add .
   git commit -m 'Add paper trader and scheduled workflow'
   gh repo create crypto-trading-ai --public --source=. --remote=origin --push
   ```

3. Create a strong wallet password and save it outside the repository as a backup. The local `.wallet-passphrase` file is ignored by Git and must remain private. Then add it to GitHub Actions secrets:

   ```bash
   umask 077
   openssl rand -base64 48 > .wallet-passphrase
   gh secret set PAPER_WALLET_PASSPHRASE < .wallet-passphrase
   ```

   Keep a private copy of this password. If it is lost, old encrypted wallet snapshots cannot be recovered.

The scheduled workflow will report a missing-wallet error until the next step seeds it. The local bot remains active during repository setup.

## Move the existing paper wallet

Do this when the GitHub repository exists and the secret is set. Stop the local bot before copying the wallet, so the two locations cannot trade separate copies. Check that the wallet commit reaches GitHub before running the workflow.

```bash
cd /home/cookie/crypto-trading-ai
systemctl --user stop crypto-paper-bot.service
python3 cloud/export-wallet.py /tmp/crypto-paper-wallet.sqlite3
python3 cloud/verify-wallet.py /tmp/crypto-paper-wallet.sqlite3
mkdir -p state
gpg --batch --yes --pinentry-mode loopback --no-symkey-cache \
  --passphrase-file .wallet-passphrase --cipher-algo AES256 --symmetric \
  --output state/paper.sqlite3.gpg /tmp/crypto-paper-wallet.sqlite3
git add state/paper.sqlite3.gpg
git commit -m 'Seed encrypted paper wallet'
git push
gh workflow run paper-trade.yml
```

Open the repository's **Actions** tab and wait for the manually triggered run to complete. If the local bot already scanned this hour, the workflow will report that the cycle is complete; check the next hourly run for a new scan and wallet commit. Then disable local automatic startup with `systemctl --user disable crypto-paper-bot.service`. If the run fails, leave the local bot stopped while investigating. Resume it only if you decide to abandon this cloud wallet, to avoid diverging histories.

The workflow runs at minute 17 UTC each hour. [GitHub warns](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows) that scheduled jobs can be delayed or dropped; check the Actions tab periodically. The bot's hourly cycle guard prevents duplicate scans within one hour. The encrypted wallet is committed each successful run, so the repository's size will grow over time. Keep the password and a separate backup of the latest wallet.

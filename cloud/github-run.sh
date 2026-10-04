#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ ${GITHUB_ACTIONS:-} != true || -z ${GITHUB_REF_NAME:-} ]]; then
  echo 'Run this script only in the GitHub Actions workflow' >&2
  exit 1
fi
if [[ -z ${PAPER_WALLET_PASSPHRASE:-} ]]; then
  echo 'Missing PAPER_WALLET_PASSPHRASE repository secret' >&2
  exit 1
fi
if [[ ! -s state/paper.sqlite3.gpg ]]; then
  echo 'Missing encrypted paper wallet: seed it before enabling scheduled runs' >&2
  exit 1
fi

printf '%s' "$PAPER_WALLET_PASSPHRASE" | gpg --batch --yes --quiet \
  --pinentry-mode loopback --no-symkey-cache --passphrase-fd 0 \
  --output paper.sqlite3 --decrypt state/paper.sqlite3.gpg
python3 cloud/verify-wallet.py paper.sqlite3
cycle_before=$(python3 -c "import sqlite3; print(sqlite3.connect('paper.sqlite3').execute('SELECT last_cycle FROM account WHERE id=1').fetchone()[0])")
python3 paper_bot.py --once --quiet --db paper.sqlite3
python3 cloud/verify-wallet.py paper.sqlite3
cycle_after=$(python3 -c "import sqlite3; print(sqlite3.connect('paper.sqlite3').execute('SELECT last_cycle FROM account WHERE id=1').fetchone()[0])")
if [[ $cycle_before == "$cycle_after" ]]; then
  echo 'Hourly paper scan was already complete; wallet unchanged'
  exit 0
fi

printf '%s' "$PAPER_WALLET_PASSPHRASE" | gpg --batch --yes --quiet \
  --pinentry-mode loopback --no-symkey-cache --passphrase-fd 0 \
  --cipher-algo AES256 --symmetric \
  --output state/paper.sqlite3.gpg.new paper.sqlite3
mv state/paper.sqlite3.gpg.new state/paper.sqlite3.gpg

git config user.name 'crypto-paper-bot'
git config user.email 'crypto-paper-bot@users.noreply.github.com'
git add state/paper.sqlite3.gpg
if ! git diff --cached --quiet; then
  git commit -m 'Update encrypted paper wallet'
  git push origin "HEAD:${GITHUB_REF_NAME}"
fi
echo 'Paper scan completed; encrypted wallet saved'

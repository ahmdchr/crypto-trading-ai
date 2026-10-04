#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash cloud/bootstrap-ubuntu.sh /path/to/paper.sqlite3" >&2
  exit 1
fi
if [[ $# -ne 1 || ! -f $1 ]]; then
  echo "Provide one existing SQLite wallet snapshot as the argument" >&2
  exit 1
fi
if [[ -e /var/lib/crypto-paper-bot/paper.sqlite3 ]]; then
  echo "Cloud wallet already exists; refusing to overwrite it" >&2
  exit 1
fi

project_dir=$(cd "$(dirname "$0")/.." && pwd)
command -v python3 >/dev/null || { echo "Install Python 3 first" >&2; exit 1; }
if ! id crypto-paper >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/crypto-paper-bot --shell /usr/sbin/nologin crypto-paper
fi
install -d -m 0755 /opt/crypto-trading-ai
install -d -o crypto-paper -g crypto-paper -m 0750 /var/lib/crypto-paper-bot
for file in paper_bot.py trader.py learning.py status.py; do
  install -m 0644 "${project_dir}/${file}" "/opt/crypto-trading-ai/${file}"
done
install -o crypto-paper -g crypto-paper -m 0600 "$1" /var/lib/crypto-paper-bot/paper.sqlite3
install -m 0644 "${project_dir}/cloud/crypto-paper-bot.service" \
  /etc/systemd/system/crypto-paper-bot.service
systemctl daemon-reload
systemctl enable --now crypto-paper-bot.service
systemctl --no-pager --full status crypto-paper-bot.service

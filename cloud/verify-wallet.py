#!/usr/bin/env python3
"""Reject a corrupt or empty paper wallet before using it."""

import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wallet", type=Path)
    args = parser.parse_args()
    if not args.wallet.is_file():
        parser.error(f"Wallet does not exist: {args.wallet}")
    with sqlite3.connect(f"file:{args.wallet.resolve()}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            parser.error("Wallet failed SQLite integrity check")
        if db.execute("SELECT COUNT(*) FROM account WHERE id=1").fetchone()[0] != 1:
            parser.error("Wallet has no paper account")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Make a consistent snapshot of the SQLite paper wallet for cloud migration."""

import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--db", type=Path, default=Path("paper.sqlite3"))
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"Source wallet does not exist: {args.db}")
    if args.destination.exists():
        parser.error(f"Destination already exists: {args.destination}")
    source = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(args.destination)
    args.destination.chmod(0o600)
    try:
        source.backup(destination)
        ok = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            parser.error(f"Snapshot integrity check failed: {ok}")
        start_cash = destination.execute("SELECT start_cash FROM account WHERE id=1").fetchone()
        if start_cash is None:
            parser.error("Snapshot has no paper account")
        destination.commit()
    finally:
        destination.close()
        source.close()
    print(f"Wallet snapshot ready: {args.destination}")


if __name__ == "__main__":
    main()

# Paper wallet baseline

`2026-10-11-paper.sqlite3.gpg` is the encrypted GitHub wallet downloaded before
the observation-only change. Its SHA-256 is
`3af60ede71b6150c5b61da5994e8fe55aa9eccc6fd9c7f5a5ac16ef96f07f7b9`.
Keep the passphrase private; it is not stored here.

The last scan in this snapshot was 2026-10-11 19:45:41 UTC. Starting cash was
$10,000 and marked paper equity was $9,352.38. There were 153 closed paper
trades and $626.92 in aggregate realized P&L. This baseline includes five
open positions and should not be used as a second active wallet.

To inspect it locally, decrypt it to a private temporary path with the existing
`.wallet-passphrase`, then run `python3 status.py --db PATH` or
`python3 cloud/compare_signals.py --db PATH`. Do not commit the decrypted file.

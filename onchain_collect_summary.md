# On-chain wallet collect summary

- JST: 2026-10-15T09:17:00.653423+09:00
- UTC: 2026-10-15T00:17:00.653423+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 301
- **added**: 0
- **refreshed**: 0
- **transfers_scanned**: 0
- **accounts_scanned**: 0
- notes: arcscan /v1/chain failed http=530

- file wallets_quality.jsonl rows: **301**
- file wallets.jsonl rows: **301**

## Robinhood

- **ok**: True
- **watchlist_n**: 3545
- **onchain_n**: 27184
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 100
- **etherscan_xfers**: 800
- notes: Blockscout /api etherscan-compat reachable; v2 /addresses → http=0 IncompleteRead: IncompleteRead(8192 bytes read, 9780 more expected); tokentx 0xcec185eb http=0; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429; tokentx 0xc6911796 http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **3545**
- file wallets_onchain.jsonl rows: **27184**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


# On-chain wallet collect summary

- JST: 2026-10-02T09:17:13.922608+09:00
- UTC: 2026-10-02T00:17:13.922608+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1589
- **added**: 0
- **refreshed**: 0
- **transfers_scanned**: 0
- **accounts_scanned**: 0
- notes: arcscan /v1/chain failed http=530

- file wallets_quality.jsonl rows: **1589**
- file wallets.jsonl rows: **1589**

## Robinhood

- **ok**: True
- **watchlist_n**: 3294
- **onchain_n**: 16232
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429; tokentx 0xc6911796 http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **3294**
- file wallets_onchain.jsonl rows: **16232**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


# On-chain wallet collect summary

- JST: 2026-10-01T15:18:02.135474+09:00
- UTC: 2026-10-01T06:18:02.135474+00:00
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
- **watchlist_n**: 3165
- **onchain_n**: 15217
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 100
- **etherscan_xfers**: 600
- notes: Blockscout /api etherscan-compat reachable; v2 /addresses → http=500 "Internal server error"; tokentx 0x492641f6 http=500; tokentx 0x5d3a1ff2 http=500; tokentx 0x5fc5360d http=500; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429; tokentx 0xc6911796 http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **3165**
- file wallets_onchain.jsonl rows: **15217**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


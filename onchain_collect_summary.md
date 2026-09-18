# On-chain wallet collect summary

- JST: 2026-09-18T15:17:23.983610+09:00
- UTC: 2026-09-18T06:17:23.983610+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 2025
- **added**: 244
- **refreshed**: 156
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **2025**
- file wallets.jsonl rows: **2025**

## Robinhood

- **ok**: True
- **watchlist_n**: 1233
- **onchain_n**: 3656
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 0
- **addrs_scanned**: 150
- **etherscan_xfers**: 800
- notes: Blockscout /api etherscan-compat reachable; v2 /token-transfers → http=500 "Internal server error"; tokentx 0x5d3a1ff2 http=500; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x39dbed3a http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **1233**
- file wallets_onchain.jsonl rows: **3656**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


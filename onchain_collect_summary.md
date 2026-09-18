# On-chain wallet collect summary

- JST: 2026-09-18T11:05:26.523682+09:00
- UTC: 2026-09-18T02:05:26.523682+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1781
- **added**: 0
- **refreshed**: 400
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **1781**
- file wallets.jsonl rows: **1781**

## Robinhood

- **ok**: True
- **watchlist_n**: 1197
- **onchain_n**: 3582
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 800
- notes: Blockscout /api etherscan-compat reachable; tokentx 0x5fc5360d http=500; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x39dbed3a http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **1197**
- file wallets_onchain.jsonl rows: **3582**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


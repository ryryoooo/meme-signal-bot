# On-chain wallet collect summary

- JST: 2026-09-23T03:16:48.235735+09:00
- UTC: 2026-09-22T18:16:48.235735+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1084
- **added**: 42
- **refreshed**: 358
- **transfers_scanned**: 600
- **accounts_scanned**: 200
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **1084**
- file wallets.jsonl rows: **1084**

## Robinhood

- **ok**: True
- **watchlist_n**: 2783
- **onchain_n**: 7800
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429; tokentx 0xc6911796 http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **2783**
- file wallets_onchain.jsonl rows: **7800**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


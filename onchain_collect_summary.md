# On-chain wallet collect summary

- JST: 2026-09-20T21:16:18.711525+09:00
- UTC: 2026-09-20T12:16:18.711525+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 3160
- **added**: 197
- **refreshed**: 203
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **3160**
- file wallets.jsonl rows: **3160**

## Robinhood

- **ok**: True
- **watchlist_n**: 2639
- **onchain_n**: 5827
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429; tokentx 0x5e49e1f8 http=429; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **2639**
- file wallets_onchain.jsonl rows: **5827**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


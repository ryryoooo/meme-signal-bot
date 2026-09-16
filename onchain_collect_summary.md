# On-chain wallet collect summary

- JST: 2026-09-16T21:17:05.240173+09:00
- UTC: 2026-09-16T12:17:05.240173+00:00
- Keeper floors: Arc=0.01 RH_onchain=0.01

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1886
- **added**: 193
- **refreshed**: 207
- **transfers_scanned**: 400
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **1886**
- file wallets.jsonl rows: **1886**

## Robinhood

- **ok**: True
- **watchlist_n**: 1075
- **onchain_n**: 676
- **added**: 99
- **touched**: 201
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429

- file wallets.jsonl rows: **1075**
- file wallets_onchain.jsonl rows: **676**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


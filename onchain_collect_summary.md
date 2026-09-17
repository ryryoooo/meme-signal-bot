# On-chain wallet collect summary

- JST: 2026-09-17T09:17:54.783786+09:00
- UTC: 2026-09-17T00:17:54.783786+00:00
- Keeper floors: Arc=0.01 RH_onchain=0.01

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1160
- **added**: 394
- **refreshed**: 6
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **1160**
- file wallets.jsonl rows: **1160**

## Robinhood

- **ok**: True
- **watchlist_n**: 1179
- **onchain_n**: 1254
- **added**: 20
- **touched**: 280
- **xfers_scanned**: 250
- **addrs_scanned**: 150
- **etherscan_xfers**: 800
- notes: Blockscout /api etherscan-compat reachable; v2 /token-transfers → http=500 "Internal server error"; tokentx 0x492641f6 http=500; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429

- file wallets.jsonl rows: **1179**
- file wallets_onchain.jsonl rows: **1254**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


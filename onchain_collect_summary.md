# On-chain wallet collect summary

- JST: 2026-09-17T00:45:59.441243+09:00
- UTC: 2026-09-16T15:45:59.441243+00:00
- Keeper floors: Arc=0.01 RH_onchain=0.01

## Arc

- **ok**: True
- **mode**: inline
- **total**: 2130
- **added**: 244
- **refreshed**: 156
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **2130**
- file wallets.jsonl rows: **2130**

## Robinhood

- **ok**: True
- **watchlist_n**: 1118
- **onchain_n**: 835
- **added**: 44
- **touched**: 256
- **xfers_scanned**: 300
- **addrs_scanned**: 100
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; v2 /addresses → http=500 "Internal server error"; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429

- file wallets.jsonl rows: **1118**
- file wallets_onchain.jsonl rows: **835**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


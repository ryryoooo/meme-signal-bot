# On-chain wallet collect summary

- JST: 2026-09-16T15:41:52.624273+09:00
- UTC: 2026-09-16T06:41:52.624273+00:00
- Keeper floors: Arc=0.01 RH_onchain=0.01

## Arc

- **ok**: True
- **mode**: inline
- **total**: 961
- **added**: 265
- **refreshed**: 135
- **transfers_scanned**: 600
- **accounts_scanned**: 300
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **961**
- file wallets.jsonl rows: **961**

## Robinhood

- **ok**: True
- **watchlist_n**: 977
- **onchain_n**: 399
- **added**: 151
- **touched**: 149
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429

- file wallets.jsonl rows: **977**
- file wallets_onchain.jsonl rows: **399**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


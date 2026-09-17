# On-chain wallet collect summary

- JST: 2026-09-17T11:20:31.297321+09:00
- UTC: 2026-09-17T02:20:31.297321+00:00
- Keeper floors: Arc=0.01 RH_onchain=0.01

## Arc

- **ok**: True
- **mode**: inline
- **total**: 1194
- **added**: 34
- **refreshed**: 65
- **transfers_scanned**: 0
- **accounts_scanned**: 100
- notes: arcscan chain_id=5042

- file wallets_quality.jsonl rows: **1194**
- file wallets.jsonl rows: **1194**

## Robinhood

- **ok**: True
- **watchlist_n**: 1000
- **onchain_n**: 1369
- **added**: 299
- **touched**: 1
- **xfers_scanned**: 300
- **addrs_scanned**: 150
- **etherscan_xfers**: 900
- notes: Blockscout /api etherscan-compat reachable; tokentx 0xf3081494 http=429; tokentx 0xce24439f http=429; tokentx 0xeeca2e7d http=429; tokentx 0x74be72af http=429

- file wallets.jsonl rows: **1000**
- file wallets_onchain.jsonl rows: **1369**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


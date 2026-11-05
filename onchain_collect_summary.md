# On-chain wallet collect summary

- JST: 2026-11-06T08:00:09.573038+09:00
- UTC: 2026-11-05T23:00:09.573038+00:00
- Keeper floors: Arc=0.01 RH_onchain=500.0

## Arc

- **ok**: True
- **mode**: inline
- **total**: 301
- **added**: 0
- **refreshed**: 0
- **transfers_scanned**: 0
- **accounts_scanned**: 0
- notes: arcscan /v1/chain failed http=530

- file wallets_quality.jsonl rows: **301**
- file wallets.jsonl rows: **301**

## Robinhood

- **ok**: True
- **watchlist_n**: 3713
- **onchain_n**: 43233
- **added**: 0
- **touched**: 0
- **merge_to_watch**: False
- **xfers_scanned**: 0
- **addrs_scanned**: 0
- **etherscan_xfers**: 0
- notes: Blockscout /api probe http=503; v2 /token-transfers → http=503 <html>
<head><title>503 Service Temporarily Unavailable</title></head>
<body>
<center><h1>503 Service Temporarily Una; v2 /addresses → http=503 <html>
<head><title>503 Service Temporarily Unavailable</title></head>
<body>
<center><h1>503 Service Temporarily Una; v2 /tokens?type=ERC-20 → http=503 <html>
<head><title>503 Service Temporarily Unavailable</title></head>
<body>
<center><h1>503 Service Temporarily Una; watch_merge=off (wallets_onchain only; set RH_ONCHAIN_MERGE_TO_WATCH=1 after vet)

- file wallets.jsonl rows: **3713**
- file wallets_onchain.jsonl rows: **43233**

## Credit policy

- No Nansen Super / FOMO / paid refresh in this job
- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)
- Kick via GitHub Actions + free cron-job.org dispatch


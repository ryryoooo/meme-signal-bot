# Arc wallets — quality filter (bot export)

- JST: 2026-09-16T14:29:26.300257+09:00
- UTC: 2026-09-16T05:29:26.300257+00:00
- Raw unique pool: **11142**
- **Quality (GMGN-tagged / profit signals): 96**
- **Activity-only (on-chain top): 800** (cap 800)
- **Total wallets_quality.jsonl: 896**
- Bot fields: `pass_pnl=true`, `realized_pnl_usd` mapped from pnl_hints or **0.01** keeper floor (Arc GMGN UI often $0)
- Bot filter simulation keep=896 drop=0
- GMGN status: RATE_LIMIT_BANNED — used pretagged gmgn_smart ranks (merged=96, gmgn_in_quality=96)
- Sources: Arcscan on-chain (`api.arc-scan.org`) + pretagged GMGN CopyTrade Rank
- Official RPC: https://rpc.mainnet.arc.io (chainId 5042) / explorer https://explorer.arc.io
- copied → `discord-bot/arc-wallets/wallets_quality.jsonl` + `wallets.jsonl`

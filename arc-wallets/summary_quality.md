# Arc wallets — quality filter (bot export)

- JST: 2026-09-16T13:21:24.562746+09:00
- UTC: 2026-09-16T04:21:24.562746+00:00
- Raw unique pool: **3578**
- **Quality (GMGN-tagged / profit signals): 96**
- **Activity-only (on-chain top): 400** (cap 400)
- **Total wallets_quality.jsonl: 496**
- Bot fields: `pass_pnl=true`, `realized_pnl_usd` mapped from pnl_hints or **0.01** keeper floor (Arc GMGN UI often $0)
- Bot filter simulation keep=496 drop=0
- GMGN status: RATE_LIMIT_BANNED — used pretagged gmgn_smart ranks
- Sources: Arcscan on-chain (`api.arc-scan.org`) + pretagged GMGN CopyTrade Rank
- Official RPC: https://rpc.mainnet.arc.io (chainId 5042) / explorer https://explorer.arc.io
- copied → `discord-bot/arc-wallets/wallets_quality.jsonl` + `wallets.jsonl`

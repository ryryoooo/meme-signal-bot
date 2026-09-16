# Arc wallets — quality filter (bot export)

- JST: 2026-09-16T15:40:43.745923+09:00
- UTC: 2026-09-16T06:40:43.745923+00:00
- Raw unique pool: **5488**
- **Quality (GMGN-tagged / profit signals): 96**
- **Activity-only (on-chain top): 600** (cap 600)
- **Total wallets_quality.jsonl: 696**
- Bot fields: `pass_pnl=true`, `realized_pnl_usd` mapped from pnl_hints or **0.01** keeper floor (Arc GMGN UI often $0)
- Bot filter simulation keep=696 drop=0
- GMGN status: RATE_LIMIT_BANNED — used pretagged gmgn_smart ranks (merged=0, gmgn_in_quality=96)
- Sources: Arcscan on-chain (`api.arc-scan.org`) + pretagged GMGN CopyTrade Rank
- Official RPC: https://rpc.mainnet.arc.io (chainId 5042) / explorer https://explorer.arc.io
- copied → `discord-bot/arc-wallets/wallets_quality.jsonl` + `wallets.jsonl`

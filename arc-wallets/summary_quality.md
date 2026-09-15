# Arc wallets — quality filter

- JST: 2026-09-15T20:51:56.837522+09:00
- UTC: 2026-09-15T11:51:56.837522+00:00
- Raw unique (wallets.jsonl): **6219**
- **Quality (GMGN-tagged / profit signals): 96**
  - Almost entirely GMGN CopyTrade Rank / smart_degen (PnL often $0 in UI scrape — tagged smart, not proven edge)
- **Activity-only (on-chain top by transfers/volume/tx): 200** (cap 200)
  - **No on-chain PnL fields exist** on Arc explorer data — these are monitoring experiments only, not smart-money quality
- **Total wallets_quality.jsonl: 296**
- Honest split: **quality ≈ GMGN 96** vs **activity_only 200**
- Skipped: 5923 low/no-signal onchain (holders/deployers without strong activity)

## Files
- `wallets_quality.jsonl` (this filter)
- copied → `discord-bot/arc-wallets/wallets_quality.jsonl`
- raw remains `wallets.jsonl` (6219)

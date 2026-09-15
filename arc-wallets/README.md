# Arc watchlist (optional)

`CHAIN=arc` のとき `WATCHLIST_PATH=arc-wallets/wallets.jsonl` を使う。
既定チェーンは robinhood。RH の動作は壊さない。

このファイルは親ディレクトリの `meme-foundation/arc-wallets/wallets.jsonl` から
profit_tagged / smartmoney 系を抽出したサブセット。

## Watchlists (2026-09-15 JST refresh)

- `wallets.jsonl`: **quality-tier only** (GMGN smartmoney/rank tags) — used by bot
- `wallets_quality.jsonl`: quality + `activity_only_top_200` for monitoring experiments
- Honest: Arc on-chain has **no PnL**; activity rows are not smart-money quality


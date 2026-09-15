# Arc ハイブリッドウォレット検証サマリ

- 実行時刻 (JST): 2026-09-15T21:50:52.432628+09:00
- 実行時刻 (UTC): 2026-09-15T12:50:52.432628+00:00
- 元プール退避: `wallets_pool_full.jsonl` = **6219**
- 候補サンプリング: **800** / cap 800（gmgnタグ優先 → 高activity → 層化）
- GMGN事前タグ付き pass: **96**（APIスキップで合格）
- GMGN API 新規検証 pass: **0**
- 検証リジェクト/延期: **200**（主因: RATE_LIMIT_BANNED）
- quality before → after: **296 → 296**
- bot用 `wallets.jsonl` = quality **296**（旧bot 96 → 296）
- 出力: candidates_onchain.jsonl / gmgn_verified.jsonl / gmgn_rejected.jsonl / wallets_quality.jsonl

## ブロッカー
- GMGN IP temporarily banned（wallet_stats 連打後）。reset待ちでも profits が拒否継続。
- stats/winrate 経路は未実施（バン回避）。
- 一時的に profits バッチは成功し ~58 件 pnl>0 を確認したが、バン後に再取得できずファイル未確定。

## RH
- スキップ（Arc GMGN バン優先・予算温存）

## ネットワーク
- Arc mainnet (chainId 5042)


# Arc 通知ゲート（2026-09-17）

通知する前に通過（上から順）:

## 銘柄
1. **ローンチパッド卒業** `REQUIRE_GRADUATED=1` — bonding/未migrate見送り。Dex labels `graduated`/`migrated`、または十分なAMM流動性（`MIN_GRAD_LIQ_USD` 既定 $2500）かつ bondingっぽくない。
2. **5分出来高** `MIN_VOLUME_M5_USD`（既定 $800）。
3. **急騰しすぎ／不自然** — 5分騰落 `MAX_PRICE_CHANGE_M5_PCT`（既定 60%）、1時間 `MAX_PRICE_CHANGE_H1_PCT`（250%）、5分買い偏り `MAX_M5_BUY_RATIO`（0.92）。
4. **24h出来高** `MIN_VOLUME_H24_USD`（$5k）。
5. **ホルダー** `MIN_HOLDERS`（80、欠落は `HOLDERS_REQUIRED=0` で通す）。
6. **薄い板** `LIQ_MCAP_MIN≥0.20`。

## 買い・財布
7. **買い金額** `MIN_TRADE_USD` + `MIN_CLUSTER_USD`（$150 / $250）+ ≥2本。
8. **bot疑惑除外** `DROP_BOT_WALLETS` — label/tags の bot・sniper・mev・fresh_wallet単独などを交差から落とす。残りが `MIN_WALLETS` 未満なら見送り。
9. **仕込み** `REQUIRE_EARLY_HIT` / `WATCH_EARLY_ONLY`。
10. **質スコア** best≥`MIN_WALLET_QUALITY`、avg≥`MIN_AVG_WALLET_QUALITY`。

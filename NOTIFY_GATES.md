# Arc 通知ゲート（4軸・2026-09-17）

通知する前にすべて通過:

1. **出来高** `MIN_VOLUME_H24_USD`（既定 $5k, DexScreener 24h）。欠けるとき `VOLUME_REQUIRED=1` なら見送り。
2. **ホルダー** `MIN_HOLDERS`（既定 80）。データ無しは `HOLDERS_REQUIRED=0` なら通す（ArcはDex中心のため）。
3. **買い金額** `MIN_TRADE_USD` + `MIN_CLUSTER_USD`（既定 $150 / $250）+ `MIN_WALLETS≥2`。
4. **スマートウォレット質** 仕込みタグ・実現益・勝率・Nansen からスコア。`MIN_WALLET_QUALITY`（best≥1.0）と `MIN_AVG_WALLET_QUALITY`（avg≥0.6）。加えて `REQUIRE_EARLY_HIT` / `WATCH_EARLY_ONLY`。

薄い板 `LIQ_MCAP_MIN≥0.20` も従来どおり。

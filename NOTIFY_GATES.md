# Arc / RH 通知ゲート（2026-09-17）

## 銘柄
1. **卒業優先・卒業前も可** `ALLOW_PRE_GRAD=1` — bondingでも5分出来高が `PRE_GRAD_MIN_VOLUME_M5`（$2k）以上なら候補。
2. **5分出来高** `MIN_VOLUME_M5_USD`（$1500）/ **24h** `MIN_VOLUME_H24_USD`（$12k）。
3. **買い増加** `REQUIRE_BUY_INCREASE` — 5分買い＞売り（1hも同方向ならなお良い）。
4. **上下に動く** — 5分に最低の値動き（`MIN_ABS_PRICE_CHANGE_M5`）＋売り比率（`MIN_M5_SELL_RATIO`）で片側テープ拒否。急騰上限は従来どおり。
5. **ホルダー / 板** — 既存。

## 財布（RH特に）
- `@xbtscout` 投稿の **投稿時刻直前〜直後** に買っている GMGN smart 財布を `xbtscout_pre_post` / `xbtscout_early` として監視にマージ（signal毎スクレイプ＋refreshジョブ）。
- bot疑惑・仕込み・質スコアは Arc 側ルール継続。

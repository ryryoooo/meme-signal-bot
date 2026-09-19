# 通知プレイブック（2026-09-17）

主軸は **流動性** ＋ **2セットのどちらか**。スマートウォレットは補助。

## 共通ハード
- （RH）流動性ゲート無効 `LIQ_REQUIRED=0`
- （他）流動性 ≥ `MIN_LIQ_USD`（$2500）かつ liq/mcap ≥ 15%
- **出来高** 24h / 5分必須。欠落・薄いは除外
- **買いボリュームは命** — 5分買い側USD・買い件数・買い＞売り
- しょーもないSM除外（質スコア低＆実現益薄い）
- **値動き必須**: `REQUIRE_PRICE_MOVE=1` — |m5|≥`MIN_ABS_PRICE_CHANGE_M5`(3) または |h1|≥`MIN_ABS_PRICE_MOVE_H1`(5)。m5≈h1≈h6≈0 は即除外
- 監視交差 ≥2
- （RH）急騰見送り無効 `ANTI_SPIKE_REQUIRED=0`（spike/dump/onesided 不問）
- RH watch: `WATCH_MIN_REALIZED_HARD=500`（$0.01 onchain 偽PnLは除外）

## 第一セット — 初動ストーリー → 調整後の反転
- 年齢: **30分〜48時間**
- いったん明確な初動（h1/h6/h24 のいずれか ≥ +40%）
- 調整: 5分がまだパラボリックでない（m5 ≤ +25%）など冷却
- **反転**: 5分買い＞売り かつ m5騰落がプラス

## 第二セット — サバイバル（48h〜7日）→ 横ばい整理
- 年齢: **48時間〜7日**
- 序盤の深い調整の名残: 24h騰落 ≤ **-50%**
- 本物の出来高: 24h ≥ `$8000`
- **横ばい**: |m5|≤10% かつ |h1|≤30%、売買が両方向（ただし完全フラット m5≈h1≈0 は上の値動きゲートで除外）

ストーリー性（物語・コミュ）は自動では未判定。セット②は特に手動リサーチ前提。

## RH soft notify (2026-09-18)
- 買いボリューム緩和: `MIN_BUY_VOLUME_M5_USD=120`, `MIN_BUYS_M5=3`, `BUY_VOLUME_REQUIRED=0`, `REQUIRE_BUY_INCREASE=0`
- 出来高緩和: h24/m5 required off、閾値低下
- 値動き必須オフ、クールダウン1h、cluster $80、弱ウォレットdropオフ
- 流動性・急騰ゲートは引き続き無効

## RH passthrough (never-stop notify)
- `NOTIFY_PASSTHROUGH=1`（or `RH_NOTIFY_ALWAYS=1` when `CHAIN=robinhood`）
- Post **all** watchlist buy overlaps to Discord; GMGN fail / safety fail / notify_gate fail do **not** block
- Still runs `safety_check` for card fields; prefers Dex `market_snapshot` when GMGN failed
- Keeps `already_seen` duplicate skip; shorten via `COOLDOWN_SECONDS` (RH: 900)
- Live trading stays off (`LIVE_TRADING=0`)


## Priority tier (2026-09-20)
- `NOTIFY_PASSTHROUGH` unchanged: **all** watchlist-overlap buys still post to the main webhook.
- Rule-based priority (no Jev): `n_wallets >= PRIORITY_MIN_WALLETS` (default 2)
  AND `cluster_usd >= MIN_CLUSTER_PRIORITY` (default 150)
  AND token age within soft window `PRIORITY_MIN_AGE_SEC`–`PRIORITY_MAX_AGE_SEC` (default 30m–48h; missing age soft-ok unless `PRIORITY_REQUIRE_AGE=1`)
  AND **not** flat/dead tape (`is_flat_dead_tape`: m5/h1/h6 ≈0 or soft move miss + thin m5 vol/buys).
- If `DISCORD_PRIORITY_WEBHOOK_URL` is set: also post a `【優先】` embed (color `PRIORITY_COLOR`) to that channel; main feed stays normal.
- If priority webhook empty: main embed itself gets `【優先】` title prefix + priority color (graceful no-op for second channel).
- Knobs: `PRIORITY_NOTIFY`, `MIN_CLUSTER_PRIORITY`, `PRIORITY_MIN_WALLETS`, `PRIORITY_MIN_AGE_SEC`, `PRIORITY_MAX_AGE_SEC`, `PRIORITY_MIN_ABS_M5`, `PRIORITY_MIN_ABS_H1`, `PRIORITY_MIN_VOLUME_M5`, `PRIORITY_TITLE_PREFIX`, `PRIORITY_COLOR`.
- Live trading stays off (`LIVE_TRADING=0`).

## Watchlist auto-prune
- Script: `scripts/prune_watch_wallets.py` (dry-run default).
- Weak: banned copy/team/bot labels, dust-only buys, consecutive losses / long inactivity **when fields exist**, inconsistent PnL when WR filled.
- Keep: scout elite / high `scout_rank_score` / themaran / rank_s|a unless clearly banned.
- Env: `PRUNE_*` (see script docstring). Weekly GHA: `prune-watchlist.yml`.


## Fast path (box FOMO tick) — 2026-09-20

**Primary RH buy notify** is the box-resident loop, not GHA:

| Path | Interval | Source | GMGN |
|------|----------|--------|------|
| **Box** `scripts/signal_tick.sh` | `SIGNAL_POLL_SECONDS` default **300s** (adaptive; was 20s — burned FOMO dry) | FOMO buy tape | **OFF** (`GMGN_DISABLED=1` / `GMGN_SMARTMONEY=0`) |
| **GHA** `signal.yml` | ~12m cron (`2,14,26,38,50`) | GMGN smartmoney backup | **ON** (GHA IP) |

### Box tick
- Same RH soft gates + `NOTIFY_PASSTHROUGH=1` / `RH_NOTIFY_ALWAYS=1` as `signal.yml`.
- `FOMO_ENABLED=1`, `FOMO_POLL_SECONDS` matches poll (each due tick hits FOMO alerts).
- Single PID via `flock` on `signal_tick.lock` (no duplicate ticks).
- FOMO holders scrape disabled on the fast tick (`FOMO_HOLDERS=0`) to save credits.
- xbtscout left to its own watcher (`XBTSCOUT_ENABLED=0` here).
- Secrets: `load_secrets` → `load_secrets` → box secrets card (`FOMO_API_KEY`, Discord webhooks) (`FOMO_API_KEY`, `DISCORD_WEBHOOK_URL`, …). Also injected into the daemon env.
- Wired by `scout-wallet-bot.sh` → `ensure_signal_tick`; `health.json` includes `signal_tick`.

### Shared state (dedupe)
- Both write `STATE_PATH=state.json` keys: `seen_signal_keys`, `ca_last_posted`, cooldown.
- Box↔GHA sync via GitHub release tag `signal-state` (`scripts/signal_state_sync.sh` pull/push).
- FOMO poll timers are **not** overwritten by GHA merges (so the box tick interval is not starved).

### FOMO credits (burn math)
- **Alerts ≈ 125 credits/call** (`x-credits-cost`). Holders scrape ≈ 250 — keep `FOMO_HOLDERS=0` on the fast tick.
- Free tier ≈ **250k credits/mo**. Burn examples (alerts only, 1 call per poll):

  | Poll | Calls/day | Credits/day | Days to empty 250k |
  |------|-----------|-------------|--------------------|
  | 20s  | ~4320     | ~540k       | **<1 day** (dry)   |
  | 120s | ~720      | ~90k        | ~2.8 days          |
  | 300s | ~288      | ~36k        | ~7 days            |
  | 1500s (25m) | ~58 | ~7.2k     | ~full month        |

- **Recommend poll ≥120s when credits scarce; default box poll is 300s.**
- Adaptive in `signal_tick.sh`:
  - `remain == 0` or HTTP **402** / `fomo err=credits` → sleep **30–60 min**, log clearly, rely on **GHA `signal.yml` GMGN** backup.
  - `remain < 5000` (`FOMO_CREDIT_LOW`) → back off poll to **120–300s**.
- Monitor `cost=` / `remain=` in `signal_tick.log` and `health.json` → `signal_tick.fomo_remain`.
- If `FOMO_API_KEY` missing on box: tick still runs with FOMO off + GMGN off (safe no-hammer); GHA remains backup.


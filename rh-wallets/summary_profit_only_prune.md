# Profit-only watchlist prune

- Updated: **2026-09-19T20:28:19.956737+00:00** (JST 2026-09-20 05:28:19 UTC+09:00)
- Watch: `rh-wallets/wallets.jsonl`
- Backup: `rh-wallets/wallets_pre_profit_prune.jsonl` (+ dated `wallets_pre_profit_prune_20260920_052819_JST.jsonl`)
- Policy: **利益出してるやつだけ** — aggregate profit makers only
- LIVE_TRADING: **off** (unchanged)

## Counts

- Before: **2567**
- After: **429**
- Dropped total: **2138**
  - no-PnL / unvetted: **2136** → `dropped_unvetted_profit_prune.jsonl`
  - negative aggregate PnL: **0**
  - 7d losers (`low_profit_7d` + `realized_pnl_7d < 0`): **2** → `dropped_7d_losers_profit_prune.jsonl`

## Keep rules (applied)

1. KEEP if `realized_pnl_usd > 0` (or `total_pnl_usd > 0` when realized missing)
2. DROP if in `low_profit_7d` with `realized_pnl_7d < 0` (even if lifetime PnL > 0)
3. KEEP `audit_quality` / `audit_active_quality_plus` / `audit_active_quality_plus_strict` **only when** they also have positive PnL (all current members do)
4. NO PnL data → **DROP** (unvetted); side-file retained for later re-vet
5. `onchain_hot_active` unvetted buy-shaped → **default DROP** per policy

## Keep reason tallies (non-exclusive)

- `realized_pnl_usd>0`: **429**
- `audit_quality`: **225**
- `audit_active_quality_plus`: **225**
- `audit_active_quality_plus_strict`: **112**

## Dropped 7d losers (detail)

| address | label | realized_pnl_7d | realized_pnl_usd (lifetime) |
|---|---|---:|---:|
| `0x24e91130…7ba3` | scout_elite [0x24e9…7ba3] | -6,946.64 | 63,991.81 |
| `0xd632a243…1718` | scout_elite [0xd632…1718] | -7,106.86 | 28,336.33 |

## Top kept by realized_pnl_usd

| # | address | label | realized_pnl_usd | tags |
|---:|---|---|---:|---|
| 1 | `0x2ac082e2…711c` |  | 12,296,201 | fomo,audit_active |
| 2 | `0x8f62a085…80a3` | DumbCrayonEater | 11,163,176 | fomo,audit_active |
| 3 | `0xb48ae67b…e167` |  | 11,163,176 | fomo,audit_active |
| 4 | `0xa14b38a3…5da0` |  | 7,913,857 | fomo,audit_active |
| 5 | `0xec6505db…0371` |  | 6,527,102 | fomo,audit_active |
| 6 | `0x830a7744…e274` |  | 5,554,588 | fomo,audit_active |
| 7 | `0xb3e02976…42ba` |  | 5,359,446 | fomo,audit_active |
| 8 | `0xf89e668f…887b` |  | 4,869,962 | fomo,audit_active |
| 9 | `0x05583684…de16` |  | 4,483,328 | fomo,audit_active |
| 10 | `0xa9f7da89…a682` |  | 4,382,782 | fomo,audit_active |

## Notes

- Intersection: all **225** `audit_quality`, **225** quality_plus, **112** quality_plus_strict retained (all had positive realized).
- Unvetted side file: **2136** rows for optional later GMGN/onchain re-vet.
- Onchain tick reloads watchlist every `ONCHAIN_WATCH_RELOAD_SEC` (default 60s); process signal optional.


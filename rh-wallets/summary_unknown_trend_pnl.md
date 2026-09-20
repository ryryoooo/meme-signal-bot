# Unknown-trend promoted wallets — PnL status

- Updated: **2026-09-20 15:17 JST**
- Source: `rh-wallets/wallets.jsonl` tags `unknown_trend` + `pnl_pending`
- Count: **95** addresses (peak_ca_overlap: 39)

## realized_pnl_usd

| bucket | count |
|--------|------:|
| positive (>) | **0** |
| negative (<) | **0** |
| zero (=0) | **0** |
| still null | **95** |

**Profit answer: unknown — all 95/95 still `realized_pnl_usd=null` (tag `pnl_pending`). No invented numbers.**

## Fill path

- Box GMGN: **disabled** (do not call from box IP)
- GHA: `gmgn-vet-throttle.yml` with `priority=pnl_pending` (new) → fills `realized_pnl_usd` / `win_rate` / `n_trades`, clears `pnl_pending` on success
- Cached fills elsewhere: **0** / 95

## GMGN 429 cooldown

- Last 429: **2026-09-20 14:13 JST** (`2026-09-20T05:13:01Z`)
- Default cool window: 6h → until **2026-09-20 20:13 JST**
- Cooled now: **False**
- Job dispatch: **held** until cool (avoid another 429). Pending: all **95** wallets.

## Next

```bash
gh workflow run gmgn-vet-throttle.yml -f cap=5 -f sleep_sec=25 -f priority=pnl_pending -f period=30d -f rate_limit_cooldown_hours=6
```

At cap=5 / ~25s sleep, ~19 runs (~2–3 calendar days on 3h schedule) to clear 95 if no further 429.

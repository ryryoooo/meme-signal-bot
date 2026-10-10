# RH Wallet Audit（品質分類）

- Updated: **2026-10-10T17:23:10.986092+00:00** (UTC) / JST+9
- Watch: `/home/runner/work/meme-signal-bot/meme-signal-bot/rh-wallets/wallets.jsonl` · total **3509**
- Elapsed: **0.3s** · tagged_rows=4 · RPC=False

## Counts

| bucket | count | 意味 |
|---|---:|---|
| ACTIVE | **401** | 最近取引 or lifetime proxy |
| INACTIVE | **3108** | 直近証拠なし / 未vet |
| QUALITY 優良 | **223** | WR≥0.5 + PnL≥1000 + n≥20 |
| WEAK | **3** | 低WR / 弱いPnL / ワンショット |

## Data coverage（どの指標か明示）

- `win_rate` filled: **272/3509** (7.8%)
- `realized_pnl*` filled: **401/3509** (11.4%)
- `n_trades*` filled: **272/3509**
- `*_7d` WR filled: **0/3509** (0.0%) ← GMGN 7d 未充足なら lifetime で判定
- `n_trades_7d` filled: **0/3509**
- `last_active*` filled: **0/3509**
- activity_metric breakdown: `{"lifetime_proxy": 401, "unvetted_proxy": 3108}`

## Thresholds

- 優良: WR≥0.5, PnL≥$1000, trades≥20, prefer_recent=True
- weak: WR<0.35 (n≥10) / PnL≤0 / oneshot n≤5
- active_days=14.0 · rpc_blocks=0

## Top 10 優良 (QUALITY)

1. `0x22055b5799d9b98e002b28f15fb2acb08ed5eee7` wr=0.6 pnl=3193958 n=447 scout=None/0.00 active=True metric=lifetime_proxy
2. `0x0cc7cedb0935ceab0b4a6b38ee7dcfd403b19a7b` wr=0.5 pnl=2548012 n=12698 scout=None/0.00 active=True metric=lifetime_proxy
3. `0x559524b8d5af5292e4ca30b30ea3dc8261e1dea4` wr=1.0 pnl=1626859 n=36 scout=None/0.00 active=True metric=lifetime_proxy
4. `0x3ce6d89e95cfa492f882ee4f69842153c80ab5cf` wr=0.6923076923076923 pnl=1647325 n=490 scout=None/0.00 active=True metric=lifetime_proxy
5. `0x5f60a59f2d243d533f82fd9844149d56ff363659` wr=0.8 pnl=1552304 n=97 scout=None/0.00 active=True metric=lifetime_proxy
6. `0x5b0051e4ea8eaf6ec523ab2aa76fe0149e68b040` wr=0.5346534653465347 pnl=1514938 n=2068 scout=None/0.00 active=True metric=lifetime_proxy
7. `0x26a2869488c4958c2aee366455a803757d8bfee7` wr=0.5 pnl=1693354 n=425 scout=None/0.00 active=True metric=lifetime_proxy
8. `0xd1794ddf809e9809572e034ba2e3b80dced1f00c` wr=0.8956043956043956 pnl=88039 n=4005 scout=elite/84.86 active=True metric=lifetime_proxy
9. `0xac6da909f2132e680aa4998263da00f519c1946b` wr=0.75 pnl=1458297 n=98 scout=None/0.00 active=True metric=lifetime_proxy
10. `0xe5e9ffe707ee071998340972af6fb178f5c64ba6` wr=0.84 pnl=866592 n=7597 scout=None/0.00 active=True metric=lifetime_proxy

## Top 10 WEAK（prune候補）

1. `0xa053641c649d93b51ce88eafec998ffb9f534f0b` wr=0.2775601566871852 pnl=24148 n=7942 reason=low_wr=0.278 n=7942
2. `0x8ee99f56672daaaff9d79b0f86d5b1e41f0f3e7b` wr=0.3237822349570201 pnl=21538 n=1867 reason=low_wr=0.324 n=1867
3. `0x2fd1887e5d99014cb0b8884f06560ed20d65003d` wr=0.3466666666666667 pnl=48891 n=2277 reason=low_wr=0.347 n=2277

## Action

- Keep **QUALITY** on watch / promote notify weight
- Prune / demote **WEAK** + long **INACTIVE** (dry-run via prune_watch_wallets)
- Fill WR gaps via GHA `gmgn-vet-throttle` (elite priority, respect 429 cool) — box GMGN off

## Files

- `rh-wallets/audit_active.jsonl`
- `rh-wallets/audit_inactive.jsonl`
- `rh-wallets/audit_quality.jsonl`
- `rh-wallets/audit_weak.jsonl`


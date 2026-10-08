# RH Wallet Audit（品質分類）

- Updated: **2026-10-08T06:18:27.621602+00:00** (UTC) / JST+9
- Watch: `/home/runner/work/meme-signal-bot/meme-signal-bot/rh-wallets/wallets.jsonl` · total **3500**
- Elapsed: **0.3s** · tagged_rows=3193 · RPC=False

## Counts

| bucket | count | 意味 |
|---|---:|---|
| ACTIVE | **389** | 最近取引 or lifetime proxy |
| INACTIVE | **3111** | 直近証拠なし / 未vet |
| QUALITY 優良 | **217** | WR≥0.5 + PnL≥1000 + n≥20 |
| WEAK | **0** | 低WR / 弱いPnL / ワンショット |

## Data coverage（どの指標か明示）

- `win_rate` filled: **261/3500** (7.5%)
- `realized_pnl*` filled: **389/3500** (11.1%)
- `n_trades*` filled: **261/3500**
- `*_7d` WR filled: **0/3500** (0.0%) ← GMGN 7d 未充足なら lifetime で判定
- `n_trades_7d` filled: **0/3500**
- `last_active*` filled: **0/3500**
- activity_metric breakdown: `{"lifetime_proxy": 389, "unvetted_proxy": 3111}`

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
8. `0xac6da909f2132e680aa4998263da00f519c1946b` wr=0.75 pnl=1458297 n=98 scout=None/0.00 active=True metric=lifetime_proxy
9. `0xe5e9ffe707ee071998340972af6fb178f5c64ba6` wr=0.84 pnl=866592 n=7597 scout=None/0.00 active=True metric=lifetime_proxy
10. `0xde4c44e841972c2c4db1b1e27a353340fe9899de` wr=1.0 pnl=1055509 n=281 scout=None/0.00 active=True metric=lifetime_proxy

## Top 10 WEAK（prune候補）

- (none)

## Action

- Keep **QUALITY** on watch / promote notify weight
- Prune / demote **WEAK** + long **INACTIVE** (dry-run via prune_watch_wallets)
- Fill WR gaps via GHA `gmgn-vet-throttle` (elite priority, respect 429 cool) — box GMGN off

## Files

- `rh-wallets/audit_active.jsonl`
- `rh-wallets/audit_inactive.jsonl`
- `rh-wallets/audit_quality.jsonl`
- `rh-wallets/audit_weak.jsonl`


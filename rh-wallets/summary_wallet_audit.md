# RH Wallet Audit（品質分類）

- Updated: **2026-09-19T20:57:15.963036+00:00** (UTC) / JST+9
- Watch: `/home/runner/work/meme-signal-bot/meme-signal-bot/rh-wallets/wallets.jsonl` · total **2568**
- Elapsed: **0.2s** · tagged_rows=0 · RPC=False

## Counts

| bucket | count | 意味 |
|---|---:|---|
| ACTIVE | **431** | 最近取引 or lifetime proxy |
| INACTIVE | **2137** | 直近証拠なし / 未vet |
| QUALITY 優良 | **225** | WR≥0.5 + PnL≥1000 + n≥20 |
| WEAK | **5** | 低WR / 弱いPnL / ワンショット |

## Data coverage（どの指標か明示）

- `win_rate` filled: **285/2568** (11.1%)
- `realized_pnl*` filled: **431/2568** (16.8%)
- `n_trades*` filled: **285/2568**
- `*_7d` WR filled: **0/2568** (0.0%) ← GMGN 7d 未充足なら lifetime で判定
- `n_trades_7d` filled: **0/2568**
- `last_active*` filled: **0/2568**
- activity_metric breakdown: `{"unvetted_proxy": 2137, "lifetime_proxy": 431}`

## Thresholds

- 優良: WR≥0.5, PnL≥$1000, trades≥20, prefer_recent=True
- weak: WR<0.35 (n≥10) / PnL≤0 / oneshot n≤5
- active_days=14.0 · rpc_blocks=0

## Top 10 優良 (QUALITY)

1. `0x22055b5799d9b98e002b28f15fb2acb08ed5eee7` wr=0.6 pnl=3193958 n=447 scout=None/0.00 active=True metric=lifetime_proxy
2. `0x0cc7cedb0935ceab0b4a6b38ee7dcfd403b19a7b` wr=0.5 pnl=2548012 n=12698 scout=None/0.00 active=True metric=lifetime_proxy
3. `0xb8f305f27ccc406373de0082cc06cb1d065504ea` wr=0.6 pnl=2579440 n=567 scout=None/0.00 active=True metric=lifetime_proxy
4. `0x559524b8d5af5292e4ca30b30ea3dc8261e1dea4` wr=1.0 pnl=1626859 n=36 scout=None/0.00 active=True metric=lifetime_proxy
5. `0x5f60a59f2d243d533f82fd9844149d56ff363659` wr=0.8 pnl=1552304 n=97 scout=None/0.00 active=True metric=lifetime_proxy
6. `0x5b0051e4ea8eaf6ec523ab2aa76fe0149e68b040` wr=0.5346534653465347 pnl=1514938 n=2068 scout=None/0.00 active=True metric=lifetime_proxy
7. `0x26a2869488c4958c2aee366455a803757d8bfee7` wr=0.5 pnl=1693354 n=425 scout=None/0.00 active=True metric=lifetime_proxy
8. `0xac6da909f2132e680aa4998263da00f519c1946b` wr=0.75 pnl=1458297 n=98 scout=None/0.00 active=True metric=lifetime_proxy
9. `0xe5e9ffe707ee071998340972af6fb178f5c64ba6` wr=0.84 pnl=866592 n=7597 scout=None/0.00 active=True metric=lifetime_proxy
10. `0xde4c44e841972c2c4db1b1e27a353340fe9899de` wr=1.0 pnl=1055509 n=281 scout=None/0.00 active=True metric=lifetime_proxy

## Top 10 WEAK（prune候補）

1. `0xe7db4e546edc6a341a6d63475d5238b4cc1be602` wr=0.22186322024771135 pnl=109068 n=8992 reason=low_wr=0.222 n=8992
2. `0x24e91130ba7fb21f853d709a1ffb8dee99ba7ba3` wr=0.26048218029350106 pnl=63992 n=14175 reason=low_wr=0.260 n=14175
3. `0xf565bee3b18f8a0cf253a7f872fe1e0e5dfc471d` wr=0.29849624060150376 pnl=18057 n=5035 reason=low_wr=0.298 n=5035
4. `0xd632a243fb30cbf5f53a78fa0ed01e7ba13d1718` wr=0.300531914893617 pnl=28336 n=6162 reason=low_wr=0.301 n=6162
5. `0xa053641c649d93b51ce88eafec998ffb9f534f0b` wr=0.3077311773623042 pnl=74277 n=15919 reason=low_wr=0.308 n=15919

## Action

- Keep **QUALITY** on watch / promote notify weight
- Prune / demote **WEAK** + long **INACTIVE** (dry-run via prune_watch_wallets)
- Fill WR gaps via GHA `gmgn-vet-throttle` (elite priority, respect 429 cool) — box GMGN off

## Files

- `rh-wallets/audit_active.jsonl`
- `rh-wallets/audit_inactive.jsonl`
- `rh-wallets/audit_quality.jsonl`
- `rh-wallets/audit_weak.jsonl`


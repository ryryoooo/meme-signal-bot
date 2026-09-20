# Unknown-trend promoted wallets — PnL status

- Updated: **2026-09-20 16:10 JST**
- Source: on-chain / Gecko trade-history estimates (`pnl_source=onchain_est`) — **not GMGN**
- Priority addrs file: 95 (`raw/unknown_trend_pnl_pending_addrs.txt`)

## First batch (`summary_onchain_pnl.md`)

| bucket (`realized_pnl_usd_est`) | count |
|--------|------:|
| positive (>) | **41** |
| negative (<) | **19** |
| zero (~0) | **20** |
| still null / no trades in window | **15** |

- Trade hits: **80/95**
- Quality-gated promote → `realized_pnl_usd` + clear `pnl_pending`: **39** (`pass_pnl=39`)
- Remaining `pnl_pending` among 95: **56** (neg/zero/low-quality/null — daemon keeps filling)

## Fill path

- Box GMGN: **disabled**
- Continuous: `scripts/estimate_wallet_pnl_onchain.py` via daemon (`PNL_FILL_EVERY_SEC=900` + after trend promote)
- Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`; promote when est>0 and quality ok
- Logs: `rh-wallets/summary_onchain_pnl.md`, `rh-wallets/pnl_fill_log.jsonl`

**Honest: these are estimates from recent Gecko pool trades + optional MTM — not vetted GMGN PnL.**

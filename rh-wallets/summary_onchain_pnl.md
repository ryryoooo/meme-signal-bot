# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 16:33 JST**
- Targets attempted: **30**
- With trade hits: **19**
- Estimates (non-null): **19**

## Estimate buckets (`realized_pnl_usd_est` — honest estimate)

| bucket | count |
|--------|------:|
| positive (>) | **1** |
| negative (<) | **10** |
| zero (~0) | **8** |
| still null / no trades | **11** |

- Promoted to `realized_pnl_usd` (quality gate): **1**
- `pass_pnl` set: **1**
- `pnl_pending` cleared: **1**
- Weak fields filled (label/n_trades/wr/last_active): **34**
- Unique CAs fetched: **23** (rpc_429=0)

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.
- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).
- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.
- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.


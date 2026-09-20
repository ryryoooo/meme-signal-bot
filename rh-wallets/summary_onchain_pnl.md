# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 16:17 JST**
- Targets attempted: **30**
- With trade hits: **15**
- Estimates (non-null): **15**

## Estimate buckets (`realized_pnl_usd_est` — honest estimate)

| bucket | count |
|--------|------:|
| positive (>) | **1** |
| negative (<) | **10** |
| zero (~0) | **4** |
| still null / no trades | **15** |

- Promoted to `realized_pnl_usd` (quality gate): **0**
- `pass_pnl` set: **0**
- `pnl_pending` cleared: **0**
- Weak fields filled (label/n_trades/wr/last_active): **31**
- Unique CAs fetched: **23** (rpc_429=0)

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.
- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).
- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.
- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.


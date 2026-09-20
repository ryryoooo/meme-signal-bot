# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 17:25 JST**
- Targets attempted: **30**
- With trade hits: **30**
- Estimates (non-null): **30**

## Estimate buckets (`realized_pnl_usd_est` — honest estimate)

| bucket | count |
|--------|------:|
| positive (>) | **22** |
| negative (<) | **5** |
| zero (~0) | **3** |
| still null / no trades | **0** |

- Promoted to `realized_pnl_usd` (quality gate): **22**
- `pass_pnl` set: **22**
- `pnl_pending` cleared: **22**
- Weak fields filled (label/n_trades/wr/last_active): **120**
- Unique CAs fetched: **29** (rpc_429=0)

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.
- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).
- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.
- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.


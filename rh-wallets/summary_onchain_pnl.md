# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 17:40 JST**
- Targets attempted: **30**
- With trade hits: **30**
- Estimates (non-null): **30**

## Estimate buckets (`realized_pnl_usd_est` — honest estimate)

| bucket | count |
|--------|------:|
| positive (>) | **23** |
| negative (<) | **5** |
| zero (~0) | **2** |
| still null / no trades | **0** |

- Promoted to `realized_pnl_usd` (quality gate): **23**
- `pass_pnl` set: **23**
- `pnl_pending` cleared: **10**
- Weak fields filled (label/n_trades/wr/last_active): **75**
- Unique CAs fetched: **27** (rpc_429=0)

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.
- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).
- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.
- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.


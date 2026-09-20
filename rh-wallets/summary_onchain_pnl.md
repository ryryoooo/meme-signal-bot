# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 16:09 JST**
- Targets attempted: **95**
- With trade hits: **80**
- Estimates (non-null): **80**

## Estimate buckets (`realized_pnl_usd_est` — honest estimate)

| bucket | count |
|--------|------:|
| positive (>) | **41** |
| negative (<) | **19** |
| zero (~0) | **20** |
| still null / no trades | **15** |

- Promoted to `realized_pnl_usd` (quality gate): **39**
- `pass_pnl` set: **39**
- `pnl_pending` cleared: **39**
- Weak fields filled (label/n_trades/wr/last_active): **365**
- Unique CAs fetched: **30** (rpc_429=0)

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.
- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).
- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.
- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.


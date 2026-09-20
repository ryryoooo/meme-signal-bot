# On-chain PnL estimates (Gecko trades via jina; no box GMGN)

- Updated: **2026-09-20 16:12 JST**
- First batch targets: **95** (`unknown_trend_pnl_pending_addrs.txt`)
- Continuous daemon fill: every **900s** + after trend promote (`PNL_EST_CAP=40`)

## First batch buckets (`realized_pnl_usd_est` — **estimate**, not GMGN)

| bucket | count |
|--------|------:|
| positive (>) | **41** |
| negative (<) | **19** |
| zero (~0) | **20** |
| still null / no trades in window | **15** |

- With trade hits (meta): **95/95**
- Quality-gated promote → `realized_pnl_usd` + clear `pnl_pending`: **39**
- Still `pnl_pending` among the 95: **56**

## Notes

- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay (+ optional MTM).
- Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`; promote when est>0 and quality ok.
- Logs: `rh-wallets/pnl_fill_log.jsonl`, state `rh-wallets/raw/onchain_pnl_state.json`
- **Honest: estimates only** — recent pool-trade window, not full wallet history / not GMGN-vetted.

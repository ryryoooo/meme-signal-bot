# rh-wallets

Main watchlist: **`wallets.jsonl`** (onchain signal tick reads this).

## Unknown trend promote

- Hunt stages unknowns → `unknown_trend_smart.jsonl` / `watch_candidates_unknown.jsonl` (not auto-merged).
- Each daemon `trend_hunt` cycle runs `scripts/promote_unknown_smart.py`: analyze active multi-CA smart-ish wallets, then **append only passers** (cap ~40/cycle).
- Tags: `unknown_trend`, `source=trend_hunt`, `pnl_pending`.
- Log: `promote_unknown_log.jsonl` · summary: `summary_promote_unknown.md`.
- Tunables: `PROMOTE_MIN_SCORE`, `PROMOTE_MIN_CAS`, `PROMOTE_MAX_PER_CYCLE`, `PROMOTE_ACTIVE_HOURS` (see `NOTIFY_GATES.md`).

Never dump the full 200+ unknown list into the watchlist.

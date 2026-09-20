# sol-wallets — StonkFun diggers (Solana)

Skilled early buyers ("diggers") on [StonkFun](https://www.stonkfun.xyz) stock-paired launches.

- `stonkfun_diggers.jsonl` — scored digger wallets (`chain=solana`)
- `summary_stonkfun_diggers.md` — human summary
- `raw/` — hunt state + per-mint early-buyer cache + signal tick state

**Do not merge into `rh-wallets/wallets.jsonl`** (Robinhood / EVM). Keep Solana watch separate.

## Collectors

- Hunt: `scripts/hunt_stonkfun_diggers.py` (free StonkFun API + public Solana RPC)
- Daemon interval: `scripts/scout-wallet-bot.sh` → `STONKFUN_EVERY_SEC` (default 1800)

## Discord signals (dedicated channel)

Live digger buys → Discord via:

- Tick: `scripts/stonkfun_signal_tick.py` / `scripts/stonkfun_signal_tick.sh`
- Poll ~15–30s (`STONKFUN_POLL_SECONDS`, default 20)
- **Webhook: `DISCORD_STONKFUN_WEBHOOK_URL` only**
- **Never falls back to `DISCORD_WEBHOOK_URL`** (RH smart-money channel stays clean)
- If the dedicated webhook is empty, the tick still runs but **skips posts** until set
- Test: `STONKFUN_SIGNAL_TEST=1` posts one **テスト** embed to the new channel
- `LIVE_TRADING=0`, no box GMGN

Start:

```bash
# persistent daemon (beside onchain signal_tick)
nohup bash scripts/stonkfun_signal_tick.sh >>/home/box/.local/share/scout-wallet-bot/stonkfun_signal_tick.log 2>&1 &

# one-shot + test embed
STONKFUN_SIGNAL_TEST=1 STONKFUN_TICK_ONCE=1 bash scripts/stonkfun_signal_tick.sh
```


## Solana-wide smart unknown (separate from StonkFun)

Quality active smart-wallet discovery across Solana trending / graduating tokens — **not** StonkFun-only.

- `sol_smart_unknown.jsonl` — scored unknown wallets (`source=solana_trend`, `chain=solana`)
- `summary_sol_smart_unknown.md` — human summary
- `watch_candidates_sol.jsonl` — quality-gated staging (not Discord-wired yet)
- `raw/sol_smart_hunt_state.json` — hunt state

Hunt: `scripts/hunt_sol_smart_wallets.py` (DexScreener + GeckoTerminal via jina + optional pump.fun; free Solana RPC unused by default for buyers).

Daemon interval: `SOL_SMART_EVERY_SEC` (default 1800) in `scripts/scout-wallet-bot.sh` — independent of `STONKFUN_EVERY_SEC`.

Excludes hubs/CEX/routers and (by default) addresses already on `stonkfun_diggers.jsonl` so the two lists stay distinct. **Never** merges into RH `wallets.jsonl` or StonkFun Discord.

## Dump-dip smart wallets (pre + post graduation)

Wallets that profit by **buying dips after dumps** — complementary to early-curve diggers.

Stages (tagged per entry / wallet):
- `pre_grad_dip` — dip buys on bonding curves (LaunchLab / pump.fun curve) before graduation
- `post_grad_dip` — dip buys after graduation (Raydium / Jupiter / pump AMM), e.g. WOJAK-style

Outputs:
- `sol_dump_dip_smart.jsonl` — scored multi-mint dump-dip wallets (`source=dump_dip`)
- `summary_sol_dump_dip.md` — human summary
- `raw/dump_dip_state.json` / `raw/dump_dip_all.jsonl` / `raw/dump_dip/`
- Quality-gated merge into `watch_candidates_sol.jsonl` with tag `dump_dip` (does not overwrite stonkfun early-only)

Hunt: `scripts/hunt_sol_dump_dip.py` (reuses `raw/seed_74pB` entry mints when present; Dex/Gecko OHLCV dump windows; official/publicnode RPC)

```bash
LIVE_TRADING=0 GMGN_DISABLED=1 \
  SOLANA_RPC_URL=https://solana-rpc.publicnode.com \
  DUMPDIP_CA_CAP=28 DUMPDIP_DUMP_PCT=0.35 DUMPDIP_ONCE=1 \
  python3 scripts/hunt_sol_dump_dip.py --once
```

Daemon interval: `DUMPDIP_EVERY_SEC` (default 3600) in `scripts/scout-wallet-bot.sh` — independent of early digger / sol7d hunts.
Never merges into RH `wallets.jsonl`.

## Solana 7d active smart wallets (official RPC)

- `sol_smart_7d_active.jsonl` — multi-mint active wallets (`source=solana_rpc_7d`)
- `summary_sol_smart_7d.md` — summary
- `raw/sol_smart_7d_state.json` / `raw/sol_smart_7d_all.jsonl` / `raw/sol7d_pools/`
- Quality merge into `watch_candidates_sol.jsonl`

Hunt: `scripts/hunt_sol_smart_7d.py` (default RPC `https://api.mainnet-beta.solana.com`).

```bash
LIVE_TRADING=0 GMGN_DISABLED=1 SOLANA_RPC_URL=https://api.mainnet-beta.solana.com   SOL7D_CA_CAP=48 SOL7D_TX_PER_CA=28 SOL7D_ONCE=1   python3 scripts/hunt_sol_smart_7d.py --once
```

Daemon: `SOL7D_EVERY_SEC` in `scripts/scout-wallet-bot.sh` (default 3600). Separate from StonkFun / RH.

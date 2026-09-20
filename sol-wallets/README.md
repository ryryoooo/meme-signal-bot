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

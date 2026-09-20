# sol-wallets — StonkFun diggers (Solana)

Skilled early buyers ("diggers") on [StonkFun](https://www.stonkfun.xyz) stock-paired launches.

- `stonkfun_diggers.jsonl` — scored digger wallets (`chain=solana`)
- `summary_stonkfun_diggers.md` — human summary
- `raw/` — hunt state + per-mint early-buyer cache

**Do not merge into `rh-wallets/wallets.jsonl`** (Robinhood / EVM). Keep Solana watch separate.

Collector: `scripts/hunt_stonkfun_diggers.py` (free StonkFun API + public Solana RPC).
Daemon: `scripts/scout-wallet-bot.sh` interval `STONKFUN_EVERY_SEC` (default 1800).

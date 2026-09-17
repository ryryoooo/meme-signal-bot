export GMGN_DISABLED="${GMGN_DISABLED:-1}"
#!/bin/bash
set -euo pipefail
cd /workspace/meme-foundation/discord-bot
export LIVE_WALLET_ADDRESS=0x822AFdCc7f1Ec829f4456A3921ff54B4a6dBCfAe
export LIVE_TICK_DIR=/workspace/meme-foundation/live-arc
# Fast mark loop defaults (env-overridable before this script if needed)
export LIVE_TICK_SEC="${LIVE_TICK_SEC:-1}"
export LIVE_SYNC_SEC="${LIVE_SYNC_SEC:-20}"
export LIVE_PRICE_TTL="${LIVE_PRICE_TTL:-1.0}"
export LIVE_EXIT_GRACE_SEC="${LIVE_EXIT_GRACE_SEC:-15}"
export LIVE_STOP_CONFIRM="${LIVE_STOP_CONFIRM:-1}"
# GMGN_ALLOW kept for any leftover signal tooling; swaps use V4 directly
export GMGN_ALLOW_AUTOMATED_TRADES=1
python3 - <<'PY'
from load_secrets import load, write_gmgn_dotenv
import os
from pathlib import Path
os.environ.pop("GMGN_API_KEY", None)
os.environ.pop("GMGN_PRIVATE_KEY", None)
st = load(["GMGN_API_KEY", "GMGN_PRIVATE_KEY", "DISCORD_ARC_LIVE_WEBHOOK_URL"])
write_gmgn_dotenv()
assert os.environ.get("GMGN_PRIVATE_KEY"), f"GMGN_PRIVATE_KEY missing: {st}"
# env file for parent (mode 600) — PK required; API key optional for V4 swaps
p = Path("/workspace/meme-foundation/live-arc/.gmgn.env")
lines = [f"GMGN_PRIVATE_KEY={os.environ['GMGN_PRIVATE_KEY']}"]
if os.environ.get("GMGN_API_KEY"):
    lines.insert(0, f"GMGN_API_KEY={os.environ['GMGN_API_KEY']}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
p.chmod(0o600)
print("preflight_ok v4_swap=1 fast_tick=1")
PY
set -a
# shellcheck disable=SC1091
source /workspace/meme-foundation/live-arc/.gmgn.env
set +a
exec python3 /workspace/meme-foundation/discord-bot/live_tick.py

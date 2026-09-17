#!/usr/bin/env bash
# Near-realtime @xbtscout early-call → Discord (RH only). No GMGN.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CHAIN=robinhood
export GMGN_DISABLED=1
export GMGN_MARKET=0
export GMGN_SMARTMONEY=0
export XBTSCOUT_ENABLED=1
export XBTSCOUT_ON_ANY_CHAIN=1
export XBTSCOUT_NOTIFY=1
export XBTSCOUT_NOTIFY_CHAINS=robinhood
export XBTSCOUT_EARLY_CALL_ONLY=1
if [ -f xbtscout/NOTIFY_AFTER ]; then export XBTSCOUT_NOTIFY_AFTER="$(cat xbtscout/NOTIFY_AFTER)"; fi
export XBTSCOUT_SCRAPE_SECONDS="${XBTSCOUT_SCRAPE_SECONDS:-30}"
export POLL_SECONDS="${POLL_SECONDS:-30}"
export ALLOW_GMGN_CLUSTER=0
# load discord webhook from card secrets
python3 - <<'PY'
from load_secrets import load
load(["DISCORD_XBTSCOUT_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"])
PY
LOG="${XBTSCOUT_WATCH_LOG:-/workspace/meme-foundation/live-arc/xbtscout_watch.log}"
mkdir -p "$(dirname "$LOG")"
echo "$(date '+%Y-%m-%dT%H:%M:%S') xbtscout_watch start interval=${POLL_SECONDS}s" | tee -a "$LOG"
while true; do
  echo "$(date '+%Y-%m-%dT%H:%M:%S') scrape+notify" >>"$LOG"
  python3 bot.py --notify-xbtscout >>"$LOG" 2>&1 || true
  # also persist scrape into cas via notify path; --notify only does new is_new by default — force full scrape notify
  sleep "$POLL_SECONDS"
done

#!/bin/bash
# Box-local Arc signal loop (~60s). Primary entry path for live_tick.
# LIVE_TRADING=0 here — live_tick owns swaps. No Super / no GHA wake.
set -euo pipefail
BOT=/workspace/meme-foundation/discord-bot
LIVE=/workspace/meme-foundation/live-arc
LOG=$LIVE/signal_fast.log
INTERVAL="${SIGNAL_FAST_SEC:-60}"

cd "$BOT"

# Prefer quality watchlist
if [ -f arc-wallets/wallets_quality.jsonl ]; then
  WL=arc-wallets/wallets_quality.jsonl
else
  WL=arc-wallets/wallets.jsonl
fi

export CHAIN=arc
export LIVE_TRADING=0
export PAPER_TRADING=0
export NANSEN_FOR_TRADES=0
export FOMO_ENABLED="${FOMO_ENABLED:-0}"
export XBTSCOUT_ENABLED=0
export ALLOW_GMGN_CLUSTER=0
export WATCHLIST_PATH="$BOT/$WL"
export STATE_PATH=$LIVE/signal_state_arc.json
export PAPER_LOG_PATH=$LIVE/signal_log_arc.jsonl
export PAPER_BOOK_PATH=$LIVE/signal_book_arc.jsonl
export LIVE_LOCAL_ALERTS=$LIVE/local_alerts.json
export GMGN_LIMIT="${GMGN_LIMIT:-40}"
export WINDOW_SECONDS="${WINDOW_SECONDS:-900}"
export MIN_WALLETS="${MIN_WALLETS:-2}"

# Load secrets once (API key for GMGN reads; Discord for notify)
python3 - <<'PY'
from load_secrets import load, write_gmgn_dotenv
import os
from pathlib import Path
load(["GMGN_API_KEY", "DISCORD_ARC_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "DISCORD_ARC_LIVE_WEBHOOK_URL"])
write_gmgn_dotenv()
# persist non-secret-ish env for parent source
envp = Path("/workspace/meme-foundation/live-arc/.signal.env")
lines = []
for k in ("GMGN_API_KEY", "DISCORD_ARC_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "DISCORD_ARC_LIVE_WEBHOOK_URL"):
    v = (os.environ.get(k) or "").strip()
    if v:
        lines.append(f"{k}={v}")
envp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
envp.chmod(0o600)
print("signal_preflight_ok gmgn_key=", bool(os.environ.get("GMGN_API_KEY")))
PY
set -a
# shellcheck disable=SC1091
source "$LIVE/.signal.env" 2>/dev/null || true
# also reuse gmgn dotenv if present
if [ -f "$LIVE/.gmgn.env" ]; then
  # shellcheck disable=SC1091
  source "$LIVE/.gmgn.env"
fi
set +a

echo "$(date '+%Y-%m-%dT%H:%M:%S') signal_fast start interval=${INTERVAL}s wl=$WL" | tee -a "$LOG"

while true; do
  t0=$(date +%s)
  echo "$(date '+%Y-%m-%dT%H:%M:%S') signal_fast run_once begin" >>"$LOG"
  set +e
  python3 "$BOT/bot.py" >>"$LOG" 2>&1
  rc=$?
  set -e
  # Dump open_alerts → local_alerts.json for live_tick (mtime = freshness)
  python3 - <<'PY' >>"$LOG" 2>&1 || true
import json, time
from pathlib import Path
LIVE = Path("/workspace/meme-foundation/live-arc")
st_path = LIVE / "signal_state_arc.json"
out_path = LIVE / "local_alerts.json"
alerts = []
if st_path.exists():
    try:
        st = json.loads(st_path.read_text(encoding="utf-8"))
        for a in st.get("open_alerts") or []:
            alerts.append(dict(a))
    except Exception as e:
        print(f"local_alerts dump state fail: {type(e).__name__}")
# also fold recent posted rows from signal log
log_path = LIVE / "signal_log_arc.jsonl"
if log_path.exists():
    try:
        for ln in log_path.read_text(encoding="utf-8").splitlines()[-200:]:
            if not ln.strip():
                continue
            row = json.loads(ln)
            if not row.get("posted"):
                continue
            ca = (row.get("ca") or "").lower()
            if not ca:
                continue
            alerts.append({
                "ca": ca,
                "symbol": row.get("symbol"),
                "alert_price_usd": row.get("alert_price_usd") or row.get("price_usd"),
                "alert_mcap": row.get("mcap"),
                "alert_liq": row.get("liq"),
                "posted_at": time.time(),  # treat as fresh for age gate if ts missing
                "n": row.get("n") or 2,
                "chain": "arc",
            })
    except Exception as e:
        print(f"local_alerts dump log fail: {type(e).__name__}")
# dedupe by ca newest first is handled by live_tick
tmp = out_path.with_suffix(".tmp")
tmp.write_text(json.dumps(alerts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(out_path)
print(f"local_alerts wrote n={len(alerts)}")
PY
  echo "$(date '+%Y-%m-%dT%H:%M:%S') signal_fast run_once end rc=$rc" >>"$LOG"
  t1=$(date +%s)
  elapsed=$((t1 - t0))
  sleep_for=$((INTERVAL - elapsed))
  if [ "$sleep_for" -lt 5 ]; then sleep_for=5; fi
  sleep "$sleep_for"
done

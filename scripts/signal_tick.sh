#!/usr/bin/env bash
# Box-resident RH FOMO signal tick — primary Discord notify path (~20s).
# GMGN stays OFF on box (GHA owns GMGN). Shared state via release tag signal-state.
#
# Env knobs:
#   SIGNAL_POLL_SECONDS   loop interval (default 20)
#   SIGNAL_TICK_ONCE=1    run one tick and exit
#   SIGNAL_STATE_SYNC=0   disable release pull/push
#   FOMO_POLL_SECONDS     defaults to SIGNAL_POLL_SECONDS
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${SIGNAL_TICK_STATE_DIR:-/home/box/.local/share/scout-wallet-bot}"
LOG="${SIGNAL_TICK_LOG:-$STATE_DIR/signal_tick.log}"
HEALTH="${SIGNAL_TICK_HEALTH:-$STATE_DIR/health.json}"
POLL="${SIGNAL_POLL_SECONDS:-20}"
ONCE="${SIGNAL_TICK_ONCE:-0}"
DO_SYNC="${SIGNAL_STATE_SYNC:-1}"
mkdir -p "$STATE_DIR"
cd "$ROOT"

log() {
  if [[ -f "$LOG" ]] && [[ $(stat -c%s "$LOG" 2>/dev/null || echo 0) -gt 2097152 ]]; then
    mv "$LOG" "$LOG.1" 2>/dev/null || true
  fi
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
}

patch_health() {
  local rc="$1" elapsed="$2" fomo_ok="$3"
  python3 - "$HEALTH" "$rc" "$elapsed" "$fomo_ok" "$POLL" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
rc, elapsed, fomo_ok, poll = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
data = {}
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
try:
    rc_v = int(rc)
except Exception:
    rc_v = rc
try:
    el_v = float(elapsed)
except Exception:
    el_v = elapsed
data["signal_tick"] = {
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "rc": rc_v,
    "elapsed_sec": el_v,
    "poll_sec": int(float(poll)),
    "fomo_key": fomo_ok == "1",
    "gmgn": "disabled",
}
path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

# Load box secrets into this shell (never print values)
eval "$(python3 - <<'PY'
from load_secrets import load
import os, shlex
keys = [
    "FOMO_API_KEY",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_PAPER_WEBHOOK_URL",
    "DISCORD_XBTSCOUT_WEBHOOK_URL",
    "DISCORD_PRIORITY_WEBHOOK_URL",
]
st = load(keys)
for k in keys:
    v = (os.environ.get(k) or "").strip()
    if v:
        print(f"export {k}={shlex.quote(v)}")
print(f"export _SIGNAL_TICK_FOMO_OK={'1' if st.get('FOMO_API_KEY') else '0'}")
print(f"export _SIGNAL_TICK_DISCORD_OK={'1' if st.get('DISCORD_WEBHOOK_URL') else '0'}")
PY
)"
FOMO_OK="${_SIGNAL_TICK_FOMO_OK:-0}"
DISCORD_OK="${_SIGNAL_TICK_DISCORD_OK:-0}"

# Env block derived from .github/workflows/signal.yml (GMGN forced off; FOMO poll = tick)
# --- signal.yml parity (box overrides: GMGN off, FOMO poll=tick) ---
# Auto-derived from signal.yml — do not hand-edit; regenerate via scripts/signal_tick.sh builder
export GMGN_DISABLED="1"
export NANSEN_FOR_TRADES="0"
export LIVE_TRADING="0"
export ALLOW_GMGN_CLUSTER="0"
export PAPER_BANKROLL_USD="300"
export POST_SKIP_NOTICES="0"
export PAPER_TRADING="0"
export PAPER_ONE_PER_CHAIN="0"
export PAPER_MAX_OPEN="5"
export PAPER_MAX_ENTRIES_WEEK="0"
export PAPER_MAX_LOSSES_WEEK="0"
export PAPER_MARK_HEARTBEAT="0"
export CHAIN="robinhood"
export WINDOW_SECONDS="1200"
export MIN_WALLETS="1"
export MIN_TRADE_USD="40"
export MIN_CLUSTER_USD="80"
export MIN_LIQ_USD="0"
export MIN_MCAP_USD="1500"
export LIQ_MCAP_MIN="0"
export LIQ_REQUIRED="0"
export RH_SOFT_GATES="1"
export NOTIFY_PASSTHROUGH="1"
export RH_NOTIFY_ALWAYS="1"
export PRIORITY_NOTIFY="1"
export MIN_CLUSTER_PRIORITY="150"
export PRIORITY_MIN_WALLETS="2"
export PRIORITY_MIN_AGE_SEC="1800"
export PRIORITY_MAX_AGE_SEC="172800"
export PRIORITY_MIN_ABS_M5="1"
export PRIORITY_MIN_ABS_H1="2"
export PRIORITY_MIN_VOLUME_M5="100"
export PRIORITY_TITLE_PREFIX="【優先】"
export PRIORITY_REQUIRE_AGE="0"
export MIN_TOKEN_AGE_SEC="180"
export MAX_TOKEN_AGE_SEC="604800"
export PLAYBOOK_REQUIRED="0"
export SET1_MIN_AGE_SEC="1800"
export SET1_MAX_AGE_SEC="172800"
export SET2_MIN_AGE_SEC="172800"
export SET2_MAX_AGE_SEC="604800"
export SET1_MIN_PUMP_PCT="40"
export SET1_CORR_M5_MAX="25"
export SET2_MIN_DUMP_PCT="-50"
export SET2_MAX_M5_ABS="10"
export SET2_MAX_H1_ABS="30"
export SET2_MIN_VOLUME_H24="1500"
export REQUIRE_WALLET_QUALITY="0"
export MIN_WALLET_QUALITY="1.0"
export MIN_AVG_WALLET_QUALITY="0.6"
export MIN_VOLUME_H24_USD="1500"
export VOLUME_REQUIRED="0"
export MIN_VOLUME_M5_USD="200"
export VOLUME_M5_REQUIRED="0"
export ALLOW_PRE_GRAD="1"
export REQUIRE_GRADUATED="0"
export PRE_GRAD_MIN_VOLUME_M5="150"
export REQUIRE_BUY_INCREASE="0"
export BUY_VOLUME_REQUIRED="0"
export MIN_BUY_VOLUME_M5_USD="120"
export MIN_BUYS_M5="3"
export DROP_WEAK_WALLETS="0"
export WEAK_WALLET_MAX_SCORE="0.5"
export WATCH_MIN_REALIZED_HARD="100"
export MIN_M5_SELL_RATIO="0"
export MIN_ABS_PRICE_CHANGE_M5="1"
export REQUIRE_PRICE_MOVE="0"
export MIN_ABS_PRICE_MOVE_H1="2"
export MAX_PRICE_CHANGE_M5_PCT="99999"
export MAX_PRICE_CHANGE_H1_PCT="99999"
export MAX_M5_BUY_RATIO="1.0"
export ANTI_SPIKE_REQUIRED="0"
export DROP_BOT_WALLETS="0"
export REQUIRE_EARLY_HIT="0"
export COOLDOWN_SECONDS="900"
export WATCH_MIN_REALIZED_USD="0"
export WATCH_MIN_WINRATE="0.40"
export WATCH_MIN_TRADES_FOR_WR="10"
export WATCH_MIN_TRADES="10"
export WATCH_MIN_AVG_PNL_PER_TRADE="30"
export REQUIRE_CONSISTENT_PNL="0"
export WATCH_MIN_PROFIT_TOKENS="2"
export WATCH_MIN_EARLY2X_TOKENS="1"
export ALLOW_FOMO_WITHOUT_WR="1"
export FOMO_WATCH_MIN_PNL="3000"
export FOMO_TAPE_MIN_BUY_USD="150"
export GMGN_LIMIT="100"
export FOMO_ENABLED="1"
export FOMO_POLL_SECONDS="${FOMO_POLL_SECONDS:-$POLL}"
export FOMO_ALERT_LIMIT="80"
export FOMO_HOLDERS="0"
export FOMO_HOLDERS_INTERVAL="14400"
export FOMO_WATCH_PATH="${FOMO_WATCH_PATH:-fomo-wallets/wallets_evm.jsonl}"
export XBTSCOUT_ENABLED="0"
export XBTSCOUT_SCRAPE_SECONDS="120"
export XBTSCOUT_MAX_TOKENS="3"
export XBTSCOUT_NOTIFY="0"
export XBTSCOUT_NOTIFY_CHAINS="robinhood"
export XBTSCOUT_EARLY_CALL_ONLY="1"
export XBTSCOUT_NOTIFY_AFTER="2026-09-17T08:07:36.351932+00:00"
export WATCHLIST_PATH="${WATCHLIST_PATH:-rh-wallets/wallets.jsonl}"
export STATE_PATH="${STATE_PATH:-$ROOT/state.json}"
export PAPER_LOG_PATH="${PAPER_LOG_PATH:-$ROOT/paper_log.jsonl}"
export PAPER_BOOK_PATH="${PAPER_BOOK_PATH:-$ROOT/paper_book.jsonl}"
export PAPER_SUMMARY_PATH="${PAPER_SUMMARY_PATH:-$ROOT/paper_summary.md}"
export GMGN_SMARTMONEY="0"

if [[ "$FOMO_OK" != "1" ]]; then
  log "WARN FOMO_API_KEY missing — tick without FOMO tape (no GMGN on box)"
  export FOMO_ENABLED=0
fi
if [[ "$DISCORD_OK" != "1" ]]; then
  log "WARN DISCORD_WEBHOOK_URL missing — notifies will no-op"
fi

echo "$$" > "$STATE_DIR/signal_tick.pid"
log "signal_tick start poll=${POLL}s once=${ONCE} fomo=${FOMO_ENABLED} fomo_key=${FOMO_OK} gmgn=off state=$STATE_PATH sync=$DO_SYNC"

run_once() {
  local t0 t1 elapsed rc
  t0=$(date +%s)
  log "tick begin"
  if [[ "$DO_SYNC" == "1" ]]; then
    ROOT="$ROOT" STATE_PATH="$STATE_PATH" bash "$ROOT/scripts/signal_state_sync.sh" pull >>"$LOG" 2>&1 || true
  fi
  set +e
  timeout "${SIGNAL_TICK_TIMEOUT:-180}" python3 "$ROOT/bot.py" --per-page 100 >>"$LOG" 2>&1
  rc=$?
  set +e
  if [[ "$DO_SYNC" == "1" ]]; then
    ROOT="$ROOT" STATE_PATH="$STATE_PATH" bash "$ROOT/scripts/signal_state_sync.sh" push >>"$LOG" 2>&1 || true
  fi
  t1=$(date +%s)
  elapsed=$((t1 - t0))
  log "tick end rc=$rc elapsed=${elapsed}s"
  patch_health "$rc" "$elapsed" "$FOMO_OK"
  return 0
}

if [[ "$ONCE" == "1" ]]; then
  run_once
  exit 0
fi

while true; do
  t0=$(date +%s)
  run_once
  t1=$(date +%s)
  elapsed=$((t1 - t0))
  sleep_for=$((POLL - elapsed))
  if (( sleep_for < 3 )); then sleep_for=3; fi
  sleep "$sleep_for"
done

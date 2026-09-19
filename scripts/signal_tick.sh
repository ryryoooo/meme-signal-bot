#!/usr/bin/env bash
# Box-resident RH FOMO signal tick — primary Discord notify path.
# GMGN stays OFF on box (GHA owns GMGN). Shared state via release tag signal-state.
#
# Env knobs:
#   SIGNAL_POLL_SECONDS   loop interval (default 300; was 20 — burned FOMO dry)
#   SIGNAL_TICK_ONCE=1    run one tick and exit
#   SIGNAL_STATE_SYNC=0   disable release pull/push
#   FOMO_POLL_SECONDS     defaults to SIGNAL_POLL_SECONDS
#   FOMO_CREDIT_LOW=5000  remain below this → back off poll to 120–300s
#   FOMO_DRY_SLEEP_SEC    sleep when remain==0 / HTTP 402 (default 1800–3600 adaptive)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${SIGNAL_TICK_STATE_DIR:-/home/box/.local/share/scout-wallet-bot}"
LOG="${SIGNAL_TICK_LOG:-$STATE_DIR/signal_tick.log}"
HEALTH="${SIGNAL_TICK_HEALTH:-$STATE_DIR/health.json}"
LOCK="${SIGNAL_TICK_LOCK:-$STATE_DIR/signal_tick.lock}"
BASE_POLL="${SIGNAL_POLL_SECONDS:-300}"
POLL="$BASE_POLL"
ONCE="${SIGNAL_TICK_ONCE:-0}"
DO_SYNC="${SIGNAL_STATE_SYNC:-1}"
CREDIT_LOW="${FOMO_CREDIT_LOW:-5000}"
mkdir -p "$STATE_DIR"
cd "$ROOT"

# Single-instance lock (daemon + manual starts share this)
if [[ -f "$STATE_DIR/signal_tick.pid" ]]; then
  oldpid=$(cat "$STATE_DIR/signal_tick.pid" 2>/dev/null || echo "")
  if [[ "$oldpid" =~ ^[0-9]+$ ]] && ! kill -0 "$oldpid" 2>/dev/null; then
    fuser -k "$LOCK" >/dev/null 2>&1 || true
    sleep 0.2
  fi
fi
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) signal_tick already running (flock $LOCK) — exit" >>"$LOG"
  exit 0
fi

# Append-only: do NOT tee — outer nohup already redirects to the same log
log() {
  if [[ -f "$LOG" ]] && [[ $(stat -c%s "$LOG" 2>/dev/null || echo 0) -gt 2097152 ]]; then
    mv "$LOG" "$LOG.1" 2>/dev/null || true
  fi
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"
}

patch_health() {
  local rc="$1" elapsed="$2" fomo_ok="$3" remain="$4" poll_now="$5" fomo_status="$6"
  python3 - "$HEALTH" "$rc" "$elapsed" "$fomo_ok" "$poll_now" "$remain" "$fomo_status" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
rc, elapsed, fomo_ok, poll, remain, fomo_status = sys.argv[2:8]
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
try:
    rem_v = int(remain) if remain not in ("", "None", "unknown") else None
except Exception:
    rem_v = remain
data["signal_tick"] = {
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "rc": rc_v,
    "elapsed_sec": el_v,
    "poll_sec": int(float(poll)),
    "fomo_key": fomo_ok == "1",
    "fomo_remain": rem_v,
    "fomo_status": fomo_status,
    "gmgn": "disabled",
}
nl = chr(10)
path.write_text(json.dumps(data, ensure_ascii=False) + nl, encoding="utf-8")
PY
}

# Parse FOMO credit / error lines from a tick capture
parse_fomo_status() {
  local capture="$1"
  FOMO_REMAIN="unknown"
  FOMO_STATUS="ok"
  FOMO_COST=""
  if rg -q 'fomo alerts HTTP 402|fomo err=credits' "$capture" 2>/dev/null; then
    FOMO_STATUS="credits"
    FOMO_REMAIN="0"
    return 0
  fi
  local line
  line=$(rg -o 'cost=[0-9]+ remain=[0-9]+' "$capture" 2>/dev/null | tail -1 || true)
  if [[ -n "$line" ]]; then
    FOMO_COST=$(echo "$line" | rg -o 'cost=[0-9]+' | cut -d= -f2)
    FOMO_REMAIN=$(echo "$line" | rg -o 'remain=[0-9]+' | cut -d= -f2)
    if [[ "$FOMO_REMAIN" == "0" ]]; then
      FOMO_STATUS="credits"
    elif [[ "$FOMO_REMAIN" =~ ^[0-9]+$ ]] && (( FOMO_REMAIN < CREDIT_LOW )); then
      FOMO_STATUS="low"
    fi
  elif rg -q 'fomo skip: interval' "$capture" 2>/dev/null; then
    FOMO_STATUS="skip_interval"
  elif rg -q 'fomo err=' "$capture" 2>/dev/null; then
    FOMO_STATUS=$(rg -o 'fomo err=\S+' "$capture" 2>/dev/null | tail -1 | cut -d= -f2)
  fi
}

# Adaptive sleep after a tick given FOMO status
next_sleep_sec() {
  local elapsed="$1"
  local sleep_for
  if [[ "$FOMO_STATUS" == "credits" || "$FOMO_REMAIN" == "0" ]]; then
    # Dead credits: 30–60 min (prefer env FOMO_DRY_SLEEP_SEC, else 2700)
    sleep_for="${FOMO_DRY_SLEEP_SEC:-2700}"
    if (( sleep_for < 1800 )); then sleep_for=1800; fi
    if (( sleep_for > 3600 )); then sleep_for=3600; fi
    POLL="$sleep_for"
    log "FOMO CREDITS DRY remain=${FOMO_REMAIN} status=${FOMO_STATUS} — sleeping ${sleep_for}s (30–60m). Rely on GHA signal.yml GMGN backup."
    echo "$sleep_for"
    return
  fi
  if [[ "$FOMO_STATUS" == "low" ]] || { [[ "$FOMO_REMAIN" =~ ^[0-9]+$ ]] && (( FOMO_REMAIN < CREDIT_LOW )); }; then
    # Low credits: back off to 120–300s (at least BASE_POLL, at least 120)
    local backed=$BASE_POLL
    if (( backed < 120 )); then backed=120; fi
    if (( backed < 180 )); then backed=180; fi
    if (( backed > 300 )); then backed=300; fi
    # If base was already >=120, use max(base, 180) capped 300
    if (( BASE_POLL >= 120 && BASE_POLL <= 300 )); then
      backed=$BASE_POLL
      if (( FOMO_REMAIN < 2000 && backed < 300 )); then backed=300; fi
    fi
    POLL=$backed
    log "FOMO credits low remain=${FOMO_REMAIN} (<${CREDIT_LOW}) — poll back-off to ${POLL}s"
  else
    POLL=$BASE_POLL
  fi
  sleep_for=$((POLL - elapsed))
  if (( sleep_for < 3 )); then sleep_for=3; fi
  echo "$sleep_for"
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
FOMO_REMAIN="unknown"
FOMO_STATUS="ok"
# Seed from recent log so we do not hammer FOMO when already dry
if [[ -f "$LOG" ]]; then
  if rg -q 'fomo alerts HTTP 402|fomo err=credits|remain=0' "$LOG" 2>/dev/null; then
    # only if the very latest credit line is dry / 402
    last_credit=$(rg -n 'remain=[0-9]+|fomo alerts HTTP 402|fomo err=credits' "$LOG" 2>/dev/null | tail -1 || true)
    if echo "$last_credit" | rg -q 'HTTP 402|err=credits|remain=0'; then
      FOMO_REMAIN="0"
      FOMO_STATUS="credits"
      POLL="${FOMO_DRY_SLEEP_SEC:-2700}"
      if (( POLL < 1800 )); then POLL=1800; fi
      if (( POLL > 3600 )); then POLL=3600; fi
      export FOMO_POLL_SECONDS="$POLL"
      log "seeded FOMO dry from log — initial poll=${POLL}s (skip hammer)"
    fi
  fi
fi
log "signal_tick start poll=${POLL}s once=${ONCE} fomo=${FOMO_ENABLED} fomo_key=${FOMO_OK} gmgn=off state=$STATE_PATH sync=$DO_SYNC lock=$LOCK"

run_once() {
  local t0 t1 elapsed rc capture
  t0=$(date +%s)
  capture=$(mktemp "$STATE_DIR/tick_capture.XXXXXX")
  log "tick begin"
  if [[ "$DO_SYNC" == "1" ]]; then
    ROOT="$ROOT" STATE_PATH="$STATE_PATH" bash "$ROOT/scripts/signal_state_sync.sh" pull >>"$LOG" 2>&1 || true
  fi
  # Keep FOMO_POLL in sync with adaptive POLL so bot.py does not skip forever / over-call
  export FOMO_POLL_SECONDS="$POLL"
  set +e
  timeout "${SIGNAL_TICK_TIMEOUT:-180}" python3 "$ROOT/bot.py" --per-page 100 >"$capture" 2>&1
  rc=$?
  set +e
  cat "$capture" >>"$LOG"
  parse_fomo_status "$capture"
  rm -f "$capture"
  if [[ "$DO_SYNC" == "1" ]]; then
    ROOT="$ROOT" STATE_PATH="$STATE_PATH" bash "$ROOT/scripts/signal_state_sync.sh" push >>"$LOG" 2>&1 || true
  fi
  t1=$(date +%s)
  elapsed=$((t1 - t0))
  log "tick end rc=$rc elapsed=${elapsed}s fomo_remain=${FOMO_REMAIN} fomo_status=${FOMO_STATUS} next_poll=${POLL}s"
  patch_health "$rc" "$elapsed" "$FOMO_OK" "$FOMO_REMAIN" "$POLL" "$FOMO_STATUS"
  LAST_ELAPSED=$elapsed
  return 0
}

if [[ "$ONCE" == "1" ]]; then
  run_once
  exit 0
fi

LAST_ELAPSED=0
# If we already know credits are dry, sleep first (do not burn another 402)
if [[ "$FOMO_STATUS" == "credits" ]]; then
  sleep_for=$(next_sleep_sec 0)
  sleep "$sleep_for"
fi
while true; do
  run_once
  sleep_for=$(next_sleep_sec "$LAST_ELAPSED")
  sleep "$sleep_for"
done

#!/usr/bin/env bash
# Box-resident RH signal tick — primary Discord notify path.
# Default SIGNAL_SOURCE=onchain (free public RPC). FOMO optional when credits left.
# GMGN stays OFF on box (GHA owns GMGN). Shared state via release tag signal-state.
#
# Env knobs:
#   SIGNAL_SOURCE         onchain|fomo|both  (default onchain)
#   ONCHAIN_POLL_SECONDS  onchain loop sleep (default 5)
#   SIGNAL_POLL_SECONDS   FOMO bot.py interval when source includes fomo (default 300)
#   SIGNAL_TICK_ONCE=1    run one tick and exit
#   SIGNAL_STATE_SYNC=0   disable release pull/push
#   FOMO_POLL_SECONDS     defaults to SIGNAL_POLL_SECONDS
#   FOMO_CREDIT_LOW=5000  remain below this → back off FOMO poll to 120–300s
#   FOMO_DRY_SLEEP_SEC    FOMO-only cadence when dry (onchain keeps ticking)
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
SIGNAL_SOURCE="${SIGNAL_SOURCE:-onchain}"
export SIGNAL_SOURCE
ONCHAIN_POLL="${ONCHAIN_POLL_SECONDS:-5}"
ONCHAIN_PY="$ROOT/scripts/onchain_signal_tick.py"
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
src = __import__("os").environ.get("SIGNAL_SOURCE", "onchain")
data["signal_tick"] = {
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "rc": rc_v,
    "elapsed_sec": el_v,
    "poll_sec": int(float(poll)),
    "fomo_key": fomo_ok == "1",
    "fomo_remain": rem_v,
    "fomo_status": fomo_status,
    "gmgn": "disabled",
    "signal_source": src,
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
  log "WARN FOMO_API_KEY missing — FOMO off (onchain still runs)"
  export FOMO_ENABLED=0
fi
case "$SIGNAL_SOURCE" in
  onchain|on-chain)
    export FOMO_ENABLED=0
    log "SIGNAL_SOURCE=onchain — FOMO disabled (free RPC path)"
    ;;
  fomo)
    if [[ "$FOMO_OK" != "1" ]]; then
      log "SIGNAL_SOURCE=fomo but no key — falling back to onchain"
      SIGNAL_SOURCE=onchain
      export FOMO_ENABLED=0
    fi
    ;;
  both)
    :
    ;;
  *)
    log "unknown SIGNAL_SOURCE=$SIGNAL_SOURCE — defaulting to onchain"
    SIGNAL_SOURCE=onchain
    export FOMO_ENABLED=0
    ;;
esac
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
      # Keep FOMO cadence long, but do not starve onchain loop
      FOMO_POLL_DRY="${FOMO_DRY_SLEEP_SEC:-2700}"
      if (( FOMO_POLL_DRY < 1800 )); then FOMO_POLL_DRY=1800; fi
      if (( FOMO_POLL_DRY > 3600 )); then FOMO_POLL_DRY=3600; fi
      POLL="$FOMO_POLL_DRY"
      export FOMO_POLL_SECONDS="$POLL"
      log "seeded FOMO dry from log — FOMO poll=${POLL}s (onchain keeps ticking)"
    fi
  fi
fi
log "signal_tick start source=${SIGNAL_SOURCE} onchain_poll=${ONCHAIN_POLL}s fomo_poll=${POLL}s once=${ONCE} fomo=${FOMO_ENABLED} fomo_key=${FOMO_OK} gmgn=off state=$STATE_PATH sync=$DO_SYNC lock=$LOCK"

source_wants_onchain() {
  case "$SIGNAL_SOURCE" in
    onchain|both|on-chain) return 0 ;;
    *) return 1 ;;
  esac
}
source_wants_fomo() {
  case "$SIGNAL_SOURCE" in
    fomo|both) return 0 ;;
    *) return 1 ;;
  esac
}

# If FOMO dry / missing key, force onchain-only even when SIGNAL_SOURCE=both
resolve_effective_source() {
  if source_wants_fomo; then
    if [[ "$FOMO_OK" != "1" ]]; then
      EFFECTIVE_SOURCE="onchain"
      return
    fi
    if [[ "$FOMO_STATUS" == "credits" || "$FOMO_REMAIN" == "0" ]]; then
      EFFECTIVE_SOURCE="onchain"
      return
    fi
  fi
  EFFECTIVE_SOURCE="$SIGNAL_SOURCE"
  if ! source_wants_onchain && ! source_wants_fomo; then
    EFFECTIVE_SOURCE="onchain"
  fi
}

run_onchain_once() {
  local t0 t1 elapsed rc
  if [[ ! -f "$ONCHAIN_PY" ]]; then
    log "onchain_signal_tick.py missing"
    return 1
  fi
  t0=$(date +%s)
  log "onchain tick begin"
  set +e
  ONCHAIN_TICK_ONCE=1 \
  LIVE_TRADING=0 \
  GMGN_DISABLED=1 \
  GMGN_SMARTMONEY=0 \
  FOMO_ENABLED=0 \
  NOTIFY_PASSTHROUGH=1 \
  RH_NOTIFY_ALWAYS=1 \
  PAPER_TRADING=0 \
  CHAIN=robinhood \
  WATCHLIST_PATH="${WATCHLIST_PATH}" \
  STATE_PATH="${STATE_PATH}" \
  SIGNAL_TICK_HEALTH="$HEALTH" \
  COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-900}" \
  MIN_WALLETS="${MIN_WALLETS:-1}" \
  DROP_WEAK_WALLETS=0 \
  DROP_BOT_WALLETS=0 \
  PRIORITY_NOTIFY="${PRIORITY_NOTIFY:-1}" \
  LIQ_REQUIRED=0 \
  ANTI_SPIKE_REQUIRED=0 \
  RH_SOFT_GATES=1 \
  REQUIRE_PRICE_MOVE=0 \
  BUY_VOLUME_REQUIRED=0 \
  VOLUME_REQUIRED=0 \
  VOLUME_M5_REQUIRED=0 \
  REQUIRE_BUY_INCREASE=0 \
  PLAYBOOK_REQUIRED=0 \
  POST_SKIP_NOTICES=0 \
  MIN_CLUSTER_USD="${MIN_CLUSTER_USD:-80}" \
  MIN_MCAP_USD="${MIN_MCAP_USD:-1500}" \
  MIN_TRADE_USD="${MIN_TRADE_USD:-40}" \
  WINDOW_SECONDS="${WINDOW_SECONDS:-1200}" \
  DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}" \
  DISCORD_PRIORITY_WEBHOOK_URL="${DISCORD_PRIORITY_WEBHOOK_URL:-}" \
    timeout "${ONCHAIN_TICK_TIMEOUT:-120}" python3 "$ONCHAIN_PY" --once >>"$LOG" 2>&1
  rc=$?
  set +e
  t1=$(date +%s)
  elapsed=$((t1 - t0))
  log "onchain tick end rc=$rc elapsed=${elapsed}s"
  LAST_ONCHAIN_ELAPSED=$elapsed
  return $rc
}

run_fomo_once() {
  local t0 t1 elapsed rc capture
  t0=$(date +%s)
  capture=$(mktemp "$STATE_DIR/tick_capture.XXXXXX")
  log "fomo tick begin"
  if [[ "$DO_SYNC" == "1" ]]; then
    ROOT="$ROOT" STATE_PATH="$STATE_PATH" bash "$ROOT/scripts/signal_state_sync.sh" pull >>"$LOG" 2>&1 || true
  fi
  export FOMO_POLL_SECONDS="$POLL"
  export FOMO_ENABLED=1
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
  log "fomo tick end rc=$rc elapsed=${elapsed}s fomo_remain=${FOMO_REMAIN} fomo_status=${FOMO_STATUS} next_fomo_poll=${POLL}s"
  patch_health "$rc" "$elapsed" "$FOMO_OK" "$FOMO_REMAIN" "$POLL" "$FOMO_STATUS"
  LAST_FOMO_ELAPSED=$elapsed
  LAST_FOMO_TS=$(date +%s)
  return 0
}

run_once() {
  resolve_effective_source
  log "tick begin source=${SIGNAL_SOURCE} effective=${EFFECTIVE_SOURCE} fomo_status=${FOMO_STATUS} remain=${FOMO_REMAIN}"
  local rc=0
  case "$EFFECTIVE_SOURCE" in
    onchain|on-chain)
      run_onchain_once || rc=$?
      patch_health "$rc" "${LAST_ONCHAIN_ELAPSED:-0}" "$FOMO_OK" "$FOMO_REMAIN" "$ONCHAIN_POLL" "onchain_primary"
      ;;
    fomo)
      run_fomo_once || rc=$?
      ;;
    both)
      run_onchain_once || true
      now=$(date +%s)
      due=1
      if [[ -n "${LAST_FOMO_TS:-}" && "${LAST_FOMO_TS:-0}" -gt 0 ]]; then
        elapsed_since=$((now - LAST_FOMO_TS))
        if (( elapsed_since < POLL )); then
          due=0
        fi
      fi
      if [[ "$FOMO_STATUS" == "credits" || "$FOMO_REMAIN" == "0" ]]; then
        due=0
        log "skip FOMO (credits dry) — onchain-only this cycle"
      fi
      if [[ "$due" == "1" && "$FOMO_OK" == "1" ]]; then
        run_fomo_once || true
      fi
      patch_health "0" "${LAST_ONCHAIN_ELAPSED:-0}" "$FOMO_OK" "$FOMO_REMAIN" "$ONCHAIN_POLL" "${FOMO_STATUS}"
      ;;
    *)
      run_onchain_once || rc=$?
      ;;
  esac
  LAST_ELAPSED=${LAST_ONCHAIN_ELAPSED:-${LAST_FOMO_ELAPSED:-0}}
  return 0
}

if [[ "$ONCE" == "1" ]]; then
  run_once
  exit 0
fi

LAST_ELAPSED=0
LAST_ONCHAIN_ELAPSED=0
LAST_FOMO_ELAPSED=0
LAST_FOMO_TS=0
resolve_effective_source
log "loop start source=${SIGNAL_SOURCE} effective=${EFFECTIVE_SOURCE} onchain_poll=${ONCHAIN_POLL}s fomo_poll=${POLL}s"
while true; do
  run_once
  resolve_effective_source
  if [[ "$EFFECTIVE_SOURCE" == "fomo" ]]; then
    sleep_for=$(next_sleep_sec "$LAST_ELAPSED")
  else
    # onchain / both: tight loop; FOMO cadence handled inside run_once
    el=${LAST_ONCHAIN_ELAPSED:-0}
    sleep_for=$((ONCHAIN_POLL - el))
    if (( sleep_for < 1 )); then sleep_for=1; fi
  fi
  sleep "$sleep_for"
done

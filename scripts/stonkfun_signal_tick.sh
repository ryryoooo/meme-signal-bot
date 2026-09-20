#!/usr/bin/env bash
# Dedicated StonkFun digger → Discord tick (Solana).
# Posts ONLY to DISCORD_STONKFUN_WEBHOOK_URL — never DISCORD_WEBHOOK_URL (RH).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${STONKFUN_TICK_STATE_DIR:-/home/box/.local/share/scout-wallet-bot}"
LOG="${STONKFUN_TICK_LOG:-$STATE_DIR/stonkfun_signal_tick.log}"
LOCK="${STONKFUN_TICK_LOCK:-$STATE_DIR/stonkfun_signal_tick.lock}"
PIDFILE="${STONKFUN_TICK_PID:-$STATE_DIR/stonkfun_signal_tick.pid}"
PY="$ROOT/scripts/stonkfun_signal_tick.py"
mkdir -p "$STATE_DIR"
cd "$ROOT"

if [[ -f "$PIDFILE" ]]; then
  oldpid=$(cat "$PIDFILE" 2>/dev/null || echo "")
  if [[ "$oldpid" =~ ^[0-9]+$ ]] && ! kill -0 "$oldpid" 2>/dev/null; then
    fuser -k "$LOCK" >/dev/null 2>&1 || true
    sleep 0.2
  fi
fi
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) stonkfun_signal_tick already running — exit" >>"$LOG"
  exit 0
fi
echo $$ >"$PIDFILE"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

# Load dedicated webhook only (never export RH main into this process for posting)
eval "$(python3 - <<'PY'
from load_secrets import load
import os, shlex
keys = ["DISCORD_STONKFUN_WEBHOOK_URL"]
st = load(keys)
for k in keys:
    v = (os.environ.get(k) or "").strip()
    if v:
        print(f"export {k}={shlex.quote(v)}")
print(f"export _STONKFUN_WEBHOOK_OK={'1' if st.get('DISCORD_STONKFUN_WEBHOOK_URL') else '0'}")
PY
)"
WEBHOOK_OK="${_STONKFUN_WEBHOOK_OK:-0}"

export LIVE_TRADING=0
export GMGN_DISABLED=1
export GMGN_SMARTMONEY=0
export GMGN_MARKET=0
export STONKFUN_POLL_SECONDS="${STONKFUN_POLL_SECONDS:-20}"
export STONKFUN_MIN_DIGGERS="${STONKFUN_MIN_DIGGERS:-1}"
export SOLANA_RPC_URL="${SOLANA_RPC_URL:-https://solana-rpc.publicnode.com}"

if [[ "$WEBHOOK_OK" != "1" ]]; then
  log "WARN DISCORD_STONKFUN_WEBHOOK_URL missing — tick runs but posts skipped (no RH fallback)"
else
  log "webhook=dedicated_ok (DISCORD_STONKFUN_WEBHOOK_URL)"
fi

if [[ ! -f "$PY" ]]; then
  log "missing $PY"
  exit 1
fi

ONCE="${STONKFUN_TICK_ONCE:-0}"
TEST="${STONKFUN_SIGNAL_TEST:-0}"
ARGS=()
[[ "$ONCE" == "1" ]] && ARGS+=(--once)
[[ "$TEST" == "1" ]] && ARGS+=(--test)

log "start poll=${STONKFUN_POLL_SECONDS}s once=$ONCE test=$TEST"
# flock FD 9 held across exec
if [[ "$ONCE" == "1" ]]; then
  python3 "$PY" "${ARGS[@]}" >>"$LOG" 2>&1
  rc=$?
  log "once end rc=$rc"
  exit $rc
fi
exec python3 "$PY" "${ARGS[@]}" >>"$LOG" 2>&1

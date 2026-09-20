#!/usr/bin/env bash
# Solana dual-profile paper trading ($100 × 4 books: 攻撃+安定 × StonkFun/Sol smart).
# LIVE_TRADING=0 always. 【紙実況】 ONLY to DISCORD_SOL_PAPER_WEBHOOK_URL.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${SOL_PAPER_STATE_DIR:-/home/box/.local/share/scout-wallet-bot}"
LOG="${SOL_PAPER_TICK_LOG:-$STATE_DIR/sol_paper_tick.log}"
LOCK="${SOL_PAPER_TICK_LOCK:-$STATE_DIR/sol_paper_tick.lock}"
PIDFILE="${SOL_PAPER_TICK_PID:-$STATE_DIR/sol_paper_tick.pid}"
PY="$ROOT/scripts/sol_paper_tick.py"
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
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sol_paper_tick already running — exit" >>"$LOG"
  exit 0
fi
echo $$ >"$PIDFILE"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

eval "$(python3 - <<'PY'
from load_secrets import load
import os, shlex
keys = ["DISCORD_SOL_PAPER_WEBHOOK_URL"]
st = load(keys)
for k in keys:
    v = (os.environ.get(k) or "").strip()
    if v:
        print(f"export {k}={shlex.quote(v)}")
print(f"export _PAPER_WH_OK={'1' if st.get('DISCORD_SOL_PAPER_WEBHOOK_URL') else '0'}")
PY
)"

export LIVE_TRADING=0
export GMGN_DISABLED=1
export GMGN_SMARTMONEY=0
export GMGN_MARKET=0
export PAPER_MARK_HEARTBEAT="${PAPER_MARK_HEARTBEAT:-0}"
export SOL_PAPER_TICK_SEC="${SOL_PAPER_TICK_SEC:-90}"
export SOL_PAPER_BANKROLL_USD="${SOL_PAPER_BANKROLL_USD:-100}"
export SOL_PAPER_DISCORD="${SOL_PAPER_DISCORD:-1}"
export SOL_PAPER_JIKEI_SEC="${SOL_PAPER_JIKEI_SEC:-1200}"
export SOL_PAPER_SEED_ON_START="${SOL_PAPER_SEED_ON_START:-1}"
# Profile knobs (攻撃/安定) are applied per-book inside sol_paper_tick.py — do not pin here.
export SOL_PAPER_STATE_DIR="$STATE_DIR"
export SOL_PAPER_TICK_LOG="$LOG"

log "webhook_sol_paper=${_PAPER_WH_OK:-0} bankroll=${SOL_PAPER_BANKROLL_USD} tick=${SOL_PAPER_TICK_SEC}s jikkei=${SOL_PAPER_JIKEI_SEC}s dual_profile=1"

if [[ ! -f "$PY" ]]; then
  log "missing $PY"
  exit 1
fi

ONCE="${SOL_PAPER_TICK_ONCE:-0}"
SEED="${SOL_PAPER_SEED:-0}"
ANNOUNCE="${SOL_PAPER_ANNOUNCE:-0}"
ARGS=()
[[ "$ONCE" == "1" ]] && ARGS+=(--once)
[[ "$SEED" == "1" ]] && ARGS+=(--seed)
[[ "$ANNOUNCE" == "1" ]] && ARGS+=(--announce)

log "start once=$ONCE seed=$SEED announce=$ANNOUNCE LIVE_TRADING=0 books=4"
if [[ "$ONCE" == "1" ]]; then
  python3 "$PY" "${ARGS[@]}" >>"$LOG" 2>&1
  rc=$?
  log "once end rc=$rc"
  exit $rc
fi
exec python3 "$PY" "${ARGS[@]}" >>"$LOG" 2>&1

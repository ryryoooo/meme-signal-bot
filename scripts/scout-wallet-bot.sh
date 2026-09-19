#!/usr/bin/env bash
# Zero-Super dispatcher: Scout TG GHA + TheMaran free + GMGN throttle on GHA + paper daily
# Aggressive continuous resolve — deep hourly, light every 10m when idle.
set -u
REPO="ryryoooo/meme-signal-bot"
WF="scout-tg-resolve.yml"
VET_WF="gmgn-vet-throttle.yml"
PAPER_WF="paper-daily.yml"
STATE="/home/box/.local/share/scout-wallet-bot"
ROOT="/workspace/meme-foundation/discord-bot"
LOG="$STATE/bot.log"
HEALTH="$STATE/health.json"
DEEP_EVERY_SEC="${DEEP_EVERY_SEC:-3600}"
LIGHT_EVERY_SEC="${LIGHT_EVERY_SEC:-600}"
THEMARAN_EVERY_SEC="${THEMARAN_EVERY_SEC:-1800}"
GMGN_VET_EVERY_SEC="${GMGN_VET_EVERY_SEC:-10800}"
PAPER_DAILY_EVERY_SEC="${PAPER_DAILY_EVERY_SEC:-86400}"
POLL_SEC="${POLL_SEC:-120}"
mkdir -p "$STATE"
exec 9>"$STATE/daemon.lock"
flock -n 9 || exit 0
log() {
  if [[ -f "$LOG" ]] && [[ $(stat -c%s "$LOG" 2>/dev/null || echo 0) -gt 1048576 ]]; then
    mv "$LOG" "$LOG.1"
  fi
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"
}
now=$(date +%s)
[[ -f "$STATE/last_deep" ]] || echo "$now" > "$STATE/last_deep"
[[ -f "$STATE/last_light" ]] || echo "$now" > "$STATE/last_light"
[[ -f "$STATE/last_themaran" ]] || echo 0 > "$STATE/last_themaran"
[[ -f "$STATE/last_gmgn_vet" ]] || echo 0 > "$STATE/last_gmgn_vet"
[[ -f "$STATE/last_paper_daily" ]] || echo 0 > "$STATE/last_paper_daily"
# RH FOMO fast notify (~20s) — primary Discord path; GMGN stays on GHA
ensure_signal_tick() {
  local tick="$ROOT/scripts/signal_tick.sh"
  local pidfile="$STATE/signal_tick.pid"
  local tick_log="$STATE/signal_tick.log"
  if [[ ! -x "$tick" ]]; then
    log "signal_tick missing: $tick"
    return 0
  fi
  if [[ -f "$pidfile" ]]; then
    local old
    old=$(cat "$pidfile" 2>/dev/null || echo "")
    if [[ "$old" =~ ^[0-9]+$ ]] && kill -0 "$old" 2>/dev/null; then
      return 0
    fi
  fi
  if pgrep -f 'scripts/signal_tick.sh' >/dev/null 2>&1; then
    return 0
  fi
  SIGNAL_POLL_SECONDS="${SIGNAL_POLL_SECONDS:-20}" \
  SIGNAL_STATE_SYNC="${SIGNAL_STATE_SYNC:-1}" \
  SIGNAL_TICK_STATE_DIR="$STATE" \
  SIGNAL_TICK_LOG="$tick_log" \
  SIGNAL_TICK_HEALTH="$HEALTH" \
    nohup bash "$tick" >>"$tick_log" 2>&1 &
  echo $! > "$pidfile"
  log "signal_tick started pid=$! poll=${SIGNAL_POLL_SECONDS:-20}s"
}

ensure_signal_tick
log "started pid=$$ deep=${DEEP_EVERY_SEC}s light=${LIGHT_EVERY_SEC}s themaran=${THEMARAN_EVERY_SEC}s gmgn_vet=${GMGN_VET_EVERY_SEC}s paper_daily=${PAPER_DAILY_EVERY_SEC}s"
while true; do
  now=$(date +%s)
  action="idle"; result="ok"
  last_tm=$(cat "$STATE/last_themaran" 2>/dev/null || echo 0)
  if (( now - last_tm >= THEMARAN_EVERY_SEC )); then
    action="themaran_local"
    if ( cd "$ROOT" && THEMARAN_PROFILE_CAP="${THEMARAN_PROFILE_CAP:-25}" THEMARAN_MERGE_WATCH=1 timeout 180 python3 scripts/harvest_themaran_stack.py ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_themaran"; log "themaran ok"
    else
      result="themaran_failed"; log "themaran failed"
    fi
  fi
  # Paper daily — prefer free box run; also dispatch GHA for cache+webhook+artifact
  last_pd=$(cat "$STATE/last_paper_daily" 2>/dev/null || echo 0)
  if (( now - last_pd >= PAPER_DAILY_EVERY_SEC )); then
    action="paper_daily"
    if ( cd "$ROOT" && LIVE_TRADING=0 PAPER_DAILY_POST_DISCORD=0 timeout 60 python3 scripts/paper_daily_review.py ) >> "$LOG" 2>&1; then
      log "paper_daily local ok"
    else
      log "paper_daily local fail (ok if empty log)"
    fi
    if timeout 120 gh workflow run "$PAPER_WF" --repo "$REPO" -f post_discord=1 >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_paper_daily"; log "paper_daily gha dispatched"
    else
      # still advance local stamp lightly so we retry next day window
      echo "$now" > "$STATE/last_paper_daily"
      result="paper_daily_dispatch_failed"; log "paper_daily gha dispatch failed"
    fi
  fi
  # GMGN only on GHA, tiny — never on box. Skip while resolve busy or post-429 cool.
  last_gv=$(cat "$STATE/last_gmgn_vet" 2>/dev/null || echo 0)
  gmgn_active=$(gh run list --repo "$REPO" --workflow "$VET_WF" --limit 5 --json status --jq '[.[]|select(.status=="in_progress" or .status=="queued")]|length' 2>/dev/null || echo -1)
  resolve_active=$(gh run list --repo "$REPO" --workflow "$WF" --limit 5 --json status --jq '[.[]|select(.status=="in_progress" or .status=="queued")]|length' 2>/dev/null || echo -1)
  cool_block=0
  if [[ -f "$ROOT/rh-wallets/raw/gmgn_vet_throttle_state.json" ]]; then
    cool_block=$(python3 - <<'PY' 2>/dev/null || echo 0
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
p=Path("/workspace/meme-foundation/discord-bot/rh-wallets/raw/gmgn_vet_throttle_state.json")
try:
    st=json.loads(p.read_text())
except Exception:
    print(0); raise SystemExit
s=st.get("last_rate_limit_at")
if not s:
    print(0); raise SystemExit
try:
    t=datetime.fromisoformat(s.replace("Z","+00:00"))
except Exception:
    print(0); raise SystemExit
print(1 if datetime.now(timezone.utc)-t < timedelta(hours=6) else 0)
PY
)
  fi
  if [[ "$gmgn_active" == "0" && "$resolve_active" == "0" && "$cool_block" == "0" ]] && (( now - last_gv >= GMGN_VET_EVERY_SEC )); then
    action="dispatch_gmgn_vet"
    if timeout 120 gh workflow run "$VET_WF" --repo "$REPO" -f cap=5 -f sleep_sec=20 -f priority=elite >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_gmgn_vet"; log "gmgn vet dispatched (elite/pending_WR)"
    else
      result="gmgn_vet_dispatch_failed"; log "gmgn vet dispatch failed"
    fi
  elif [[ "$cool_block" == "1" ]] && (( now - last_gv >= GMGN_VET_EVERY_SEC )); then
    [[ "$action" == "idle" ]] && action="gmgn_cool_429"
  fi
  active=$(gh run list --repo "$REPO" --workflow "$WF" --limit 20 --json status --jq '[.[]|select(.status=="in_progress" or .status=="queued" or .status=="waiting" or .status=="pending" or .status=="requested")]|length' 2>/dev/null || echo -1)
  last_deep=$(cat "$STATE/last_deep" 2>/dev/null || echo 0)
  last_light=$(cat "$STATE/last_light" 2>/dev/null || echo 0)
  # Keep ≤1 in_progress + ≤1 pending (active covers queued/in_progress/waiting)
  if [[ "$active" =~ ^[0-9]+$ ]] && (( active >= 2 )); then
    [[ "$action" == "idle" ]] && action="skip_active"
  elif [[ "$active" =~ ^[0-9]+$ ]] && (( active <= 1 )); then
    if (( now - last_deep >= DEEP_EVERY_SEC )); then
      action="dispatch_deep"
      # pages=1 scrape + chunked BS; vet_cap=0 (GMGN portfolio elsewhere)
      if timeout 120 gh workflow run "$WF" --repo "$REPO" -f pages=1 -f bs_pages=50 -f vet_cap=0 -f max_tokens=0 -f bs_all=1 -f bs_token_cap=80 -f time_budget=1500 >> "$LOG" 2>&1; then
        echo "$now" > "$STATE/last_deep"; echo "$now" > "$STATE/last_light"; log "deep dispatched"
      else
        result="dispatch_failed"; log "deep dispatch failed"
      fi
    elif (( now - last_light >= LIGHT_EVERY_SEC )); then
      action="dispatch_light"
      # pages=0 resolve-only; chunked BS via bs_all=1 + bs_token_cap
      if timeout 120 gh workflow run "$WF" --repo "$REPO" -f pages=0 -f bs_pages=30 -f vet_cap=0 -f max_tokens=0 -f bs_all=1 -f bs_token_cap=80 -f time_budget=1200 >> "$LOG" 2>&1; then
        echo "$now" > "$STATE/last_light"; log "light dispatched"
      else
        result="dispatch_failed"; log "light dispatch failed"
      fi
    fi
  else
    [[ "$action" == "idle" ]] && action="github_check_failed"
    result="retry_later"
  fi
  latest=$(gh run list --repo "$REPO" --workflow "$WF" --limit 1 --json databaseId,status,conclusion,createdAt --jq '.[0] // {}' 2>/dev/null || echo '{}')
  python3 - "$HEALTH" "$$" "${active:--1}" "$action" "$result" "$latest" <<'PYH'
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
pid, active, action, result, latest_raw = sys.argv[2:7]
prev = {}
if path.exists():
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
try:
    latest = json.loads(latest_raw) if latest_raw else {}
except Exception:
    latest = {}
out = {
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "pid": int(pid),
    "active": int(active) if str(active).lstrip("-").isdigit() else active,
    "action": action,
    "result": result,
    "latest": latest,
}
if isinstance(prev.get("signal_tick"), dict):
    out["signal_tick"] = prev["signal_tick"]
nl = chr(10)
path.write_text(json.dumps(out, ensure_ascii=False) + nl, encoding="utf-8")
PYH
  ensure_signal_tick
  sleep "$POLL_SEC"
done

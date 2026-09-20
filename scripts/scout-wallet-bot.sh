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
AUDIT_EVERY_SEC="${AUDIT_EVERY_SEC:-21600}"
HUNT_EVERY_SEC="${HUNT_EVERY_SEC:-900}"
TREND_HUNT_EVERY_SEC="${TREND_HUNT_EVERY_SEC:-900}"
PNL_FILL_EVERY_SEC="${PNL_FILL_EVERY_SEC:-900}"
STONKFUN_EVERY_SEC="${STONKFUN_EVERY_SEC:-1800}"
SOL_SMART_EVERY_SEC="${SOL_SMART_EVERY_SEC:-1800}"
SOL7D_EVERY_SEC="${SOL7D_EVERY_SEC:-3600}"
DUMPDIP_EVERY_SEC="${DUMPDIP_EVERY_SEC:-3600}"
AUDIT_WF="wallet-audit.yml"
POLL_SEC="${POLL_SEC:-120}"
mkdir -p "$STATE"
# If prior daemon bash was kill -9'd, orphan sleep may still hold the lock FD
if [[ -f "$STATE/bot.pid" ]]; then
  oldpid=$(cat "$STATE/bot.pid" 2>/dev/null || echo "")
  if [[ "$oldpid" =~ ^[0-9]+$ ]] && ! kill -0 "$oldpid" 2>/dev/null; then
    fuser -k "$STATE/daemon.lock" >/dev/null 2>&1 || true
    sleep 0.2
  fi
fi
exec 9>"$STATE/daemon.lock"
flock -n 9 || exit 0
echo $$ > "$STATE/bot.pid"
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
[[ -f "$STATE/last_wallet_audit" ]] || echo 0 > "$STATE/last_wallet_audit"
[[ -f "$STATE/last_onchain_hunt" ]] || echo 0 > "$STATE/last_onchain_hunt"
[[ -f "$STATE/last_trend_hunt" ]] || echo 0 > "$STATE/last_trend_hunt"
[[ -f "$STATE/last_pnl_fill" ]] || echo 0 > "$STATE/last_pnl_fill"
[[ -f "$STATE/last_stonkfun" ]] || echo 0 > "$STATE/last_stonkfun"
[[ -f "$STATE/last_sol_smart" ]] || echo 0 > "$STATE/last_sol_smart"
[[ -f "$STATE/last_sol7d" ]] || echo 0 > "$STATE/last_sol7d"
[[ -f "$STATE/last_dumpdip" ]] || echo 0 > "$STATE/last_dumpdip"
# RH notify: default SIGNAL_SOURCE=onchain (free RPC). FOMO optional; GMGN stays on GHA
ensure_signal_tick() {
  local tick="$ROOT/scripts/signal_tick.sh"
  local pidfile="$STATE/signal_tick.pid"
  local tick_log="$STATE/signal_tick.log"
  local lock="$STATE/signal_tick.lock"
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
    rm -f "$pidfile"
  fi
  # flock inside signal_tick.sh is authoritative; also skip if any tick bash is alive
  if pgrep -f '/scripts/signal_tick\.sh' >/dev/null 2>&1; then
    return 0
  fi
  SIGNAL_SOURCE="${SIGNAL_SOURCE:-onchain}" \
  ONCHAIN_POLL_SECONDS="${ONCHAIN_POLL_SECONDS:-2}" \
  SIGNAL_POLL_SECONDS="${SIGNAL_POLL_SECONDS:-300}" \
  SIGNAL_STATE_SYNC="${SIGNAL_STATE_SYNC:-1}" \
  SIGNAL_TICK_STATE_DIR="$STATE" \
  SIGNAL_TICK_LOG="$tick_log" \
  SIGNAL_TICK_HEALTH="$HEALTH" \
  SIGNAL_TICK_LOCK="$lock" \
  STATE_PATH="$ROOT/state.json" \
  LIVE_TRADING=0 \
  GMGN_DISABLED=1 \
    nohup bash "$tick" >>"$tick_log" 2>&1 &
  echo $! > "$pidfile"
  log "signal_tick started pid=$! source=${SIGNAL_SOURCE:-onchain} onchain_poll=${ONCHAIN_POLL_SECONDS:-2}s fomo_poll=${SIGNAL_POLL_SECONDS:-300}s"
}

ensure_signal_tick

# StonkFun digger Discord (dedicated channel; never RH DISCORD_WEBHOOK_URL)
ensure_stonkfun_signal_tick() {
  local tick="$ROOT/scripts/stonkfun_signal_tick.sh"
  local pidfile="$STATE/stonkfun_signal_tick.pid"
  local tick_log="$STATE/stonkfun_signal_tick.log"
  local lock="$STATE/stonkfun_signal_tick.lock"
  if [[ ! -x "$tick" ]]; then
    log "stonkfun_signal_tick missing: $tick"
    return 0
  fi
  if [[ -f "$pidfile" ]]; then
    local old
    old=$(cat "$pidfile" 2>/dev/null || echo "")
    if [[ "$old" =~ ^[0-9]+$ ]] && kill -0 "$old" 2>/dev/null; then
      return 0
    fi
    rm -f "$pidfile"
  fi
  if pgrep -f '/scripts/stonkfun_signal_tick\.sh' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'stonkfun_signal_tick\.py' >/dev/null 2>&1; then
    return 0
  fi
  STONKFUN_POLL_SECONDS="${STONKFUN_POLL_SECONDS:-20}" \
  STONKFUN_TICK_STATE_DIR="$STATE" \
  STONKFUN_TICK_LOG="$tick_log" \
  STONKFUN_TICK_LOCK="$lock" \
  STONKFUN_TICK_PID="$pidfile" \
  LIVE_TRADING=0 \
  GMGN_DISABLED=1 \
    nohup bash "$tick" >>"$tick_log" 2>&1 &
  echo $! > "$pidfile"
  log "stonkfun_signal_tick started pid=$! poll=${STONKFUN_POLL_SECONDS:-20}s webhook=dedicated_only"
}

ensure_stonkfun_signal_tick

# Solana smart wallets Discord (dedicated channel; never RH / StonkFun webhooks)
ensure_sol_smart_signal_tick() {
  local tick="$ROOT/scripts/sol_smart_signal_tick.sh"
  local pidfile="$STATE/sol_smart_signal_tick.pid"
  local tick_log="$STATE/sol_smart_signal_tick.log"
  local lock="$STATE/sol_smart_signal_tick.lock"
  if [[ ! -x "$tick" ]]; then
    log "sol_smart_signal_tick missing: $tick"
    return 0
  fi
  if [[ -f "$pidfile" ]]; then
    local old
    old=$(cat "$pidfile" 2>/dev/null || echo "")
    if [[ "$old" =~ ^[0-9]+$ ]] && kill -0 "$old" 2>/dev/null; then
      return 0
    fi
    rm -f "$pidfile"
  fi
  if pgrep -f '/scripts/sol_smart_signal_tick\.sh' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'sol_smart_signal_tick\.py' >/dev/null 2>&1; then
    return 0
  fi
  SOL_SMART_POLL_SECONDS="${SOL_SMART_POLL_SECONDS:-30}" \
  SOL_SMART_TICK_STATE_DIR="$STATE" \
  SOL_SMART_TICK_LOG="$tick_log" \
  SOL_SMART_TICK_LOCK="$lock" \
  SOL_SMART_TICK_PID="$pidfile" \
  LIVE_TRADING=0 \
  GMGN_DISABLED=1 \
    nohup bash "$tick" >>"$tick_log" 2>&1 &
  echo $! > "$pidfile"
  log "sol_smart_signal_tick started pid=$! poll=${SOL_SMART_POLL_SECONDS:-30}s webhook=dedicated_only"
}

ensure_sol_smart_signal_tick
log "started pid=$$ deep=${DEEP_EVERY_SEC}s light=${LIGHT_EVERY_SEC}s themaran=${THEMARAN_EVERY_SEC}s gmgn_vet=${GMGN_VET_EVERY_SEC}s paper_daily=${PAPER_DAILY_EVERY_SEC}s audit=${AUDIT_EVERY_SEC}s hunt=${HUNT_EVERY_SEC}s trend_hunt=${TREND_HUNT_EVERY_SEC}s pnl_fill=${PNL_FILL_EVERY_SEC}s stonkfun=${STONKFUN_EVERY_SEC}s sol_smart=${SOL_SMART_EVERY_SEC}s sol7d=${SOL7D_EVERY_SEC}s dumpdip=${DUMPDIP_EVERY_SEC}s"
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
  # Fast wallet quality audit — local jsonl classify (no box GMGN); also dispatch GHA for commit
  last_wa=$(cat "$STATE/last_wallet_audit" 2>/dev/null || echo 0)
  if (( now - last_wa >= AUDIT_EVERY_SEC )); then
    action="wallet_audit"
    if ( cd "$ROOT" && LIVE_TRADING=0 AUDIT_TAG_WATCH=1 AUDIT_RPC=0 timeout 120 python3 scripts/audit_wallet_quality.py ) >> "$LOG" 2>&1; then
      log "wallet_audit local ok"
      # commit+push if dirty (daemon identity)
      if ( cd "$ROOT" && git status --porcelain rh-wallets/audit_*.jsonl rh-wallets/summary_wallet_audit.md rh-wallets/wallets.jsonl 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add rh-wallets/audit_active.jsonl rh-wallets/audit_inactive.jsonl \
                  rh-wallets/audit_quality.jsonl rh-wallets/audit_weak.jsonl \
                  rh-wallets/summary_wallet_audit.md rh-wallets/wallets.jsonl && \
          git commit -m "chore(watch): wallet quality audit (scout-bot)" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "wallet_audit commit/push fail"
      fi
    else
      log "wallet_audit local fail"
    fi
    if timeout 120 gh workflow run "$AUDIT_WF" --repo "$REPO" -f rpc=0 -f tag_watch=1 >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_wallet_audit"; log "wallet_audit gha dispatched"
    else
      echo "$now" > "$STATE/last_wallet_audit"
      result="wallet_audit_dispatch_failed"; log "wallet_audit gha dispatch failed"
    fi
  fi
  # On-chain hot wallet hunt — tip-follow recent blocks (free RPC; no GMGN)
  last_hunt=$(cat "$STATE/last_onchain_hunt" 2>/dev/null || echo 0)
  if (( now - last_hunt >= HUNT_EVERY_SEC )); then
    action="onchain_hunt"
    if ( cd "$ROOT" && LIVE_TRADING=0 timeout 420 python3 scripts/onchain_hunt_wallets.py --once --blocks "${HUNT_BLOCKS:-800}" ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_onchain_hunt"; log "onchain_hunt ok"
      if ( cd "$ROOT" && git status --porcelain rh-wallets/onchain_hot_active.jsonl rh-wallets/summary_onchain_hot.md rh-wallets/raw/onchain_hunt_state.json 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add rh-wallets/onchain_hot_active.jsonl rh-wallets/summary_onchain_hot.md rh-wallets/raw/onchain_hunt_state.json scripts/onchain_hunt_wallets.py && \
          git commit -m "chore(onchain): hot active wallet hunt" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "onchain_hunt commit/push fail"
      fi
    else
      result="onchain_hunt_failed"; log "onchain_hunt failed"
      echo "$now" > "$STATE/last_onchain_hunt"
    fi
  fi
  # Unknown smart wallets from trending CAs (jina gecko/dex + free RH RPC; no GMGN)
  # then analyze → promote only quality-gated active smart unknowns into wallets.jsonl
  last_th=$(cat "$STATE/last_trend_hunt" 2>/dev/null || echo 0)
  if (( now - last_th >= TREND_HUNT_EVERY_SEC )); then
    action="trend_unknown_hunt"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 TREND_ONCE=1 timeout 480 python3 scripts/hunt_unknown_from_trend.py --once ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_trend_hunt"; log "trend_unknown_hunt ok"
      # Quality-gated promote (never dump full unknown list)
      if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 PROMOTE_DRY_RUN=0 timeout 120 python3 scripts/promote_unknown_smart.py --apply ) >> "$LOG" 2>&1; then
        log "promote_unknown_smart ok"
      else
        log "promote_unknown_smart failed"
      fi
      # Kick on-chain PnL fill for newly promoted unknowns (weak gaps)
      if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 PNL_EST_CAP="${PNL_EST_CAP_AFTER_PROMOTE:-30}" PNL_EST_CA_CONC="${PNL_EST_CA_CONC:-3}" PNL_EST_PROMOTE=1 timeout 480 python3 scripts/estimate_wallet_pnl_onchain.py --cap "${PNL_EST_CAP_AFTER_PROMOTE:-30}" ) >> "$LOG" 2>&1; then
        echo "$now" > "$STATE/last_pnl_fill"; log "onchain_pnl_fill after promote ok"
      else
        log "onchain_pnl_fill after promote failed"
      fi
      if ( cd "$ROOT" && git status --porcelain rh-wallets/unknown_trend_smart.jsonl rh-wallets/summary_unknown_trend_smart.md rh-wallets/watch_candidates_unknown.jsonl rh-wallets/raw/unknown_trend_hunt_state.json rh-wallets/wallets.jsonl rh-wallets/promote_unknown_log.jsonl rh-wallets/summary_promote_unknown.md scripts/hunt_unknown_from_trend.py scripts/promote_unknown_smart.py 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add rh-wallets/unknown_trend_smart.jsonl rh-wallets/summary_unknown_trend_smart.md \
                  rh-wallets/watch_candidates_unknown.jsonl rh-wallets/raw/unknown_trend_hunt_state.json \
                  rh-wallets/wallets.jsonl rh-wallets/promote_unknown_log.jsonl rh-wallets/summary_promote_unknown.md \
                  rh-wallets/raw/wallets_pre_unknown_promote.jsonl \
                  rh-wallets/summary_onchain_pnl.md rh-wallets/pnl_fill_log.jsonl rh-wallets/raw/onchain_pnl_state.json \
                  scripts/hunt_unknown_from_trend.py scripts/promote_unknown_smart.py scripts/estimate_wallet_pnl_onchain.py scripts/scout-wallet-bot.sh && \
          git commit -m "chore(onchain): trend hunt + promote + pnl est" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "trend_unknown_hunt commit/push fail"
      fi
    else
      result="trend_unknown_hunt_failed"; log "trend_unknown_hunt failed"
      echo "$now" > "$STATE/last_trend_hunt"
    fi
  fi

  # StonkFun Solana digger hunt — free API + public RPC (separate from RH onchain tick)
  last_sf=$(cat "$STATE/last_stonkfun" 2>/dev/null || echo 0)
  if (( now - last_sf >= STONKFUN_EVERY_SEC )); then
    action="stonkfun_diggers"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 STONK_ONCE=1 timeout 900 python3 scripts/hunt_stonkfun_diggers.py --once ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_stonkfun"; log "stonkfun_diggers ok"
      # PnL + activity vet → merge profitable active diggers (separate from sol_smart / RH)
      if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 timeout 600 python3 scripts/promote_stonkfun_diggers.py --apply ) >> "$LOG" 2>&1; then
        log "promote_stonkfun_diggers ok"
      else
        log "promote_stonkfun_diggers failed"
      fi
      if ( cd "$ROOT" && git status --porcelain sol-wallets/stonkfun_diggers.jsonl sol-wallets/summary_stonkfun_diggers.md sol-wallets/summary_promote_stonkfun.md sol-wallets/promote_stonkfun_log.jsonl sol-wallets/raw/stonkfun_hunt_state.json sol-wallets/raw/stonkfun_promote_state.json scripts/hunt_stonkfun_diggers.py scripts/promote_stonkfun_diggers.py 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add sol-wallets/stonkfun_diggers.jsonl sol-wallets/summary_stonkfun_diggers.md \
                  sol-wallets/summary_promote_stonkfun.md sol-wallets/promote_stonkfun_log.jsonl \
                  sol-wallets/raw/stonkfun_hunt_state.json sol-wallets/raw/stonkfun_promote_state.json \
                  scripts/hunt_stonkfun_diggers.py scripts/promote_stonkfun_diggers.py scripts/scout-wallet-bot.sh && \
          git commit -m "chore(sol): stonkfun digger hunt + promote" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "stonkfun_diggers commit/push fail"
      fi
    else
      result="stonkfun_diggers_failed"; log "stonkfun_diggers failed"
      echo "$now" > "$STATE/last_stonkfun"
    fi
  fi

  # Solana-wide smart unknown hunt — Dex/Gecko/pump free (SEPARATE from stonkfun diggers)
  last_ss=$(cat "$STATE/last_sol_smart" 2>/dev/null || echo 0)
  if (( now - last_ss >= SOL_SMART_EVERY_SEC )); then
    action="sol_smart_hunt"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 SOL_TREND_ONCE=1 timeout 900 python3 scripts/hunt_sol_smart_wallets.py --once ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_sol_smart"; log "sol_smart_hunt ok"
      if ( cd "$ROOT" && git status --porcelain sol-wallets/sol_smart_unknown.jsonl sol-wallets/summary_sol_smart_unknown.md sol-wallets/watch_candidates_sol.jsonl sol-wallets/raw/sol_smart_hunt_state.json scripts/hunt_sol_smart_wallets.py 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add sol-wallets/sol_smart_unknown.jsonl sol-wallets/summary_sol_smart_unknown.md \
                  sol-wallets/watch_candidates_sol.jsonl sol-wallets/raw/sol_smart_hunt_state.json \
                  sol-wallets/raw/sol_smart_unknown_all.jsonl sol-wallets/README.md \
                  scripts/hunt_sol_smart_wallets.py scripts/scout-wallet-bot.sh && \
          git commit -m "chore(sol): solana-wide smart wallet hunt" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "sol_smart_hunt commit/push fail"
      fi
    else
      result="sol_smart_hunt_failed"; log "sol_smart_hunt failed"
      echo "$now" > "$STATE/last_sol_smart"
    fi
  fi

  # Solana 7d active smart wallets via official RPC (SEPARATE from stonkfun / gecko sol_smart)
  last_s7=$(cat "$STATE/last_sol7d" 2>/dev/null || echo 0)
  if (( now - last_s7 >= SOL7D_EVERY_SEC )); then
    action="sol7d_hunt"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 SOLANA_RPC_URL="${SOLANA_RPC_URL:-https://api.mainnet-beta.solana.com}" SOL7D_ONCE=1 timeout 2700 python3 scripts/hunt_sol_smart_7d.py --once ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_sol7d"; log "sol7d_hunt ok"
    else
      result="sol7d_hunt_failed"; log "sol7d_hunt failed"
      echo "$now" > "$STATE/last_sol7d"
    fi
  fi

  # Dump-dip smart wallets (pre_grad_dip + post_grad_dip) — SEPARATE from early diggers
  last_dd=$(cat "$STATE/last_dumpdip" 2>/dev/null || echo 0)
  if (( now - last_dd >= DUMPDIP_EVERY_SEC )); then
    action="dumpdip_hunt"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 \
      SOLANA_RPC_URL="${DUMPDIP_RPC_URL:-${SOLANA_RPC_URL:-https://solana-rpc.publicnode.com}}" \
      SOLANA_RPC_FALLBACK="${SOLANA_RPC_FALLBACK:-https://api.mainnet-beta.solana.com}" \
      DUMPDIP_ONCE=1 timeout 2700 python3 scripts/hunt_sol_dump_dip.py --once ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_dumpdip"; log "dumpdip_hunt ok"
      if ( cd "$ROOT" && git status --porcelain sol-wallets/sol_dump_dip_smart.jsonl sol-wallets/summary_sol_dump_dip.md sol-wallets/watch_candidates_sol.jsonl sol-wallets/raw/dump_dip_state.json scripts/hunt_sol_dump_dip.py 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add sol-wallets/sol_dump_dip_smart.jsonl sol-wallets/summary_sol_dump_dip.md \
                  sol-wallets/watch_candidates_sol.jsonl sol-wallets/raw/dump_dip_state.json \
                  sol-wallets/raw/dump_dip_all.jsonl sol-wallets/raw/dump_dip \
                  sol-wallets/README.md scripts/hunt_sol_dump_dip.py scripts/scout-wallet-bot.sh && \
          git commit -m "chore(sol): dump-dip smart wallet hunt (pre+post grad)" && \
          git pull --rebase origin main && git push origin HEAD:main ) >> "$LOG" 2>&1 || log "dumpdip_hunt commit/push fail"
      fi
    else
      result="dumpdip_hunt_failed"; log "dumpdip_hunt failed"
      echo "$now" > "$STATE/last_dumpdip"
    fi
  fi


  # On-chain PnL estimate + weak-field auto-fill (no box GMGN)
  last_pnl=$(cat "$STATE/last_pnl_fill" 2>/dev/null || echo 0)
  if (( now - last_pnl >= PNL_FILL_EVERY_SEC )); then
    action="onchain_pnl_fill"
    if ( cd "$ROOT" && LIVE_TRADING=0 GMGN_DISABLED=1 PNL_EST_CAP="${PNL_EST_CAP:-40}" PNL_EST_CA_CONC="${PNL_EST_CA_CONC:-3}" PNL_EST_PROMOTE=1 timeout 600 python3 scripts/estimate_wallet_pnl_onchain.py --cap "${PNL_EST_CAP:-40}" ) >> "$LOG" 2>&1; then
      echo "$now" > "$STATE/last_pnl_fill"; log "onchain_pnl_fill ok"
      if ( cd "$ROOT" && git status --porcelain rh-wallets/wallets.jsonl rh-wallets/summary_onchain_pnl.md rh-wallets/pnl_fill_log.jsonl rh-wallets/raw/onchain_pnl_state.json rh-wallets/raw/wallets_pre_onchain_pnl.jsonl scripts/estimate_wallet_pnl_onchain.py 2>/dev/null | grep -q . ); then
        ( cd "$ROOT" && \
          git add rh-wallets/wallets.jsonl rh-wallets/summary_onchain_pnl.md rh-wallets/pnl_fill_log.jsonl \
                  rh-wallets/raw/onchain_pnl_state.json rh-wallets/raw/wallets_pre_onchain_pnl.jsonl                   rh-wallets/summary_unknown_trend_pnl.md                   scripts/estimate_wallet_pnl_onchain.py scripts/scout-wallet-bot.sh && \
          git commit -m "chore(onchain): pnl est + weak field auto-fill" || true;           git stash push -u -m "daemon-pnl-push-tmp" -- . >/dev/null 2>&1 || true;           git pull --rebase origin main && git push origin HEAD:main;           st=$?; git stash pop >/dev/null 2>&1 || true; exit $st ) >> "$LOG" 2>&1 || log "onchain_pnl_fill commit/push fail"
      fi
    else
      result="onchain_pnl_fill_failed"; log "onchain_pnl_fill failed"
      echo "$now" > "$STATE/last_pnl_fill"
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
  ensure_stonkfun_signal_tick
  ensure_sol_smart_signal_tick
  sleep "$POLL_SEC"
done

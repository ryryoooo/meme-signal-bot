#!/usr/bin/env bash
# Pull/push shared RH signal state via GitHub release tag `signal-state`.
# Best-effort / non-blocking — never fails the caller; GitHub 429 is NOT a notify failure.
set -u
ROOT="${ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO="${SIGNAL_STATE_REPO:-ryryoooo/meme-signal-bot}"
TAG="${SIGNAL_STATE_TAG:-signal-state}"
STATE_PATH="${STATE_PATH:-$ROOT/state.json}"
TMPDIR_SYNC="${SIGNAL_STATE_TMP:-/tmp/signal-state-sync-$$}"
MODE="${1:-pull}"  # pull | push
MAX_TRIES="${SIGNAL_STATE_SYNC_RETRIES:-4}"

mkdir -p "$TMPDIR_SYNC"
cleanup() { rm -rf "$TMPDIR_SYNC"; }
trap cleanup EXIT

# Run gh with simple exponential backoff on rate limits / transient errors.
gh_retry() {
  local attempt=1 wait=2 out rc
  while (( attempt <= MAX_TRIES )); do
    out=$(gh "$@" 2>&1)
    rc=$?
    if (( rc == 0 )); then
      printf '%s' "$out"
      return 0
    fi
    if echo "$out" | grep -Eiq '429|rate limit|abuse|secondary rate|API rate'; then
      echo "signal_state_sync: GitHub 429/rate-limit (non-blocking) attempt=${attempt}/${MAX_TRIES} sleep=${wait}s" >&2
      sleep "$wait"
      wait=$(( wait * 2 ))
      (( wait > 60 )) && wait=60
      attempt=$(( attempt + 1 ))
      continue
    fi
    echo "signal_state_sync: gh err (non-blocking) rc=$rc: ${out:0:200}" >&2
    return "$rc"
  done
  echo "signal_state_sync: giving up after ${MAX_TRIES} tries (non-blocking; notify path unaffected)" >&2
  return 1
}

ensure_release() {
  if gh_retry release view "$TAG" --repo "$REPO" >/dev/null; then
    return 0
  fi
  gh_retry release create "$TAG" --repo "$REPO" --title "RH signal shared state"     --notes "Box↔GHA dedupe for ca_last_posted / seen_signal_keys. Auto-updated."     >/dev/null || true
}

pull_merge() {
  ensure_release || true
  rm -f "$TMPDIR_SYNC/state.json"
  if ! gh_retry release download "$TAG" --repo "$REPO" -p state.json -D "$TMPDIR_SYNC" >/dev/null; then
    echo "signal_state_sync pull: no remote state yet (ok)"
    return 0
  fi
  local local_state="$STATE_PATH"
  if [[ ! -f "$local_state" ]]; then
    mkdir -p "$(dirname "$local_state")"
    echo '{}' > "$local_state"
  fi
  python3 "$ROOT/scripts/merge_signal_state.py" "$local_state" "$TMPDIR_SYNC/state.json"     -o "$local_state" --keep-fomo-from a
  echo "signal_state_sync pull: ok"
}

push_state() {
  [[ -f "$STATE_PATH" ]] || { echo "signal_state_sync push: missing $STATE_PATH (ok)"; return 0; }
  ensure_release || true
  rm -f "$TMPDIR_SYNC/state.json"
  if gh_retry release download "$TAG" --repo "$REPO" -p state.json -D "$TMPDIR_SYNC" >/dev/null; then
    python3 "$ROOT/scripts/merge_signal_state.py" "$STATE_PATH" "$TMPDIR_SYNC/state.json"       -o "$STATE_PATH" --keep-fomo-from a
  fi
  if gh_retry release upload "$TAG" "$STATE_PATH" --repo "$REPO" --clobber >/dev/null; then
    echo "signal_state_sync push: ok"
  else
    echo "signal_state_sync push: failed (non-blocking; Discord notify unaffected)"
  fi
}

case "$MODE" in
  pull) pull_merge ;;
  push) push_state ;;
  *) echo "usage: $0 pull|push"; exit 2 ;;
esac

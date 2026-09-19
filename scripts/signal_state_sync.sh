#!/usr/bin/env bash
# Pull/push shared RH signal state via GitHub release tag `signal-state`.
# Best-effort — never fails the caller hard.
set -u
ROOT="${ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO="${SIGNAL_STATE_REPO:-ryryoooo/meme-signal-bot}"
TAG="${SIGNAL_STATE_TAG:-signal-state}"
STATE_PATH="${STATE_PATH:-$ROOT/state.json}"
TMPDIR_SYNC="${SIGNAL_STATE_TMP:-/tmp/signal-state-sync-$$}"
MODE="${1:-pull}"  # pull | push

mkdir -p "$TMPDIR_SYNC"
cleanup() { rm -rf "$TMPDIR_SYNC"; }
trap cleanup EXIT

ensure_release() {
  if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    return 0
  fi
  gh release create "$TAG" --repo "$REPO" --title "RH signal shared state" \
    --notes "Box↔GHA dedupe for ca_last_posted / seen_signal_keys. Auto-updated." \
    >/dev/null 2>&1 || true
}

pull_merge() {
  ensure_release
  rm -f "$TMPDIR_SYNC/state.json"
  if ! gh release download "$TAG" --repo "$REPO" -p state.json -D "$TMPDIR_SYNC" >/dev/null 2>&1; then
    echo "signal_state_sync pull: no remote state yet"
    return 0
  fi
  local local_state="$STATE_PATH"
  if [[ ! -f "$local_state" ]]; then
    mkdir -p "$(dirname "$local_state")"
    echo '{}' > "$local_state"
  fi
  # preserve local FOMO timers (box fast tick)
  python3 "$ROOT/scripts/merge_signal_state.py" "$local_state" "$TMPDIR_SYNC/state.json" \
    -o "$local_state" --keep-fomo-from a
}

push_state() {
  [[ -f "$STATE_PATH" ]] || { echo "signal_state_sync push: missing $STATE_PATH"; return 0; }
  ensure_release
  # merge remote first so we don't clobber peer writes
  rm -f "$TMPDIR_SYNC/state.json"
  if gh release download "$TAG" --repo "$REPO" -p state.json -D "$TMPDIR_SYNC" >/dev/null 2>&1; then
    python3 "$ROOT/scripts/merge_signal_state.py" "$STATE_PATH" "$TMPDIR_SYNC/state.json" \
      -o "$STATE_PATH" --keep-fomo-from a
  fi
  # upload (clobber)
  if gh release upload "$TAG" "$STATE_PATH" --repo "$REPO" --clobber >/dev/null 2>&1; then
    echo "signal_state_sync push: ok"
  else
    # retry after ensure
    ensure_release
    gh release upload "$TAG" "$STATE_PATH" --repo "$REPO" --clobber >/dev/null 2>&1 \
      && echo "signal_state_sync push: ok (retry)" \
      || echo "signal_state_sync push: failed"
  fi
}

case "$MODE" in
  pull) pull_merge ;;
  push) push_state ;;
  *) echo "usage: $0 pull|push"; exit 2 ;;
esac

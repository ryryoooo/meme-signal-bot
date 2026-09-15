#!/usr/bin/env python3
"""Silent 1s paper TP/SL watcher on the box. No Discord. No Super wakes.

Price: DexScreener (free). GMGN unused here to save API budget.
Sync: occasionally pull latest GHA paper-state opens into local book.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import paper_trade as paper_mod  # noqa: E402

LIVE = Path(os.environ.get("PAPER_LIVE_DIR") or "/workspace/meme-foundation/paper-live")
STATE = LIVE / "state.json"
BOOK = LIVE / "paper_book.jsonl"
LOG = LIVE / "tick.log"
REPO = os.environ.get("PAPER_REPO") or "ryryoooo/meme-signal-bot"
INTERVAL = float(os.environ.get("PAPER_TICK_SEC") or "1")
SYNC_EVERY = float(os.environ.get("PAPER_SYNC_SEC") or "120")
CHAIN = os.environ.get("CHAIN") or "robinhood"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LIVE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    if not STATE.exists():
        return {"paper_positions": [], "paper": {}}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"paper_positions": [], "paper": {}}


def save_state(st: dict) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def fetch_dex_price(ca: str, chain: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-tick/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "price_usd": None, "reason": type(e).__name__}
    pairs = (data or {}).get("pairs") or []
    if not pairs:
        return {"ok": False, "price_usd": None, "reason": "no_pair"}
    slug = chain.lower()
    preferred = [p for p in pairs if (p.get("chainId") or "").lower() == slug]
    pool = preferred or pairs

    def liq(p):
        try:
            return float(((p.get("liquidity") or {}).get("usd")) or 0)
        except (TypeError, ValueError):
            return 0.0

    pair = max(pool, key=liq)
    try:
        price = float(pair.get("priceUsd")) if pair.get("priceUsd") is not None else None
    except (TypeError, ValueError):
        price = None
    mcap = pair.get("marketCap")
    liq_usd = (pair.get("liquidity") or {}).get("usd")
    try:
        mcap = float(mcap) if mcap is not None else None
    except (TypeError, ValueError):
        mcap = None
    try:
        liq_usd = float(liq_usd) if liq_usd is not None else None
    except (TypeError, ValueError):
        liq_usd = None
    return {
        "ok": bool(price and price > 0),
        "price_usd": price,
        "mcap_usd": mcap,
        "fdv": mcap,
        "liq_usd": liq_usd,
        "reason": None if price else "no_price",
    }


def sync_opens_from_gha(local: dict) -> dict:
    """Pull newest paper-state artifact; adopt remote opens we haven't closed locally."""
    try:
        out = subprocess.check_output(
            [
                "gh",
                "run",
                "list",
                "-R",
                REPO,
                "--workflow=meme-signal",
                "--limit",
                "1",
                "--json",
                "databaseId",
            ],
            text=True,
            timeout=30,
        )
        runs = json.loads(out)
        if not runs:
            return local
        rid = runs[0]["databaseId"]
        arts = json.loads(
            subprocess.check_output(
                [
                    "gh",
                    "api",
                    f"repos/{REPO}/actions/runs/{rid}/artifacts",
                ],
                text=True,
                timeout=30,
            )
        )
        aid = None
        for a in arts.get("artifacts") or []:
            if a.get("name") == "paper-state" and not a.get("expired"):
                aid = a["id"]
                break
        if not aid:
            return local
        zpath = LIVE / "_sync.zip"
        raw = subprocess.check_output(
            ["gh", "api", f"repos/{REPO}/actions/artifacts/{aid}/zip"],
            timeout=60,
        )
        zpath.write_bytes(raw)
        subprocess.check_call(
            ["unzip", "-o", "-q", str(zpath), "state.json", "-d", str(LIVE / "_sync")],
            timeout=30,
        )
        remote = json.loads((LIVE / "_sync" / "state.json").read_text(encoding="utf-8"))
    except Exception as e:
        log(f"sync skip: {type(e).__name__}")
        return local

    closed = {
        (p.get("ca") or "").lower()
        for p in (local.get("paper_positions") or [])
        if (p.get("status") or "") in ("stopped", "closed", "done")
    }
    # also remember locally exited cas
    closed |= set(local.get("tick_closed_cas") or [])

    local_active = {
        (p.get("ca") or "").lower()
        for p in (local.get("paper_positions") or [])
        if (p.get("status") or "open") in ("open", "half_taken")
    }

    adopted = 0
    positions = list(local.get("paper_positions") or [])
    for rp in remote.get("paper_positions") or []:
        st = rp.get("status") or "open"
        if st not in ("open", "half_taken"):
            continue
        ca = (rp.get("ca") or "").lower()
        if not ca or ca in closed or ca in local_active:
            continue
        positions.append(rp)
        local_active.add(ca)
        adopted += 1

    # If local has no paper bankroll yet, take remote paper meta (cash etc.) once
    if not (local.get("paper") or {}).get("cash_usd") and remote.get("paper"):
        local["paper"] = remote["paper"]

    local["paper_positions"] = positions
    if adopted:
        log(f"sync adopted_opens={adopted} active={len(local_active)}")
    return local


def main() -> int:
    LIVE.mkdir(parents=True, exist_ok=True)
    BOOK.touch(exist_ok=True)
    log(f"start interval={INTERVAL}s sync_every={SYNC_EVERY}s chain={CHAIN} dir={LIVE}")
    last_sync = 0.0
    last_status = 0.0
    while True:
        t0 = time.time()
        st = load_state()
        if t0 - last_sync >= SYNC_EVERY:
            st = sync_opens_from_gha(st)
            save_state(st)
            last_sync = t0

        def fetch(_ca: str, ch: str) -> dict:
            return fetch_dex_price(_ca, ch or CHAIN)

        before = {
            (p.get("ca") or "").lower(): p.get("status")
            for p in (st.get("paper_positions") or [])
        }
        stats = paper_mod.process_paper_positions(
            st,
            BOOK,
            CHAIN,
            fetch,
            webhook=None,
            discord_post=None,
        )
        # remember newly closed
        closed = list(st.get("tick_closed_cas") or [])
        for p in st.get("paper_positions") or []:
            ca = (p.get("ca") or "").lower()
            prev = before.get(ca)
            now_st = p.get("status")
            if prev in ("open", "half_taken") and now_st in ("stopped", "closed"):
                if ca not in closed:
                    closed.append(ca)
                log(
                    f"exit {p.get('symbol')} {ca[:10]}… status={now_st} "
                    f"mult={p.get('last_mark_mult')} mark={p.get('last_mark_price')}"
                )
        st["tick_closed_cas"] = closed[-200:]
        save_state(st)

        if t0 - last_status >= 60:
            opens = [
                p
                for p in (st.get("paper_positions") or [])
                if (p.get("status") or "open") in ("open", "half_taken")
            ]
            bits = []
            for p in opens:
                bits.append(
                    f"{p.get('symbol') or '?'}@{float(p.get('last_mark_mult') or 0):.3f}x"
                )
            log(
                f"tick marked={stats.get('marked')} open={stats.get('open')} "
                f"half={stats.get('half')} stop={stats.get('stop')} "
                f"[{', '.join(bits) or 'none'}]"
            )
            last_status = t0

        elapsed = time.time() - t0
        time.sleep(max(0.05, INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("stop")
        raise SystemExit(0)

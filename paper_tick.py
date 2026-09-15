#!/usr/bin/env python3
"""Box-owned silent paper TP/SL. No Discord. No Super wakes.

SoT: this process owns the paper book. GHA should set PAPER_TRADING=0 and only
post signal Discord + open_alerts; we adopt new alerts and run +100%/−40%.

Price: DexScreener first (free); GMGN fallback throttled (RH often missing on Dex).
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

try:
    import gmgn_token as gmgn_tok
except Exception:
    gmgn_tok = None  # type: ignore

LIVE = Path(os.environ.get("PAPER_LIVE_DIR") or "/workspace/meme-foundation/paper-live")
STATE = LIVE / "state.json"
BOOK = LIVE / "paper_book.jsonl"
LOG = LIVE / "tick.log"
REPO = os.environ.get("PAPER_REPO") or "ryryoooo/meme-signal-bot"
INTERVAL = float(os.environ.get("PAPER_TICK_SEC") or "5")
SYNC_EVERY = float(os.environ.get("PAPER_SYNC_SEC") or "90")
CHAIN = os.environ.get("CHAIN") or "robinhood"
GMGN_MIN_GAP = float(os.environ.get("PAPER_GMGN_GAP_SEC") or "20")

_gmgn_last: dict[str, float] = {}
_price_cache: dict[str, tuple[float, dict]] = {}
_PRICE_TTL = 8.0


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
        return {"paper_positions": [], "paper": {}, "tick_closed_cas": [], "seen_alert_cas": []}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"paper_positions": [], "paper": {}, "tick_closed_cas": [], "seen_alert_cas": []}


def save_state(st: dict) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def fetch_dex_price(ca: str, chain: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-tick/2.0"})
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
        "src": "dex",
    }


def fetch_gmgn_price(ca: str, chain: str) -> dict:
    if gmgn_tok is None:
        return {"ok": False, "price_usd": None, "reason": "no_gmgn"}
    now = time.time()
    last = _gmgn_last.get(ca.lower(), 0.0)
    if now - last < GMGN_MIN_GAP:
        return {"ok": False, "price_usd": None, "reason": "gmgn_throttle"}
    _gmgn_last[ca.lower()] = now
    try:
        snap = gmgn_tok.market_snapshot(chain, ca)
    except Exception as e:
        return {"ok": False, "price_usd": None, "reason": type(e).__name__}
    price = snap.get("price_usd")
    ok = bool(price and float(price) > 0)
    return {
        "ok": ok,
        "price_usd": float(price) if ok else None,
        "mcap_usd": snap.get("mcap_usd"),
        "fdv": snap.get("fdv"),
        "liq_usd": snap.get("liq_usd"),
        "reason": None if ok else (snap.get("reason") or "gmgn_empty"),
        "src": "gmgn",
    }


def fetch_price(ca: str, chain: str) -> dict:
    key = ca.lower()
    now = time.time()
    cached = _price_cache.get(key)
    if cached and now - cached[0] < _PRICE_TTL and cached[1].get("ok"):
        return cached[1]
    dex = fetch_dex_price(ca, chain)
    if dex.get("ok"):
        _price_cache[key] = (now, dex)
        return dex
    gm = fetch_gmgn_price(ca, chain)
    if gm.get("ok"):
        _price_cache[key] = (now, gm)
        return gm
    # keep last good mark briefly
    if cached and cached[1].get("ok"):
        out = dict(cached[1])
        out["reason"] = f"stale:{dex.get('reason')}/{gm.get('reason')}"
        return out
    return {"ok": False, "price_usd": None, "reason": f"{dex.get('reason')}+{gm.get('reason')}"}


def _download_remote_state() -> dict | None:
    """Prefer newest artifact that still has alerts / opens / posted log rows."""
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
                "8",
                "--json",
                "databaseId",
            ],
            text=True,
            timeout=30,
        )
        runs = json.loads(out)
    except Exception as e:
        log(f"sync skip: {type(e).__name__}")
        return None

    sync_dir = LIVE / "_sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    zpath = LIVE / "_sync.zip"
    best = None
    best_score = -1
    for run in runs:
        rid = run["databaseId"]
        try:
            arts = json.loads(
                subprocess.check_output(
                    ["gh", "api", f"repos/{REPO}/actions/runs/{rid}/artifacts"],
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
                continue
            raw = subprocess.check_output(
                ["gh", "api", f"repos/{REPO}/actions/artifacts/{aid}/zip"],
                timeout=60,
            )
            zpath.write_bytes(raw)
            subprocess.check_call(
                ["unzip", "-o", "-q", str(zpath), "-d", str(sync_dir)],
                timeout=30,
            )
            st = json.loads((sync_dir / "state.json").read_text(encoding="utf-8"))
            log_path = sync_dir / "paper_log.jsonl"
            if log_path.exists():
                st["_paper_log_path"] = str(log_path)
            score = len(st.get("open_alerts") or [])
            score += sum(
                1
                for p in (st.get("paper_positions") or [])
                if (p.get("status") or "open") in ("open", "half_taken")
            )
            if log_path.exists():
                score += sum(
                    1
                    for ln in log_path.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and json.loads(ln).get("posted")
                )
            if score > best_score:
                best_score = score
                best = st
                if score > 0:
                    # good enough — prefer freshest non-empty
                    break
        except Exception:
            continue
    if best is None:
        log("sync skip: no artifacts")
    elif best_score <= 0:
        log("sync note: newest artifacts empty of opens/alerts")
    return best


def sync_from_gha_alerts(local: dict) -> dict:
    """Adopt new Discord-signal alerts as paper entries (cash-correct via open_paper_position)."""
    remote = _download_remote_state()
    if not remote:
        return local

    closed = set(local.get("tick_closed_cas") or [])
    closed |= {
        (p.get("ca") or "").lower()
        for p in (local.get("paper_positions") or [])
        if (p.get("status") or "") in ("stopped", "closed", "done")
    }
    active = [
        p
        for p in (local.get("paper_positions") or [])
        if (p.get("status") or "open") in ("open", "half_taken")
    ]
    if active:
        # already in a position — do not open another
        return local

    alerts = list(remote.get("open_alerts") or [])
    # Fallback: reconstruct alerts from paper_log posted rows (cache may drop open_alerts)
    log_path = remote.get("_paper_log_path")
    if log_path:
        try:
            for ln in Path(log_path).read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                row = json.loads(ln)
                if not row.get("posted"):
                    continue
                ca = (row.get("ca") or "").lower()
                if not ca:
                    continue
                price = row.get("alert_price_usd") or row.get("price_usd")
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                ts = row.get("ts")
                posted_at = 0.0
                if isinstance(ts, (int, float)):
                    posted_at = float(ts)
                elif isinstance(ts, str) and ts:
                    try:
                        from datetime import datetime
                        posted_at = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        posted_at = 0.0
                alerts.append(
                    {
                        "ca": ca,
                        "symbol": row.get("symbol"),
                        "alert_price_usd": price,
                        "alert_mcap": row.get("mcap"),
                        "alert_liq": row.get("liq"),
                        "posted_at": posted_at,
                        "n": row.get("n") or 2,
                    }
                )
        except Exception as e:
            log(f"paper_log parse skip: {type(e).__name__}")

    # also mirror remote open paper_positions that still have entry_price
    for rp in remote.get("paper_positions") or []:
        if (rp.get("status") or "open") not in ("open", "half_taken"):
            continue
        ca = (rp.get("ca") or "").lower()
        if not ca:
            continue
        try:
            price = float(rp.get("entry_price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        alerts.append(
            {
                "ca": ca,
                "symbol": rp.get("symbol"),
                "alert_price_usd": price,
                "alert_mcap": rp.get("alert_mcap") or rp.get("last_mcap"),
                "alert_liq": rp.get("alert_liq") or rp.get("last_liq"),
                "posted_at": float(rp.get("opened_at") or 0),
                "n": int(rp.get("n") or 2),
            }
        )

    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    # de-dupe by ca keep newest
    dedup = {}
    for a in alerts:
        ca = (a.get("ca") or "").lower()
        if ca and ca not in dedup:
            dedup[ca] = a
    alerts = list(dedup.values())
    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    seen = set(local.get("seen_alert_cas") or [])
    log(f"sync candidates={len(alerts)} closed={len(closed)}")

    for alert in alerts:
        ca = (alert.get("ca") or "").lower()
        if not ca or ca in closed or ca in seen:
            continue
        price = alert.get("alert_price_usd")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        if not price or price <= 0:
            continue
        # skip very old alerts (>6h)
        try:
            age = time.time() - float(alert.get("posted_at") or 0)
        except (TypeError, ValueError):
            age = 0
        if age > 6 * 3600:
            seen.add(ca)
            continue
        pos = paper_mod.open_paper_position(
            local,
            BOOK,
            ca=ca,
            symbol=alert.get("symbol"),
            entry_price=price,
            n=int(alert.get("n") or 2),
            chain=CHAIN,
            mcap=alert.get("alert_mcap"),
            liq=alert.get("alert_liq"),
            webhook=None,
            discord_post=None,
        )
        seen.add(ca)
        if pos:
            log(f"sync open {alert.get('symbol')} {ca[:10]}… entry={price} from_alert")
            break
        log(f"sync open blocked {ca[:10]}…")
        break

    local["seen_alert_cas"] = list(seen)[-500:]
    return local


def main() -> int:
    LIVE.mkdir(parents=True, exist_ok=True)
    BOOK.touch(exist_ok=True)
    # ensure paper bankroll exists
    st0 = load_state()
    paper_mod.ensure_paper_state(st0)
    save_state(st0)
    log(
        f"start interval={INTERVAL}s sync_every={SYNC_EVERY}s chain={CHAIN} "
        f"gmgn_gap={GMGN_MIN_GAP}s dir={LIVE}"
    )
    last_sync = 0.0
    last_status = 0.0
    while True:
        t0 = time.time()
        st = load_state()
        if t0 - last_sync >= SYNC_EVERY:
            st = sync_from_gha_alerts(st)
            save_state(st)
            last_sync = t0

        before = {
            (p.get("ca") or "").lower(): p.get("status")
            for p in (st.get("paper_positions") or [])
        }
        stats = paper_mod.process_paper_positions(
            st,
            BOOK,
            CHAIN,
            lambda ca, ch: fetch_price(ca, ch or CHAIN),
            webhook=None,
            discord_post=None,
        )
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

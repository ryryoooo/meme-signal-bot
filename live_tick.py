#!/usr/bin/env python3
"""Box-owned Arc LIVE TP/SL + entry from GHA alerts. Key stays on the box.

GHA: LIVE_TRADING=0 (notify only). This process owns live_state / swaps.
Syncs newest paper-state-arc artifact open_alerts / paper_log_arc posted rows.
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

import live_trade as live_mod  # noqa: E402

try:
    import gmgn_token as gmgn_tok
except Exception:
    gmgn_tok = None  # type: ignore

try:
    from load_secrets import load as load_box_secrets, write_gmgn_dotenv
except Exception:
    def load_box_secrets(names=None):  # type: ignore
        return {}

    def write_gmgn_dotenv(path=None):  # type: ignore
        return False

LIVE = Path(os.environ.get("LIVE_TICK_DIR") or "/workspace/meme-foundation/live-arc")
STATE = LIVE / "live_state_arc.json"
BOOK = LIVE / "live_book_arc.jsonl"
LOCAL = LIVE / "tick_state.json"
LOG = LIVE / "tick.log"
REPO = os.environ.get("LIVE_REPO") or "ryryoooo/meme-signal-bot"
WORKFLOW = os.environ.get("LIVE_SYNC_WORKFLOW") or "meme-signal-arc"
ARTIFACT = os.environ.get("LIVE_SYNC_ARTIFACT") or "paper-state-arc"
INTERVAL = float(os.environ.get("LIVE_TICK_SEC") or "15")
SYNC_EVERY = float(os.environ.get("LIVE_SYNC_SEC") or "60")
CHAIN = "arc"
MAX_ALERT_AGE_SEC = float(os.environ.get("LIVE_ALERT_MAX_AGE_SEC") or "900")  # 15m
GMGN_MIN_GAP = float(os.environ.get("LIVE_GMGN_GAP_SEC") or "15")

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


def load_tick_meta() -> dict:
    if not LOCAL.exists():
        return {"seen_alert_cas": [], "tick_closed_cas": []}
    try:
        return json.loads(LOCAL.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_alert_cas": [], "tick_closed_cas": []}


def save_tick_meta(st: dict) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    tmp = LOCAL.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LOCAL)


def _live_webhook() -> str | None:
    load_box_secrets(
        [
            "DISCORD_ARC_LIVE_WEBHOOK_URL",
            "DISCORD_ARC_WEBHOOK_URL",
            "DISCORD_WEBHOOK_URL",
        ]
    )
    for k in ("DISCORD_ARC_LIVE_WEBHOOK_URL", "DISCORD_ARC_LIVE", "DISCORD_ARC_WEBHOOK_URL"):
        u = (os.environ.get(k) or "").strip()
        if u:
            return u
    return (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip() or None


def _discord_post(url: str, embeds: list | None = None, content: str = "", **_kw) -> None:
    body = json.dumps({"content": content or "", "embeds": embeds or []}).encode()
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        url + sep + "wait=true",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "live-tick-arc/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def fetch_dex_price(ca: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "live-tick/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "price_usd": None, "reason": type(e).__name__}
    pairs = (data or {}).get("pairs") or []
    if not pairs:
        return {"ok": False, "price_usd": None, "reason": "no_pair"}
    preferred = [p for p in pairs if (p.get("chainId") or "").lower() in ("arc", "arcadium")]
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


def fetch_gmgn_price(ca: str) -> dict:
    if gmgn_tok is None:
        return {"ok": False, "price_usd": None, "reason": "no_gmgn"}
    now = time.time()
    last = _gmgn_last.get(ca.lower(), 0.0)
    if now - last < GMGN_MIN_GAP:
        return {"ok": False, "price_usd": None, "reason": "gmgn_throttle"}
    _gmgn_last[ca.lower()] = now
    try:
        snap = gmgn_tok.market_snapshot("arc", ca)
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


def fetch_price(ca: str) -> dict:
    key = ca.lower()
    now = time.time()
    cached = _price_cache.get(key)
    if cached and now - cached[0] < _PRICE_TTL and cached[1].get("ok"):
        return cached[1]
    dex = fetch_dex_price(ca)
    if dex.get("ok"):
        _price_cache[key] = (now, dex)
        return dex
    gm = fetch_gmgn_price(ca)
    if gm.get("ok"):
        _price_cache[key] = (now, gm)
        return gm
    if cached and cached[1].get("ok"):
        out = dict(cached[1])
        out["reason"] = f"stale:{dex.get('reason')}/{gm.get('reason')}"
        return out
    return {"ok": False, "price_usd": None, "reason": f"{dex.get('reason')}+{gm.get('reason')}"}


def live_danger_gate(ca: str) -> tuple[bool, list[str]]:
    """Mirror bot.live_danger_gate: Arc ignores open_source danger."""
    if gmgn_tok is None:
        return False, ["no_gmgn"]
    sec, sec_err = gmgn_tok.fetch_token_security("arc", ca)
    if not sec:
        return False, [f"live_security:{sec_err or 'fail'}"]
    parsed = gmgn_tok.parse_security(sec)
    checklist = gmgn_tok.gmgn_security_checklist(parsed, "arc")
    _ok, fails = gmgn_tok.checklist_no_danger(checklist)
    fails = [f for f in fails if not str(f).startswith("audit_open_source:")]
    if fails:
        return False, fails
    return True, []


def _download_remote_alerts() -> list[dict]:
    try:
        out = subprocess.check_output(
            [
                "gh",
                "run",
                "list",
                "-R",
                REPO,
                f"--workflow={WORKFLOW}",
                "--limit",
                "6",
                "--json",
                "databaseId",
            ],
            text=True,
            timeout=30,
        )
        runs = json.loads(out)
    except Exception as e:
        log(f"sync skip list: {type(e).__name__}")
        return []

    sync_dir = LIVE / "_sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    zpath = LIVE / "_sync.zip"
    alerts: list[dict] = []
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
                if a.get("name") == ARTIFACT and not a.get("expired"):
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
            st_path = sync_dir / "state-arc.json"
            if st_path.exists():
                st = json.loads(st_path.read_text(encoding="utf-8"))
                for a in st.get("open_alerts") or []:
                    alerts.append(dict(a))
            log_path = sync_dir / "paper_log_arc.jsonl"
            if log_path.exists():
                for ln in log_path.read_text(encoding="utf-8").splitlines():
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
                            "chain": "arc",
                        }
                    )
            if alerts:
                break
        except Exception as e:
            log(f"sync run {rid} skip: {type(e).__name__}")
            continue
    return alerts


def sync_and_open(live_state: dict, meta: dict) -> dict:
    alerts = _download_remote_alerts()
    if not alerts:
        log("sync: no alerts")
        return meta

    now = time.time()
    closed = set(meta.get("tick_closed_cas") or [])
    seen = set(meta.get("seen_alert_cas") or [])
    live_mod.ensure_live_state(live_state)
    open_cas = {(p.get("ca") or "").lower() for p in live_mod.active_positions(live_state)}

    # de-dupe newest first
    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    dedup: dict[str, dict] = {}
    for a in alerts:
        ca = (a.get("ca") or "").lower()
        if ca and ca not in dedup:
            dedup[ca] = a
    alerts = list(dedup.values())
    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    log(f"sync candidates={len(alerts)} open={len(open_cas)} seen={len(seen)}")

    webhook = _live_webhook()
    for a in alerts:
        ca = (a.get("ca") or "").lower()
        if not ca or ca in seen or ca in closed or ca in open_cas:
            continue
        posted_at = float(a.get("posted_at") or 0)
        if posted_at and (now - posted_at) > MAX_ALERT_AGE_SEC:
            seen.add(ca)
            continue
        price = a.get("alert_price_usd")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        if not price or price <= 0:
            snap = fetch_price(ca)
            price = snap.get("price_usd")
        if not price or float(price) <= 0:
            log(f"skip no price {ca[:10]}…")
            continue

        danger_ok, danger_reasons = live_danger_gate(ca)
        try:
            live_mod.open_live_position(
                live_state,
                BOOK,
                ca=ca,
                symbol=a.get("symbol"),
                entry_price=float(price),
                n=int(a.get("n") or 2),
                chain=CHAIN,
                mcap=a.get("alert_mcap"),
                liq=a.get("alert_liq"),
                webhook=webhook,
                discord_post=_discord_post if webhook else None,
                danger_ok=danger_ok,
                danger_reasons=danger_reasons,
            )
            live_mod.save_live_state(STATE, live_state)
            open_cas.add(ca)
            log(f"live try {a.get('symbol')} {ca[:10]}… danger_ok={danger_ok}")
        except Exception as e:
            log(f"live open soft-fail {ca[:10]}… {type(e).__name__}")
        seen.add(ca)
        # respect max open via can_open inside open_live_position
        meta["seen_alert_cas"] = list(seen)[-500:]
        save_tick_meta(meta)
    meta["seen_alert_cas"] = list(seen)[-500:]
    return meta


def main() -> int:
    LIVE.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LIVE_STATE_PATH", str(STATE))
    os.environ.setdefault("LIVE_BOOK_PATH", str(BOOK))
    os.environ.setdefault("LIVE_BANKROLL_USD", os.environ.get("LIVE_BANKROLL_USD") or "80")
    os.environ.setdefault("LIVE_MAX_OPEN", "5")
    os.environ.setdefault("LIVE_WALLET_ADDRESS", "0x822AFdCc7f1Ec829f4456A3921ff54B4a6dBCfAe")
    os.environ.setdefault("GMGN_ALLOW_AUTOMATED_TRADES", "1")
    os.chdir(ROOT)

    load_box_secrets(["GMGN_API_KEY", "GMGN_PRIVATE_KEY", "DISCORD_ARC_LIVE_WEBHOOK_URL"])
    write_gmgn_dotenv()
    if not (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
        # also accept from dotenv already on disk (without printing)
        env_path = Path.home() / ".config/gmgn/.env"
        if env_path.exists():
            for ln in env_path.read_text(encoding="utf-8").splitlines():
                if ln.startswith("GMGN_PRIVATE_KEY=") and ln.split("=", 1)[1].strip():
                    os.environ["GMGN_PRIVATE_KEY"] = ln.split("=", 1)[1].strip()
                    break
    if not (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
        log("FATAL: GMGN_PRIVATE_KEY missing on box — cannot swap")
        return 2

    log(
        f"start interval={INTERVAL}s sync_every={SYNC_EVERY}s chain={CHAIN} "
        f"max_alert_age={MAX_ALERT_AGE_SEC}s"
    )
    meta = load_tick_meta()
    last_sync = 0.0
    while True:
        t0 = time.time()
        try:
            live_state = live_mod.load_live_state(STATE)
            live_mod.ensure_live_state(live_state)
            if t0 - last_sync >= SYNC_EVERY:
                meta = sync_and_open(live_state, meta)
                save_tick_meta(meta)
                last_sync = t0
            webhook = _live_webhook()
            live_mod.process_live_positions(
                live_state,
                BOOK,
                CHAIN,
                lambda ca, ch: fetch_price(ca),
                webhook=webhook,
                discord_post=_discord_post if webhook else None,
            )
            live_mod.save_live_state(STATE, live_state)
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        elapsed = time.time() - t0
        time.sleep(max(1.0, INTERVAL - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())

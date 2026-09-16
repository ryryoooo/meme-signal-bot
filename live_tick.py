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
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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
LOCAL_ALERTS = Path(os.environ.get("LIVE_LOCAL_ALERTS") or str(LIVE / "local_alerts.json"))
SIGNAL_STATE = Path(os.environ.get("LIVE_SIGNAL_STATE") or str(LIVE / "signal_state_arc.json"))
SIGNAL_LOG = Path(os.environ.get("LIVE_SIGNAL_LOG") or str(LIVE / "signal_log_arc.jsonl"))
REPO = os.environ.get("LIVE_REPO") or "ryryoooo/meme-signal-bot"
WORKFLOW = os.environ.get("LIVE_SYNC_WORKFLOW") or "meme-signal-arc"
ARTIFACT = os.environ.get("LIVE_SYNC_ARTIFACT") or "paper-state-arc"
INTERVAL = float(os.environ.get("LIVE_TICK_SEC") or "1")
SYNC_EVERY = float(os.environ.get("LIVE_SYNC_SEC") or "20")
CHAIN = "arc"
MAX_ALERT_AGE_SEC = float(os.environ.get("LIVE_ALERT_MAX_AGE_SEC") or "900")  # 15m
GMGN_MIN_GAP = float(os.environ.get("LIVE_GMGN_GAP_SEC") or "15")

_gmgn_last: dict[str, float] = {}
_price_cache: dict[str, tuple[float, dict]] = {}
_PRICE_TTL = float(os.environ.get("LIVE_PRICE_TTL") or "1.0")

_sync_lock = threading.Lock()
_sync_running = False
_pending_alerts: list[dict] | None = None
_pending_alerts_src = ""
_last_timing_log = 0.0


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
    tmp = LIVE / f".tick_state.{os.getpid()}.{time.time_ns()}.tmp"
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
    data = None
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        data = None
    if data is None:
        # Cloudflare sometimes 403s urllib; curl usually works on the box
        try:
            raw = subprocess.check_output(
                ["curl", "-fsS", "-A", ua, "-H", "Accept: application/json", "--max-time", "8", url],
                text=True,
                timeout=12,
            )
            data = json.loads(raw or "{}")
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
    if getattr(gmgn_tok, "gmgn_on_cooldown", lambda: False)():
        return {"ok": False, "price_usd": None, "reason": "gmgn_cooldown"}
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
    """DexScreener only. Never hit GMGN for marks (saves the IP ban for smartmoney)."""
    key = ca.lower()
    now = time.time()
    cached = _price_cache.get(key)
    if cached and now - cached[0] < _PRICE_TTL and cached[1].get("ok"):
        return cached[1]
    dex = fetch_dex_price(ca)
    if dex.get("ok"):
        _price_cache[key] = (now, dex)
        return dex
    if cached and cached[1].get("ok"):
        out = dict(cached[1])
        out["reason"] = f"stale:{dex.get('reason')}"
        return out
    return {"ok": False, "price_usd": None, "reason": dex.get("reason")}


def live_danger_gate(ca: str) -> tuple[bool, list[str]]:
    """Arc: no GMGN security. Honeypot/tax 🚫 needs GMGN and burns the IP.

    Heat/liq gates already ran on the signal. Pass so Dex marks + V4 swaps keep going.
    """
    return True, ["dex_skip_audit"]


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



def _alerts_from_state_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for a in st.get("open_alerts") or []:
        out.append(dict(a))
    return out


def _alerts_from_paper_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
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
            out.append(
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
    except Exception:
        return out
    return out


def load_local_alerts() -> tuple[list[dict], float]:
    """Box-primary alerts from local signal loop. Returns (alerts, newest_mtime)."""
    alerts: list[dict] = []
    mtime = 0.0
    for path in (LOCAL_ALERTS, SIGNAL_STATE, SIGNAL_LOG):
        if path.exists():
            try:
                mtime = max(mtime, path.stat().st_mtime)
            except OSError:
                pass
    if LOCAL_ALERTS.exists():
        try:
            raw = json.loads(LOCAL_ALERTS.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                alerts.extend(dict(a) for a in raw)
            elif isinstance(raw, dict):
                alerts.extend(dict(a) for a in (raw.get("open_alerts") or []))
        except Exception:
            pass
    alerts.extend(_alerts_from_state_file(SIGNAL_STATE))
    alerts.extend(_alerts_from_paper_log(SIGNAL_LOG))
    return alerts, mtime


def apply_alerts(live_state: dict, meta: dict, alerts: list[dict], *, src: str) -> dict:
    """Open live positions from alert list (local or GHA)."""
    if not alerts:
        return meta
    now = time.time()
    closed = set(meta.get("tick_closed_cas") or [])
    seen = set(meta.get("seen_alert_cas") or [])
    live_mod.ensure_live_state(live_state)
    open_cas = {(p.get("ca") or "").lower() for p in live_mod.active_positions(live_state)}

    alerts = [dict(a) for a in alerts]
    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    dedup: dict[str, dict] = {}
    for a in alerts:
        ca = (a.get("ca") or "").lower()
        if ca and ca not in dedup:
            dedup[ca] = a
    alerts = list(dedup.values())
    alerts.sort(key=lambda a: float(a.get("posted_at") or 0), reverse=True)
    log(f"alerts src={src} candidates={len(alerts)} open={len(open_cas)} seen={len(seen)}")

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
            log(f"live try {a.get('symbol')} {ca[:10]}… danger_ok={danger_ok} src={src}")
        except Exception as e:
            log(f"live open soft-fail {ca[:10]}… {type(e).__name__}")
        soft = (not danger_ok) and any(
            str(x).startswith("live_security_soft:") for x in (danger_reasons or [])
        )
        if not soft:
            seen.add(ca)
        meta["seen_alert_cas"] = list(seen)[-500:]
        save_tick_meta(meta)
    meta["seen_alert_cas"] = list(seen)[-500:]
    return meta


def _exit_pending(live_state: dict) -> bool:
    """True if a stop/half is likely this tick — prefer not starting a slow sync."""
    now = time.time()
    grace = live_mod.exit_grace_sec()
    for pos in live_mod.active_positions(live_state):
        opened = float(pos.get("opened_at") or 0)
        if opened and (now - opened) < grace:
            continue
        mult = pos.get("last_mark_mult")
        try:
            mult = float(mult) if mult is not None else None
        except (TypeError, ValueError):
            mult = None
        if mult is None:
            continue
        if mult <= live_mod.LIVE_STOP_MULT:
            return True
        if (not pos.get("half_taken")) and mult >= live_mod.LIVE_HALF_TAKE_MULT and (pos.get("status") or "") == "open":
            return True
    return False


def _sync_worker() -> None:
    global _sync_running, _pending_alerts, _pending_alerts_src
    try:
        local, local_mtime = load_local_alerts()
        remote: list[dict] = []
        # Prefer local when present/fresh; still fetch remote as backup merge
        try:
            remote = _download_remote_alerts()
        except Exception as e:
            log(f"sync remote fail: {type(e).__name__}")
        # Prefer local if newer than artifact zip or remote empty
        art_mtime = 0.0
        zpath = LIVE / "_sync.zip"
        if zpath.exists():
            try:
                art_mtime = zpath.stat().st_mtime
            except OSError:
                pass
        if local and (not remote or local_mtime >= art_mtime):
            merged = local + remote
            src = "local+gha" if remote else "local"
        elif remote:
            merged = remote + local
            src = "gha+local" if local else "gha"
        else:
            merged = local
            src = "local" if local else "none"
        with _sync_lock:
            _pending_alerts = merged
            _pending_alerts_src = src
        log(f"sync ready src={src} n={len(merged)} local_mtime={int(local_mtime)} art_mtime={int(art_mtime)}")
    finally:
        with _sync_lock:
            _sync_running = False


def prefetch_prices(cas: list[str]) -> None:
    """Parallel Dex fetches when multiple opens."""
    uniq = [c for c in dict.fromkeys(cas) if c]
    if len(uniq) <= 1:
        if uniq:
            fetch_price(uniq[0])
        return
    with ThreadPoolExecutor(max_workers=min(6, len(uniq))) as ex:
        futs = {ex.submit(fetch_price, ca): ca for ca in uniq}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception:
                pass



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
        # permanent seen only when not a soft security miss (retry next sync)
        soft = (not danger_ok) and any(str(x).startswith("live_security_soft:") for x in (danger_reasons or []))
        if not soft:
            seen.add(ca)
        # respect max open via can_open inside open_live_position
        meta["seen_alert_cas"] = list(seen)[-500:]
        save_tick_meta(meta)
    meta["seen_alert_cas"] = list(seen)[-500:]
    return meta


def main() -> int:
    global _sync_running, _pending_alerts, _pending_alerts_src, _last_timing_log
    LIVE.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LIVE_STATE_PATH", str(STATE))
    os.environ.setdefault("LIVE_BOOK_PATH", str(BOOK))
    os.environ.setdefault("LIVE_BANKROLL_USD", os.environ.get("LIVE_BANKROLL_USD") or "80")
    os.environ.setdefault("LIVE_MAX_OPEN", "5")
    os.environ.setdefault("LIVE_WALLET_ADDRESS", "0x822AFdCc7f1Ec829f4456A3921ff54B4a6dBCfAe")
    os.environ.setdefault("LIVE_EXIT_GRACE_SEC", os.environ.get("LIVE_EXIT_GRACE_SEC") or "15")
    os.environ.setdefault("LIVE_STOP_CONFIRM", os.environ.get("LIVE_STOP_CONFIRM") or "1")
    os.environ.setdefault("GMGN_ALLOW_AUTOMATED_TRADES", "1")
    os.chdir(ROOT)

    load_box_secrets(["GMGN_API_KEY", "GMGN_PRIVATE_KEY", "DISCORD_ARC_LIVE_WEBHOOK_URL"])
    write_gmgn_dotenv()
    if not (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
        for env_path in (
            Path.home() / ".config/gmgn/.env",
            LIVE / ".gmgn.env",
        ):
            if not env_path.exists():
                continue
            for ln in env_path.read_text(encoding="utf-8").splitlines():
                if ln.startswith("GMGN_PRIVATE_KEY=") and ln.split("=", 1)[1].strip():
                    os.environ["GMGN_PRIVATE_KEY"] = ln.split("=", 1)[1].strip()
                    break
            if (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
                break
    if not (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
        log("FATAL: GMGN_PRIVATE_KEY missing on box — cannot V4 swap")
        return 2

    log(
        f"start interval={INTERVAL}s sync_every={SYNC_EVERY}s price_ttl={_PRICE_TTL}s "
        f"grace={os.environ.get('LIVE_EXIT_GRACE_SEC')} stop_confirm={os.environ.get('LIVE_STOP_CONFIRM')} "
        f"chain={CHAIN} max_alert_age={MAX_ALERT_AGE_SEC}s"
    )
    meta = load_tick_meta()
    last_sync = 0.0
    while True:
        t0 = time.time()
        mark_ms = 0
        n_open = 0
        try:
            live_state = live_mod.load_live_state(STATE)
            live_mod.ensure_live_state(live_state)
            n_open = len(live_mod.active_positions(live_state))

            # Kick sync in background — never block marks on gh
            if (t0 - last_sync) >= SYNC_EVERY:
                start_sync = False
                with _sync_lock:
                    if not _sync_running:
                        if _exit_pending(live_state):
                            log("sync defer: exit pending")
                            last_sync = t0  # avoid tight defer spam; retry next window
                        else:
                            _sync_running = True
                            start_sync = True
                if start_sync:
                    threading.Thread(target=_sync_worker, name="live-sync", daemon=True).start()
                    last_sync = t0

            # Apply any finished sync alerts (after marks would also be fine; do before opens)
            pending = None
            src = ""
            with _sync_lock:
                if _pending_alerts is not None:
                    pending = _pending_alerts
                    src = _pending_alerts_src or "sync"
                    _pending_alerts = None
            # Also poll local alerts every tick when file newer (fast path, no gh)
            local_alerts, local_mtime = load_local_alerts()
            meta_mtime = float(meta.get("local_alerts_mtime") or 0)
            if local_alerts and local_mtime > meta_mtime:
                meta = apply_alerts(live_state, meta, local_alerts, src="local_tick")
                meta["local_alerts_mtime"] = local_mtime
                save_tick_meta(meta)
                live_state = live_mod.load_live_state(STATE)
                n_open = len(live_mod.active_positions(live_state))
            if pending:
                meta = apply_alerts(live_state, meta, pending, src=src)
                save_tick_meta(meta)
                live_state = live_mod.load_live_state(STATE)
                n_open = len(live_mod.active_positions(live_state))

            # ALWAYS mark every tick
            cas = [(p.get("ca") or "") for p in live_mod.active_positions(live_state)]
            t_mark = time.time()
            if cas:
                prefetch_prices(cas)
            webhook = _live_webhook()
            stats = live_mod.process_live_positions(
                live_state,
                BOOK,
                CHAIN,
                lambda ca, ch: fetch_price(ca),
                webhook=webhook,
                discord_post=_discord_post if webhook else None,
            )
            live_mod.save_live_state(STATE, live_state)
            mark_ms = int((time.time() - t_mark) * 1000)
            n_open = len(live_mod.active_positions(live_state))
            if n_open > 0 and (t0 - _last_timing_log) >= 5.0:
                log(
                    f"timing mark_ms={mark_ms} n_open={n_open} "
                    f"stats={stats} interval={INTERVAL}"
                )
                _last_timing_log = t0
            elif n_open == 0 and (t0 - _last_timing_log) >= 30.0:
                log(f"timing mark_ms={mark_ms} n_open=0 idle")
                _last_timing_log = t0
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        elapsed = time.time() - t0
        # Aggressive sleep when positions open
        if n_open > 0:
            time.sleep(max(0.2, INTERVAL - elapsed))
        else:
            time.sleep(max(0.5, INTERVAL - elapsed))



if __name__ == "__main__":
    raise SystemExit(main())

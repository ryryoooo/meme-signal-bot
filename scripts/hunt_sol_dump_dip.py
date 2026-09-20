#!/usr/bin/env python3
"""Hunt Solana wallets that profit by buying dips after dumps.

Covers BOTH stages (tag separately):
  - pre_grad_dip  — dump→dip buys on bonding curves (LaunchLab / pump.fun curve)
  - post_grad_dip — dump→dip buys after graduation (Raydium / Jupiter / pump AMM)

NOT early-curve diggers-only: does not require LaunchLab top-30.
Reuses seed 74pB entry mints when present to avoid RPC thrash.

Writes:
  sol-wallets/sol_dump_dip_smart.jsonl
  sol-wallets/summary_sol_dump_dip.md
  sol-wallets/raw/dump_dip_state.json
  sol-wallets/raw/dump_dip_all.jsonl
  optionally merges top into watch_candidates_sol.jsonl with tag dump_dip

LIVE_TRADING=0, no box GMGN. Official Solana RPC + publicnode fallback.
Never merges into RH wallets.jsonl or overwrites stonkfun early-only lists.

Env:
  SOLANA_RPC_URL, SOLANA_RPC_FALLBACK,
  DUMPDIP_CA_CAP (28), DUMPDIP_TX_PER_CA (40), DUMPDIP_SIG_PAGES (5),
  DUMPDIP_MIN_MINTS (2), DUMPDIP_DUMP_PCT (0.35), DUMPDIP_MIN_SCORE (10),
  DUMPDIP_TOP_WATCH (30), DUMPDIP_RPC_SLEEP (0.3), DUMPDIP_WINDOW_SEC (604800),
  DUMPDIP_UPDATE_WATCH=1, DUMPDIP_ONCE=1 / DUMPDIP_DAEMON=1,
  DUMPDIP_INTERVAL_SEC (3600), DUMPDIP_EXCLUDE_STONK=0 (keep diggers eligible;
  dump-dip is a different strategy — default keep them unless set)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
UA = os.environ.get(
    "DUMPDIP_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/dump-dip; +https://github.com/ryryoooo/meme-signal-bot)",
)
RPC_PRIMARY = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
RPC_FALLBACK = (os.environ.get("SOLANA_RPC_FALLBACK") or "https://api.mainnet-beta.solana.com").strip()
JINA = "https://r.jina.ai/http://"

OUT_DIR = ROOT / "sol-wallets"
OUT_JSONL = OUT_DIR / "sol_dump_dip_smart.jsonl"
OUT_MD = OUT_DIR / "summary_sol_dump_dip.md"
OUT_CAND = OUT_DIR / "watch_candidates_sol.jsonl"
STATE_PATH = OUT_DIR / "raw" / "dump_dip_state.json"
RAW_ALL = OUT_DIR / "raw" / "dump_dip_all.jsonl"
RAW_DIR = OUT_DIR / "raw" / "dump_dip"
SEED_DIR = OUT_DIR / "raw" / "seed_74pB"
SEED_ENTRIES = SEED_DIR / "seed_entries_raw.json"
SEED_WALLET = os.environ.get("SEED_WALLET", "74pBdqs9niFM5VUNwVMCQDK7TQhVB3ACKUEJ5DvmGThB").strip()

LAUNCHLAB = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAY_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAY_CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
JUP6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "USD1ttGY1N17NEEOGmBshDhNY0erjacQkMiQbmn6mP8",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "bSo13r4TkiE4KumL2BsLoAxnnYCuKQykLfQQUx1WEe",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektEV",
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
}

SKIP_WALLETS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    RAY_CPMM, RAY_V4, JUP6,
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcCanq",
    PUMP_FUN, PUMP_AMM, LAUNCHLAB,
    "pfeeUxB6jkeY1Hxd7CsFCGUgBfgf1G7BYnvyxZrBssu",
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",
    "WhirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1",
    "routeUGWgWzqBWFcrCfv8tritsquVsuZ8bXPd1BXbXz",
    "MEViEnscUm6tsQRoGd9h6nLQaQspKj7DB2M5FwM3Xvz",
    "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNytnA4Dy",
    "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi7hCJOzii2tN",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dQKvQT",
    "2AQdpHJ2JpcEgPiATUXjpxFGXsfW4b9gZvzWcJPcQ9jX",
    "5VCwKtCXgCJ6kit5FybXjvriW3xEynqrB6EVKBEgt55",
    "ASTyfSima4LLAdDgoFMkgmxrKrKn8RH5wRYnh66Wy4WC",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE",
    "5nnLqyxkEmgJhRn5nQVr8ouLm8q6xBqWKvQCk8T7u3vH",
    # common CEX / hub deposit hot wallets
    "5VCwKtCXgCJ6kit5FybXjvriW3xEynqrB6EVKBEgt55",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dQKvQT",
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
}

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MAJOR_SYMS = {
    "RAY", "JUP", "PUMP", "BONK", "WIF", "PYTH", "JITO", "HNT", "ORCA", "RENDER",
    "SOL", "WSOL", "USDC", "USDT", "MSOL", "JITOSOL", "BSOL", "ETH", "BTC", "WBTC",
    "UXD", "DAI", "USD1",
}

_rpc_url = [RPC_PRIMARY]
_rpc_sleep = [0.0]
_rpc_calls = [0]
_rpc_429s = [0]


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} dumpdip {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jst_now_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def _f(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def norm_sol(a: str | None) -> str | None:
    if not isinstance(a, str):
        return None
    a = a.strip()
    if a.startswith("0x") or not _BASE58_RE.match(a):
        return None
    return a


def http_get(url: str, timeout: float = 35.0, retries: int = 4) -> tuple[str | None, str | None]:
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            wait = min(20.0, 1.2 * (2 ** attempt))
            if e.code in (429, 403, 502, 503, 504):
                time.sleep(wait)
                continue
            time.sleep(wait * 0.5)
        except Exception as e:
            last = type(e).__name__
            time.sleep(min(12.0, 1.0 * (2 ** attempt)))
    return None, last


def extract_json(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    content = raw
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict):
            c = outer.get("data")
            if isinstance(c, dict):
                c = c.get("content") or c.get("text") or ""
            if isinstance(c, str) and c.strip():
                content = c
    except json.JSONDecodeError:
        content = raw
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[-1]
    content = re.sub(r"^```\w*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content)
    prefer = None
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, content)
        if not m:
            continue
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed
        if prefer is None and isinstance(parsed, (dict, list)):
            prefer = parsed
    return prefer


def jina_get_json(api_url: str, timeout: float = 40.0):
    if api_url.startswith("https://"):
        relay = JINA + api_url[len("https://") :]
    elif api_url.startswith("http://"):
        relay = JINA + api_url[len("http://") :]
    else:
        relay = JINA + api_url
    raw, err = http_get(relay, timeout=timeout)
    if raw is None:
        return None, err or "empty"
    data = extract_json(raw)
    if data is None:
        return None, "parse_fail"
    return data, None


def direct_get_json(url: str, timeout: float = 30.0):
    raw, err = http_get(url, timeout=timeout, retries=3)
    if raw is None:
        return None, err or "empty"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        data = extract_json(raw)
        if data is None:
            return None, "parse_fail"
        return data, None


def http_get_json(url: str, timeout: float = 20.0):
    data, err = direct_get_json(url, timeout=timeout)
    if data is not None:
        return data, None
    data2, err2 = jina_get_json(url, timeout=max(timeout, 35.0))
    if data2 is not None:
        return data2, None
    return None, err or err2


def load_jsonl_addrs(path: Path, key: str = "address") -> set[str]:
    out: set[str] = set()
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict):
                continue
            a = norm_sol(o.get(key) or o.get("wallet"))
            if a:
                out.add(a)
    return out


def rpc(method: str, params: list, timeout: float = 45.0):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last = None
    urls = [_rpc_url[0]]
    if RPC_FALLBACK and RPC_FALLBACK not in urls:
        urls.append(RPC_FALLBACK)
    base_sleep = env_float("DUMPDIP_RPC_SLEEP", 0.3)
    for url in urls:
        for attempt in range(8):
            gap = base_sleep + min(12.0, _rpc_sleep[0])
            if gap > 0:
                time.sleep(gap)
            try:
                req = urllib.request.Request(
                    url,
                    data=payload.encode(),
                    headers={"content-type": "application/json", "User-Agent": UA},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                _rpc_calls[0] += 1
                if body.get("error"):
                    err = body["error"]
                    msg = str(err)
                    code = err.get("code") if isinstance(err, dict) else None
                    if code in (-32005, 429) or "429" in msg or "rate" in msg.lower() or "Too many" in msg:
                        _rpc_429s[0] += 1
                        _rpc_sleep[0] = min(15.0, max(0.5, _rpc_sleep[0] * 1.7) + 0.4)
                        time.sleep(min(28.0, 0.9 * (2 ** attempt)))
                        continue
                    # auto-fix maxSupportedTransactionVersion if needed
                    if isinstance(err, dict) and "maxSupportedTransactionVersion" in msg:
                        if method == "getTransaction" and isinstance(params, list) and len(params) >= 2:
                            cfg = dict(params[1]) if isinstance(params[1], dict) else {}
                            cfg["maxSupportedTransactionVersion"] = 1
                            params = [params[0], cfg]
                            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                            continue
                    raise RuntimeError(err)
                _rpc_sleep[0] = max(0.0, _rpc_sleep[0] * 0.85 - 0.02)
                if url != _rpc_url[0]:
                    log(f"switched RPC -> {url}")
                    _rpc_url[0] = url
                return body.get("result")
            except urllib.error.HTTPError as e:
                last = e
                _rpc_calls[0] += 1
                if e.code == 429:
                    _rpc_429s[0] += 1
                    _rpc_sleep[0] = min(18.0, max(0.6, _rpc_sleep[0] * 1.8) + 0.5)
                    time.sleep(min(30.0, 1.0 * (2 ** attempt)))
                    continue
                time.sleep(min(12.0, 0.5 * (2 ** attempt)))
            except Exception as e:
                last = e
                time.sleep(min(10.0, 0.4 * (1.7 ** attempt)))
        log(f"RPC {method} exhausted on {url}: {last}; trying next")
    raise RuntimeError(f"RPC {method} failed: {last}")


def _tb_amount(tb: dict) -> float:
    ui = tb.get("uiTokenAmount") or {}
    try:
        if ui.get("uiAmount") is not None:
            return float(ui["uiAmount"])
        return float(ui.get("uiAmountString") or 0)
    except (TypeError, ValueError):
        return 0.0


def account_keys(msg: dict) -> list[str]:
    aks = msg.get("accountKeys") or []
    out = []
    for a in aks:
        if isinstance(a, dict):
            out.append(a.get("pubkey") or "")
        else:
            out.append(str(a))
    return out


def classify_venue(keys: list[str], log_msgs: list | None = None) -> str:
    ks = set(keys)
    if LAUNCHLAB in ks:
        return "stonkfun_launchlab"
    if PUMP_FUN in ks:
        return "pump_fun"
    if PUMP_AMM in ks:
        return "pump_amm"
    if RAY_V4 in ks or RAY_CPMM in ks:
        return "raydium"
    if JUP6 in ks:
        return "jupiter"
    blob = " ".join(str(x) for x in (log_msgs or []))[:2000].lower()
    if "pump" in blob:
        return "pump_like"
    if "launchlab" in blob or "stonk" in blob:
        return "stonk_like"
    return "other"


def venue_stage(venue: str) -> str:
    """Map venue to pre_grad vs post_grad stage tag."""
    v = (venue or "").lower()
    if v in ("stonkfun_launchlab", "pump_fun", "pump_like", "stonk_like"):
        return "pre_grad_dip"
    if v in ("raydium", "jupiter", "pump_amm", "other"):
        return "post_grad_dip"
    return "post_grad_dip"


def buyers_from_tx(res: dict, mint: str) -> list[tuple[str, float, str]]:
    """Return list of (owner, amount, venue) who gained mint."""
    meta = res.get("meta") or {}
    if meta.get("err"):
        return []
    msg = (res.get("transaction") or {}).get("message") or {}
    keys = account_keys(msg)
    venue = classify_venue(keys, meta.get("logMessages"))
    signers = []
    for a in msg.get("accountKeys") or []:
        if isinstance(a, dict) and a.get("signer"):
            signers.append(a["pubkey"])
    fee_payer = signers[0] if signers else (keys[0] if keys else None)
    pre = {(tb.get("owner"), tb.get("mint")): _tb_amount(tb) for tb in (meta.get("preTokenBalances") or [])}
    post = {(tb.get("owner"), tb.get("mint")): _tb_amount(tb) for tb in (meta.get("postTokenBalances") or [])}
    gained = []
    owners = {k[0] for k in list(pre) + list(post)}
    for o in owners:
        if not o or o in SKIP_WALLETS:
            continue
        dlt = post.get((o, mint), 0.0) - pre.get((o, mint), 0.0)
        if dlt > 0:
            gained.append((o, dlt, venue))
    if not gained and fee_payer and fee_payer not in SKIP_WALLETS:
        mint_seen = any(m == mint for (_, m) in list(pre) + list(post))
        if mint_seen:
            gained.append((fee_payer, 0.0, venue))
    ordered = []
    used = set()
    for o, d, v in gained:
        if o == fee_payer and o not in used:
            ordered.append((o, d, v))
            used.add(o)
    for o, d, v in gained:
        if o in signers and o not in used:
            ordered.append((o, d, v))
            used.add(o)
    for o, d, v in sorted(gained, key=lambda x: -x[1]):
        if o not in used:
            ordered.append((o, d, v))
            used.add(o)
    return ordered


# ---------- Mint discovery ----------

def load_seed_mints() -> list[dict]:
    """Reuse seed 74pB entry mints (late + curve) — avoid re-parsing seed."""
    out: list[dict] = []
    if not SEED_ENTRIES.exists():
        log("no seed_entries_raw.json — skip seed mint reuse")
        return out
    try:
        raw = json.loads(SEED_ENTRIES.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"seed entries read fail: {e}")
        return out
    mint_stats = raw.get("mint_stats") or {}
    for mint, st in mint_stats.items():
        mint = norm_sol(mint)
        if not mint or mint in SKIP_MINTS:
            continue
        venues = st.get("venues") or {}
        # prefer venues that suggest dump-dip (raydium/jup) OR curve dips (launchlab/pump)
        venue_top = max(venues, key=venues.get) if venues else "unknown"
        stage_hint = venue_stage(venue_top)
        out.append({
            "ca": mint,
            "symbol": mint[:8],
            "pool": None,
            "sources": ["seed_74pB"],
            "hot": 12.0 + float(st.get("n_buys") or 0) * 2.0,
            "stage_hint": stage_hint,
            "seed_first_buy_bt": st.get("first_buy_bt"),
            "seed_venue": venue_top,
            "n_seed_buys": st.get("n_buys") or 0,
        })
    log(f"seed mints reused={len(out)}")
    return out


def dex_info(mint: str) -> dict:
    data, err = http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
    if err or not isinstance(data, dict):
        return {"error": err or "no_data"}
    pairs = data.get("pairs") or []
    sol = [p for p in pairs if (p.get("chainId") or "").lower() == "solana"]
    if not sol:
        return {"pairs": 0}
    sol.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    p = sol[0]
    created = p.get("pairCreatedAt")
    created_ts = int(created / 1000) if isinstance(created, (int, float)) and created > 1e12 else (
        int(created) if isinstance(created, (int, float)) else None
    )
    chg = p.get("priceChange") or {}
    return {
        "symbol": (p.get("baseToken") or {}).get("symbol"),
        "name": (p.get("baseToken") or {}).get("name"),
        "pair": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "price_usd": _f(p.get("priceUsd")),
        "fdv": _f(p.get("fdv")),
        "mcap": _f(p.get("marketCap")),
        "liq_usd": _f((p.get("liquidity") or {}).get("usd")),
        "vol_h24": _f((p.get("volume") or {}).get("h24")),
        "chg_m5": _f(chg.get("m5")),
        "chg_h1": _f(chg.get("h1")),
        "chg_h6": _f(chg.get("h6")),
        "chg_h24": _f(chg.get("h24")),
        "pair_created_ts": created_ts,
        "url": p.get("url"),
        "pairs": len(sol),
    }


def fetch_dex_dump_bounce() -> list[dict]:
    """Find tokens with dump then bounce signatures via DexScreener boosts + search."""
    out: list[dict] = []
    urls = [
        ("https://api.dexscreener.com/token-boosts/top/v1", "dex_boosts"),
        ("https://api.dexscreener.com/token-boosts/latest/v1", "dex_boosts_latest"),
    ]
    seen: set[str] = set()
    for url, src in urls:
        data, err = http_get_json(url)
        if err or not isinstance(data, list):
            log(f"dex {src} err={err}")
            continue
        for row in data[:80]:
            if not isinstance(row, dict):
                continue
            if (row.get("chainId") or "").lower() != "solana":
                continue
            mint = norm_sol(row.get("tokenAddress"))
            if not mint or mint in SKIP_MINTS or mint in seen:
                continue
            seen.add(mint)
            out.append({
                "ca": mint,
                "symbol": (row.get("description") or mint[:8])[:48],
                "pool": None,
                "sources": [src],
                "hot": 6.0,
                "stage_hint": "post_grad_dip",
            })
        time.sleep(0.25)
    # search known dump-bounce style queries
    for q in ("WOJAK", "PEPE", "dump", "pump"):
        data, err = http_get_json(f"https://api.dexscreener.com/latest/dex/search?q={q}")
        if err or not isinstance(data, dict):
            continue
        for p in (data.get("pairs") or [])[:40]:
            if (p.get("chainId") or "").lower() != "solana":
                continue
            mint = norm_sol((p.get("baseToken") or {}).get("address"))
            if not mint or mint in SKIP_MINTS or mint in seen:
                continue
            chg = p.get("priceChange") or {}
            h1 = _f(chg.get("h1")) or 0
            h6 = _f(chg.get("h6")) or 0
            h24 = _f(chg.get("h24")) or 0
            # dump-bounce heuristic: recent dump OR mixed signs (h1 down, h6/h24 up)
            dumpish = (h1 <= -25) or (h6 <= -30) or (h1 < -15 and h6 > 20) or (h1 < -10 and h24 > 50)
            if not dumpish:
                continue
            seen.add(mint)
            sym = (p.get("baseToken") or {}).get("symbol") or mint[:8]
            if sym.upper() in MAJOR_SYMS:
                continue
            out.append({
                "ca": mint,
                "symbol": sym[:48],
                "pool": norm_sol(p.get("pairAddress")),
                "sources": [f"dex_search_{q}"],
                "hot": 8.0 + abs(min(h1, 0)) / 10.0,
                "stage_hint": "post_grad_dip",
                "chg_h1": h1,
                "chg_h6": h6,
                "chg_h24": h24,
                "liq_usd": _f((p.get("liquidity") or {}).get("usd")),
            })
        time.sleep(0.3)
    return out


def fetch_gecko_trending_dumps() -> list[dict]:
    out: list[dict] = []
    for page in (1, 2):
        data, err = http_get_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}"
        )
        if err or not isinstance(data, dict):
            log(f"gecko trending page={page} err={err}")
            break
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            attrs = row.get("attributes") or {}
            rel = row.get("relationships") or {}
            tid = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
            mint = norm_sol(tid.split("_", 1)[-1] if "_" in tid else tid)
            if not mint or mint in SKIP_MINTS:
                continue
            pool = attrs.get("address")
            if not pool and isinstance(row.get("id"), str) and "_" in row["id"]:
                pool = row["id"].split("_", 1)[-1]
            pool = norm_sol(pool) if pool else None
            name = attrs.get("name") or ""
            sym = name.split("/")[0].strip() if "/" in name else name
            if sym.upper() in MAJOR_SYMS:
                continue
            chg = attrs.get("price_change_percentage") or {}
            h1 = _f(chg.get("h1")) or 0
            h6 = _f(chg.get("h6")) or 0
            h24 = _f(chg.get("h24")) or 0
            dumpish = (h1 <= -20) or (h6 <= -25) or (h1 < -12 and (h6 > 15 or h24 > 40))
            bounce_hot = abs(min(h1, h6, 0)) / 8.0
            out.append({
                "ca": mint,
                "symbol": (sym[:48] or mint[:8]),
                "pool": pool,
                "sources": ["gecko_trending"],
                "hot": (10.0 if dumpish else 4.0) + bounce_hot,
                "stage_hint": "post_grad_dip",
                "chg_h1": h1,
                "chg_h6": h6,
                "chg_h24": h24,
                "dumpish": dumpish,
            })
        time.sleep(0.4)
    return out


def fetch_pump_graduated() -> list[dict]:
    """Graduated / near-grad pump tokens — good for both pre & post dip hunts."""
    out: list[dict] = []
    urls = [
        "https://frontend-api.pump.fun/coins?offset=0&limit=40&sort=last_trade_timestamp&order=DESC&includeNsfw=false",
        "https://frontend-api.pump.fun/coins/for-you?offset=0&limit=30&includeNsfw=false",
    ]
    for url in urls:
        data, err = http_get_json(url)
        if err:
            # try alt host
            alt = url.replace("frontend-api.pump.fun", "frontend-api-v3.pump.fun")
            data, err = http_get_json(alt)
        if err or data is None:
            log(f"pump api err={err}")
            continue
        rows = data if isinstance(data, list) else (data.get("coins") or data.get("data") or [])
        if not isinstance(rows, list):
            continue
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            mint = norm_sol(row.get("mint") or row.get("address"))
            if not mint or mint in SKIP_MINTS:
                continue
            complete = bool(row.get("complete") or row.get("raydium_pool"))
            sym = row.get("symbol") or mint[:8]
            if str(sym).upper() in MAJOR_SYMS:
                continue
            # bonding curve still open → pre_grad; completed → post_grad
            stage = "post_grad_dip" if complete else "pre_grad_dip"
            out.append({
                "ca": mint,
                "symbol": str(sym)[:48],
                "pool": norm_sol(row.get("raydium_pool") or row.get("bonding_curve")),
                "sources": ["pump_fun"],
                "hot": 7.0 + (3.0 if complete else 5.0),
                "stage_hint": stage,
                "pump_complete": complete,
            })
        time.sleep(0.3)
    return out


def merge_cas(rows: list[dict], cap: int) -> list[dict]:
    by: dict[str, dict] = {}
    for r in rows:
        ca = r.get("ca")
        if not ca:
            continue
        if ca not in by:
            by[ca] = dict(r)
            by[ca]["sources"] = list(r.get("sources") or [])
            continue
        cur = by[ca]
        cur["hot"] = max(float(cur.get("hot") or 0), float(r.get("hot") or 0))
        for s in r.get("sources") or []:
            if s not in cur["sources"]:
                cur["sources"].append(s)
        if r.get("pool") and not cur.get("pool"):
            cur["pool"] = r["pool"]
        if r.get("symbol") and (not cur.get("symbol") or cur["symbol"] == ca[:8]):
            cur["symbol"] = r["symbol"]
        # prefer explicit dumpish / seed
        if r.get("dumpish") or "seed_74pB" in (r.get("sources") or []):
            cur["hot"] = max(float(cur.get("hot") or 0), float(r.get("hot") or 0) + 2)
        if r.get("stage_hint") and "seed_74pB" in (r.get("sources") or []):
            cur["stage_hint"] = r["stage_hint"]
        for k in ("chg_h1", "chg_h6", "chg_h24", "seed_first_buy_bt", "seed_venue", "liq_usd"):
            if r.get(k) is not None and cur.get(k) is None:
                cur[k] = r[k]
    ranked = sorted(by.values(), key=lambda x: -float(x.get("hot") or 0))
    return ranked[:cap]


# ---------- OHLCV dump window detection ----------

def gecko_pool_for_mint(mint: str) -> dict:
    data, err = http_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
    )
    if err or not isinstance(data, dict):
        return {}
    rows = data.get("data") or []
    best = None
    best_res = -1.0
    for row in rows:
        attrs = row.get("attributes") or {}
        resv = _f(attrs.get("reserve_in_usd")) or 0.0
        if resv >= best_res:
            best_res = resv
            best = attrs
    if not best:
        return {}
    return {
        "pool": best.get("address"),
        "name": best.get("name"),
        "price_usd": _f(best.get("base_token_price_usd")),
        "reserve_usd": best_res,
        "chg": best.get("price_change_percentage") or {},
    }


def fetch_ohlcv(pool: str, aggregate: int = 15, limit: int = 100) -> list[list]:
    """Return ohlcv_list: [ts, open, high, low, close, volume]"""
    data, err = http_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute"
        f"?aggregate={aggregate}&limit={limit}&currency=usd"
    )
    if err or not isinstance(data, dict):
        return []
    attrs = (data.get("data") or {}).get("attributes") or {}
    return list(attrs.get("ohlcv_list") or [])


def detect_dump_windows(ohlcv: list[list], dump_pct: float = 0.35) -> list[dict]:
    """Find local peak → trough (≥dump_pct drop) → bounce windows.

    Returns windows with peak_ts, trough_ts, peak_px, trough_px, dump_pct,
    bounce_px (max after trough), dip_start/end for buyer sampling.
    """
    if len(ohlcv) < 8:
        return []
    # ensure chronological
    bars = sorted(ohlcv, key=lambda x: x[0])
    windows = []
    i = 2
    while i < len(bars) - 3:
        # local peak: high[i] >= neighbors
        peak_h = bars[i][2]
        if peak_h <= 0:
            i += 1
            continue
        if not (peak_h >= bars[i - 1][2] and peak_h >= bars[i + 1][2]):
            i += 1
            continue
        # look ahead for trough within next 40 bars
        trough_j = None
        trough_l = peak_h
        for j in range(i + 1, min(len(bars), i + 45)):
            low = bars[j][3]
            if low < trough_l:
                trough_l = low
                trough_j = j
            # stop if recovered above peak already without deep dump
            if trough_j is not None and bars[j][2] > peak_h * 0.95 and (peak_h - trough_l) / peak_h < dump_pct:
                break
        if trough_j is None or trough_l <= 0:
            i += 1
            continue
        drop = (peak_h - trough_l) / peak_h
        if drop < dump_pct:
            i += 1
            continue
        # bounce: max high after trough
        bounce_px = trough_l
        bounce_j = trough_j
        for j in range(trough_j, min(len(bars), trough_j + 40)):
            if bars[j][2] > bounce_px:
                bounce_px = bars[j][2]
                bounce_j = j
        bounce_pct = (bounce_px - trough_l) / trough_l if trough_l > 0 else 0
        if bounce_pct < 0.12:  # need some recovery to call it a profitable dip zone
            i = max(i + 1, trough_j)
            continue
        # dip buy window: from ~halfway dump to shortly after trough
        dip_start = bars[max(i, trough_j - 8)][0]
        dip_end = bars[min(len(bars) - 1, trough_j + 6)][0]
        windows.append({
            "peak_ts": bars[i][0],
            "trough_ts": bars[trough_j][0],
            "peak_px": peak_h,
            "trough_px": trough_l,
            "dump_pct": round(drop, 4),
            "bounce_px": bounce_px,
            "bounce_ts": bars[bounce_j][0],
            "bounce_pct": round(bounce_pct, 4),
            "dip_start": dip_start,
            "dip_end": dip_end,
        })
        i = max(i + 1, trough_j + 1)
    # dedupe overlapping (keep deepest dump)
    windows.sort(key=lambda w: -w["dump_pct"])
    kept = []
    used_spans = []
    for w in windows:
        span = (w["dip_start"], w["dip_end"])
        overlap = False
        for a, b in used_spans:
            if not (span[1] < a or span[0] > b):
                overlap = True
                break
        if overlap:
            continue
        used_spans.append(span)
        kept.append(w)
        if len(kept) >= 4:
            break
    return kept


def price_at(ohlcv: list[list], ts: int) -> float | None:
    if not ohlcv:
        return None
    best = None
    best_d = 10**18
    for bar in ohlcv:
        d = abs(bar[0] - ts)
        if d < best_d:
            best_d = d
            best = bar[4]  # close
    return best


# ---------- Dip buyer scan ----------

def sample_dip_buyers(
    mint: str,
    pool: str | None,
    windows: list[dict],
    ohlcv: list[list],
    tx_budget: int,
    sig_pages: int,
    known: set[str],
) -> list[dict]:
    """Parse txs in dip windows; return buyer rows with profit hints."""
    targets = []
    if pool:
        targets.append(pool)
    targets.append(mint)
    # also try pump bonding curve PDA — skip if unknown; mint+pool enough

    all_sigs: list[dict] = []
    if not windows:
        return []
    min_bt = min(w["dip_start"] for w in windows) - 600
    max_bt = max(w["dip_end"] for w in windows) + 600

    for target in targets[:2]:
        before = None
        for page in range(sig_pages):
            params: dict = {"limit": 1000}
            if before:
                params["before"] = before
            try:
                chunk = rpc("getSignaturesForAddress", [target, params]) or []
            except Exception as e:
                log(f"  sigs fail {target[:8]}: {e}")
                break
            if not chunk:
                break
            all_sigs.extend(chunk)
            before = chunk[-1]["signature"]
            oldest = min((s.get("blockTime") or 0) for s in chunk)
            if len(chunk) < 1000 or (oldest and oldest < min_bt):
                break
        if all_sigs:
            break

    in_win = [
        s for s in all_sigs
        if s.get("err") is None and min_bt <= (s.get("blockTime") or 0) <= max_bt
    ]
    # prefer closest to troughs
    troughs = [w["trough_ts"] for w in windows]

    def dist(s):
        bt = s.get("blockTime") or 0
        return min(abs(bt - t) for t in troughs)

    in_win.sort(key=dist)
    to_parse = in_win[:tx_budget]

    buyers: list[dict] = []
    seen_w: set[str] = set()
    for s in to_parse:
        try:
            res = rpc(
                "getTransaction",
                [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}],
            )
        except Exception:
            continue
        if not res:
            continue
        bt = s.get("blockTime") or res.get("blockTime") or 0
        # which window?
        win = None
        for w in windows:
            if w["dip_start"] - 120 <= bt <= w["dip_end"] + 120:
                win = w
                break
        if not win:
            continue
        ordered = buyers_from_tx(res, mint)
        entry_px = price_at(ohlcv, bt)
        synthetic = bool(win.get("synthetic"))
        for o, amt, venue in ordered[:3]:
            if o in seen_w or o in SKIP_WALLETS or o in known:
                continue
            if not norm_sol(o):
                continue
            if o == SEED_WALLET:
                continue  # seed is reference, not a hunt target
            seen_w.add(o)
            stage = venue_stage(venue)
            profit = False
            mult = None
            near_trough = False
            if entry_px and win.get("trough_px") and win["trough_px"] > 0 and not synthetic:
                near_trough = entry_px <= win["trough_px"] * 1.35
            elif synthetic:
                near_trough = True
            if not synthetic and entry_px and entry_px > 0 and win.get("bounce_px"):
                if (win.get("bounce_ts") or 0) >= bt or win["bounce_px"] > entry_px:
                    raw_mult = win["bounce_px"] / entry_px
                    if raw_mult <= 80:
                        mult = raw_mult
                        profit = mult >= 1.15
            if synthetic:
                # never mix synthetic px with real OHLCV entry_px
                profit = False
                mult = None
            if not profit and near_trough and not synthetic and win.get("bounce_pct", 0) >= 0.2:
                profit = True
                if mult is None and entry_px and win.get("bounce_px"):
                    raw_mult = win["bounce_px"] / entry_px
                    mult = raw_mult if raw_mult <= 80 else None
            if mult is not None:
                mult = round(min(float(mult), 50.0), 3)
            buyers.append({
                "address": o,
                "mint": mint,
                "amount": amt,
                "blockTime": bt,
                "sig": s["signature"],
                "venue": venue,
                "stage": stage,
                "entry_px": entry_px,
                "trough_px": win.get("trough_px"),
                "bounce_px": win.get("bounce_px"),
                "dump_pct": win.get("dump_pct"),
                "mult_est": mult,
                "profitable": profit,
                "near_trough": near_trough,
                "synthetic_window": synthetic,
            })
            if len(buyers) >= 60:
                return buyers
    return buyers


def wallet_recent_activity(addr: str, hours: float = 168.0) -> dict:
    min_bt = int(time.time()) - int(hours * 3600)
    try:
        sigs = rpc("getSignaturesForAddress", [addr, {"limit": 30}]) or []
    except Exception as e:
        return {"ok": False, "error": str(e)}
    ok = [s for s in sigs if s.get("err") is None]
    bts = [s.get("blockTime") or 0 for s in ok if s.get("blockTime")]
    recent = [bt for bt in bts if bt >= min_bt]
    return {
        "ok": True,
        "n_sigs_sample": len(ok),
        "n_recent": len(recent),
        "last_bt": max(bts) if bts else None,
        "active_in_window": len(recent) >= 2,
    }


def score_wallet(w: dict) -> float:
    n = int(w.get("n_mints") or 0)
    n_prof = int(w.get("n_profitable") or 0)
    stages = w.get("stages") or []
    mults = [m for m in (w.get("mults") or []) if m]
    avg_mult = sum(mults) / len(mults) if mults else 1.0
    avg_mult = min(avg_mult, 20.0)
    score = n * 6.0 + n_prof * 10.0 + min(avg_mult, 8.0) * 3.0
    if "pre_grad_dip" in stages and "post_grad_dip" in stages:
        score += 8.0  # versatile
    elif "pre_grad_dip" in stages:
        score += 3.0
    if w.get("active"):
        score += 5.0
    if int(w.get("n_buys") or 0) >= 4:
        score += 2.0
    return round(score, 3)


# ---------- Main hunt ----------

def hunt_once() -> dict:
    t0 = time.time()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ca_cap = max(6, min(60, env_int("DUMPDIP_CA_CAP", 28)))
    tx_per = max(8, min(80, env_int("DUMPDIP_TX_PER_CA", 40)))
    sig_pages = max(2, min(12, env_int("DUMPDIP_SIG_PAGES", 5)))
    min_mints = max(1, env_int("DUMPDIP_MIN_MINTS", 2))
    dump_pct = env_float("DUMPDIP_DUMP_PCT", 0.35)
    min_score = env_float("DUMPDIP_MIN_SCORE", 10.0)
    top_watch = max(5, min(60, env_int("DUMPDIP_TOP_WATCH", 30)))
    update_watch = os.environ.get("DUMPDIP_UPDATE_WATCH", "1").strip() not in ("0", "false", "no")
    exclude_stonk = os.environ.get("DUMPDIP_EXCLUDE_STONK", "0").strip() in ("1", "true", "yes")

    log(f"RPC={_rpc_url[0]} fallback={RPC_FALLBACK} dump_pct={dump_pct} ca_cap={ca_cap}")

    known: set[str] = set(SKIP_WALLETS)
    if exclude_stonk:
        known |= load_jsonl_addrs(OUT_DIR / "stonkfun_diggers.jsonl")
    # always exclude obvious hubs already in skip; do NOT wipe diggers by default
    # (dump-dip is complementary — same wallet can dig early AND buy dips)
    log(f"exclude_addrs={len(known)}")

    rows: list[dict] = []
    rows.extend(load_seed_mints())
    rows.extend(fetch_dex_dump_bounce())
    rows.extend(fetch_gecko_trending_dumps())
    rows.extend(fetch_pump_graduated())
    cas = merge_cas(rows, ca_cap)
    log(f"cas={len(cas)}")
    for i, c in enumerate(cas[:12]):
        log(
            f"  CA#{i+1} {c.get('symbol')} {c['ca'][:12]}… "
            f"hot={float(c.get('hot') or 0):.1f} stage={c.get('stage_hint')} "
            f"src={','.join(c.get('sources') or [])}"
        )

    by_wallet: dict[str, dict] = {}
    ca_stats: list[dict] = []
    mint_meta: dict[str, dict] = {}

    for ci, c in enumerate(cas):
        mint = c["ca"]
        sym = c.get("symbol") or mint[:8]
        log(f"[{ci+1}/{len(cas)}] {sym} {mint[:12]}… stage_hint={c.get('stage_hint')}")
        info = dex_info(mint)
        time.sleep(0.2)
        pool = c.get("pool") or info.get("pair")
        ginfo = {}
        try:
            ginfo = gecko_pool_for_mint(mint)
            if ginfo.get("pool") and not pool:
                pool = ginfo["pool"]
        except Exception as e:
            log(f"  gecko pool skip: {e}")
        time.sleep(0.25)

        ohlcv: list[list] = []
        windows: list[dict] = []
        if pool:
            try:
                ohlcv = fetch_ohlcv(pool, aggregate=15, limit=100)
                windows = detect_dump_windows(ohlcv, dump_pct=dump_pct)
            except Exception as e:
                log(f"  ohlcv fail: {e}")
            time.sleep(0.25)

        # Fallback windows from dex priceChange when OHLCV thin
        if not windows:
            h1 = info.get("chg_h1") or c.get("chg_h1") or 0
            h6 = info.get("chg_h6") or c.get("chg_h6") or 0
            now_ts = int(time.time())
            # synthetic: assume dump in last 1–6h then bounce
            if h1 <= -dump_pct * 100 or (h1 < -15 and h6 > 10):
                windows = [{
                    "peak_ts": now_ts - 3600 * 3,
                    "trough_ts": now_ts - 1800,
                    "peak_px": 1.0,
                    "trough_px": max(0.01, 1.0 + min(h1, 0) / 100.0),
                    "dump_pct": abs(min(h1, 0)) / 100.0,
                    "bounce_px": max(1.0, 1.0 + max(h6, 0) / 100.0),
                    "bounce_ts": now_ts,
                    "bounce_pct": max(0.15, max(h6, 0) / 100.0),
                    "dip_start": now_ts - 3600 * 2,
                    "dip_end": now_ts - 600,
                    "synthetic": True,
                }]
            elif c.get("seed_first_buy_bt"):
                # seed bought here — treat ±2h around seed buy as dip window candidate
                sbt = int(c["seed_first_buy_bt"])
                windows = [{
                    "peak_ts": sbt - 7200,
                    "trough_ts": sbt,
                    "peak_px": 1.0,
                    "trough_px": 0.5,
                    "dump_pct": 0.5,
                    "bounce_px": 1.2,
                    "bounce_ts": sbt + 7200,
                    "bounce_pct": 1.4,
                    "dip_start": sbt - 3600,
                    "dip_end": sbt + 1800,
                    "synthetic": True,
                    "from_seed": True,
                }]

        mint_meta[mint] = {
            "symbol": info.get("symbol") or sym,
            "pool": pool,
            "n_windows": len(windows),
            "ohlcv_bars": len(ohlcv),
            "stage_hint": c.get("stage_hint"),
            "sources": c.get("sources"),
            "chg_h1": info.get("chg_h1") or c.get("chg_h1"),
            "chg_h6": info.get("chg_h6") or c.get("chg_h6"),
            "liq_usd": info.get("liq_usd"),
        }

        if not windows:
            log(f"  no dump windows — skip buyer scan")
            ca_stats.append({**mint_meta[mint], "ca": mint, "n_buyers": 0})
            continue

        log(f"  windows={len(windows)} best_dump={windows[0]['dump_pct']:.0%} pool={str(pool)[:12] if pool else '-'}")
        try:
            buyers = sample_dip_buyers(
                mint, pool, windows, ohlcv, tx_per, sig_pages, known
            )
        except Exception as e:
            log(f"  buyer scan fail: {e}")
            buyers = []

        n_prof = sum(1 for b in buyers if b.get("profitable"))
        log(f"  buyers={len(buyers)} profitable={n_prof}")
        ca_stats.append({**mint_meta[mint], "ca": mint, "n_buyers": len(buyers), "n_profitable": n_prof})

        # cache per-mint
        (RAW_DIR / f"buyers_{mint[:16]}.json").write_text(
            json.dumps({"mint": mint, "windows": windows, "buyers": buyers}, indent=2, default=str),
            encoding="utf-8",
        )

        for b in buyers:
            a = b["address"]
            w = by_wallet.setdefault(a, {
                "address": a,
                "mints": [],
                "symbols": [],
                "stages": set(),
                "venues": Counter(),
                "n_buys": 0,
                "n_profitable": 0,
                "mults": [],
                "sigs": [],
                "last_bt": None,
                "first_bt": None,
                "entries": [],
            })
            w["n_buys"] += 1
            if b.get("profitable"):
                w["n_profitable"] += 1
            if b.get("mult_est"):
                w["mults"].append(b["mult_est"])
            if mint not in w["mints"]:
                w["mints"].append(mint)
            sym2 = mint_meta[mint].get("symbol") or sym
            if sym2 not in w["symbols"]:
                w["symbols"].append(sym2)
            w["stages"].add(b.get("stage") or "post_grad_dip")
            w["venues"][b.get("venue") or "other"] += 1
            w["sigs"].append(b.get("sig"))
            bt = b.get("blockTime")
            if bt:
                w["last_bt"] = max(w["last_bt"] or 0, bt)
                w["first_bt"] = min(w["first_bt"] or bt, bt)
            w["entries"].append({
                "mint": mint,
                "symbol": sym2,
                "stage": b.get("stage"),
                "venue": b.get("venue"),
                "profitable": b.get("profitable"),
                "mult_est": b.get("mult_est"),
                "dump_pct": b.get("dump_pct"),
                "blockTime": bt,
            })

    # activity check for multi-mint candidates
    candidates_raw = []
    for a, w in by_wallet.items():
        n_mints = len(w["mints"])
        if n_mints < 1:
            continue
        # require profitable on ≥1; multi-mint preferred
        if int(w["n_profitable"]) < 1 and n_mints < min_mints:
            continue
        act = {"ok": False}
        if n_mints >= min_mints or w["n_profitable"] >= 1:
            try:
                act = wallet_recent_activity(a, hours=168.0)
            except Exception:
                act = {"ok": False}
        stages = sorted(w["stages"]) if isinstance(w["stages"], set) else list(w["stages"])
        row = {
            "address": a,
            "n_mints": n_mints,
            "n_buys": w["n_buys"],
            "n_profitable": w["n_profitable"],
            "cas": w["mints"],
            "symbols": w["symbols"][:12],
            "stages": stages,
            "venues": dict(w["venues"]),
            "mults": w["mults"][:12],
            "avg_mult": round(sum(w["mults"]) / len(w["mults"]), 3) if w["mults"] else None,
            "active": bool(act.get("active_in_window")),
            "last_bt": w["last_bt"],
            "last_seen": (
                datetime.fromtimestamp(w["last_bt"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if w["last_bt"] else None
            ),
            "sample_sigs": (w["sigs"] or [])[:5],
            "entries": w["entries"][:8],
            "discovered_at": now_iso(),
            "chain": "solana",
            "source": "dump_dip",
            "tags": ["solana", "dump_dip"] + stages,
            "rpc": _rpc_url[0],
        }
        row["score"] = score_wallet(row)
        candidates_raw.append(row)

    candidates_raw.sort(key=lambda r: (r["score"], r["n_profitable"], r["n_mints"]), reverse=True)

    with RAW_ALL.open("w", encoding="utf-8") as f:
        for r in candidates_raw:
            # stages set already list
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    # publish: ≥min_mints profitable dump-dip OR strong single with high mult + active
    published = []
    for r in candidates_raw:
        n_m = int(r["n_mints"])
        n_p = int(r["n_profitable"])
        n_pre_mints = len({e.get("mint") for e in (r.get("entries") or []) if e.get("stage") == "pre_grad_dip"})
        r["n_pre_grad_mints"] = n_pre_mints
        if r["address"] == SEED_WALLET:
            continue
        if n_m >= min_mints and n_p >= 1 and float(r["score"]) >= min_score:
            published.append(r)
        elif n_pre_mints >= 3 and float(r["score"]) >= min_score:
            # repeated pre-grad curve dip buys (OHLCV often missing on LaunchLab)
            published.append(r)
        elif (
            n_p >= 1
            and n_m >= 1
            and (r.get("avg_mult") or 0) >= 1.5
            and r.get("active")
            and float(r["score"]) >= min_score + 4
        ):
            published.append(r)

    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in published:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    # merge watch — tag dump_dip; do not wipe other sources; never RH
    watch_new = [
        r for r in published
        if r["n_mints"] >= min_mints and r["score"] >= max(min_score, 14.0)
    ][:top_watch]

    merged_n = 0
    if update_watch:
        existing: list[dict] = []
        if OUT_CAND.exists():
            with OUT_CAND.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(o, dict) or not o.get("address"):
                        continue
                    # drop prior dump_dip rows we are refreshing; keep others
                    src = o.get("source") or ""
                    tags = o.get("tags") or []
                    if src == "dump_dip" or "dump_dip" in tags:
                        continue
                    existing.append(o)
        merged: list[dict] = []
        seen_a: set[str] = set()
        for r in watch_new:
            a = r["address"]
            if a in seen_a or a in known:
                continue
            seen_a.add(a)
            merged.append({
                "address": a,
                "score": r["score"],
                "n_cas": r["n_mints"],
                "cas": r["cas"],
                "symbols": r["symbols"],
                "stages": r["stages"],
                "n_profitable": r["n_profitable"],
                "avg_mult": r.get("avg_mult"),
                "discovered_at": r["discovered_at"],
                "chain": "solana",
                "source": "dump_dip",
                "tags": ["watch_candidate_sol", "dump_dip"] + list(r.get("stages") or []),
                "note": "dump-dip smart (pre_grad_dip / post_grad_dip) — NOT RH, not early-only digger",
            })
        for o in existing:
            a = o["address"]
            if a in seen_a:
                continue
            seen_a.add(a)
            merged.append(o)
        merged = merged[: max(top_watch + 40, 80)]
        with OUT_CAND.open("w", encoding="utf-8") as f:
            for r in merged:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        merged_n = len(merged)

    pre_n = sum(1 for r in published if "pre_grad_dip" in (r.get("stages") or []))
    post_n = sum(1 for r in published if "post_grad_dip" in (r.get("stages") or []))
    both_n = sum(
        1 for r in published
        if "pre_grad_dip" in (r.get("stages") or []) and "post_grad_dip" in (r.get("stages") or [])
    )

    lines = [
        f"# Solana dump-dip smart wallets — {jst_now_str()}",
        "",
        f"- RPC: `{_rpc_url[0]}`",
        f"- Dump threshold: **{dump_pct:.0%}** from local peak",
        f"- Stages: `pre_grad_dip` (LaunchLab / pump curve) · `post_grad_dip` (Raydium / Jupiter / pump AMM)",
        f"- CAs scanned: **{len(cas)}**",
        f"- Published wallets: **{len(published)}** (raw {len(candidates_raw)})",
        f"-  pre_grad_dip: **{pre_n}** · post_grad_dip: **{post_n}** · both: **{both_n}**",
        f"- Watch merge (tag dump_dip): **{merged_n}** total rows in watch_candidates_sol.jsonl",
        f"- RPC calls: **{_rpc_calls[0]}** · 429s: **{_rpc_429s[0]}**",
        f"- Elapsed: **{time.time() - t0:.1f}s**",
        f"- Seed 74pB mints reused: **{sum(1 for c in cas if 'seed_74pB' in (c.get('sources') or []))}**",
        "",
        "## CA scan (top)",
        "",
    ]
    for c in ca_stats[:20]:
        lines.append(
            f"- `{c.get('symbol')}` `{c['ca']}` buyers={c.get('n_buyers')} "
            f"prof={c.get('n_profitable')} win={c.get('n_windows')} "
            f"hint={c.get('stage_hint')} src={','.join(c.get('sources') or [])}"
        )
    lines += ["", "## Top wallets (JP-ready)", ""]
    for i, r in enumerate(published[:20]):
        stages = ",".join(r.get("stages") or [])
        lines.append(
            f"{i+1}. `{r['address']}` score={r['score']} mints={r['n_mints']} "
            f"prof={r['n_profitable']} mult≈{r.get('avg_mult')} "
            f"stages=[{stages}] syms={','.join(r.get('symbols') or [])[:60]} "
            f"active={r.get('active')} last={r.get('last_seen')}"
        )
    if not published:
        lines.append("_No wallets passed filters yet — try lower DUMPDIP_DUMP_PCT or more CAs._")
    lines += [
        "",
        "## Notes",
        "",
        "- Detects local peak→dump→bounce via Gecko OHLCV (or Dex priceChange / seed synthetic windows).",
        "- Samples buyers in dip windows on mint/pool (includes LaunchLab & pump curve = pre_grad_dip).",
        "- Profit heuristic: bounce after entry ≥1.15× or near-trough + bounce.",
        "- Separate from early-only StonkFun digger list; complementary strategy.",
        "- Never writes RH `wallets.jsonl`. Watch tag: `dump_dip`.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = {
        "updated_at": now_iso(),
        "rpc": _rpc_url[0],
        "dump_pct": dump_pct,
        "n_cas": len(cas),
        "n_published": len(published),
        "n_raw": len(candidates_raw),
        "n_pre_grad": pre_n,
        "n_post_grad": post_n,
        "n_both": both_n,
        "n_watch": merged_n,
        "rpc_calls": _rpc_calls[0],
        "rpc_429s": _rpc_429s[0],
        "elapsed_s": round(time.time() - t0, 2),
        "top": [
            {
                "address": r["address"],
                "score": r["score"],
                "n_mints": r["n_mints"],
                "stages": r.get("stages"),
                "symbols": r.get("symbols"),
            }
            for r in published[:20]
        ],
        "ca_stats": ca_stats,
        "source": "dump_dip",
        "chain": "solana",
    }
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    log(
        f"done published={len(published)} pre={pre_n} post={post_n} both={both_n} "
        f"watch={merged_n} rpc={_rpc_calls[0]} 429={_rpc_429s[0]} elapsed={time.time()-t0:.1f}s"
    )
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt dump-dip smart wallets (pre+post grad)")
    ap.add_argument("--once", action="store_true", help="Run once and exit")
    ap.add_argument("--daemon", action="store_true", help="Loop forever")
    args = ap.parse_args()
    daemon = args.daemon or os.environ.get("DUMPDIP_DAEMON", "").strip() in ("1", "true", "yes")
    once = args.once or os.environ.get("DUMPDIP_ONCE", "").strip() in ("1", "true", "yes") or not daemon

    if daemon and not once:
        interval = max(600, env_int("DUMPDIP_INTERVAL_SEC", 3600))
        log(f"daemon mode interval={interval}s")
        while True:
            try:
                hunt_once()
            except Exception as e:
                log(f"hunt error: {e}")
            log(f"sleep {interval}s")
            time.sleep(interval)
        return 0

    hunt_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

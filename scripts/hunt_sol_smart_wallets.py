#!/usr/bin/env python3
"""Solana-wide quality active smart-wallet discovery (NOT StonkFun-only).

Completely separate from StonkFun diggers:
  - NEVER write sol-wallets/stonkfun_* or rh-wallets/wallets.jsonl
  - Tags/source: solana_trend / chain=solana

Pipeline (free APIs only; LIVE_TRADING=0; no box GMGN):
  1) Trending / graduating CAs — DexScreener Solana boosts/profiles,
     GeckoTerminal solana trending (jina if 429), optionally pump.fun recent graduated
  2) Early/active buyers via Gecko pool trades (jina); optional light Solana RPC
  3) Exclude hubs / CEX / routers / stonkfun digger list (keep lists distinct)
  4) Score multi-CA early hits, recent activity, volume floors
  5) Write:
       sol-wallets/sol_smart_unknown.jsonl
       sol-wallets/summary_sol_smart_unknown.md
       sol-wallets/watch_candidates_sol.jsonl  (quality-gated)

Env:
  SOLANA_RPC_URL, SOL_TREND_CA_CAP (25), SOL_TREND_TOP_WATCH (25),
  SOL_TREND_MIN_SCORE (10), SOL_TREND_MIN_VOL (15),
  SOL_TREND_EXCLUDE_STONK=1, SOL_TREND_USE_PUMP=1,
  SOL_TREND_DAEMON=1 / SOL_TREND_INTERVAL_SEC=1800, SOL_TREND_ONCE=1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
UA = os.environ.get(
    "SOL_TREND_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/sol-smart; +https://github.com/ryryoooo/meme-signal-bot)",
)
RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
JINA = "https://r.jina.ai/http://"

OUT_DIR = ROOT / "sol-wallets"
OUT_JSONL = OUT_DIR / "sol_smart_unknown.jsonl"
OUT_MD = OUT_DIR / "summary_sol_smart_unknown.md"
OUT_CAND = OUT_DIR / "watch_candidates_sol.jsonl"
STATE_PATH = OUT_DIR / "raw" / "sol_smart_hunt_state.json"
RAW_ALL = OUT_DIR / "raw" / "sol_smart_unknown_all.jsonl"

# Quote / stable / wrapped — not meme CAs
SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USD1ttGY1N17NEEOGmBshDhNY0erjacQkMiQbmn6mP8",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # ETH wormhole
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "bSo13r4TkiE4KumL2BsLoAxnnYCuKQykLfQQUx1WEe",
    # Established majors / infra — not early-meme dig signals
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",  # PUMP token
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",  # PYTH
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",  # JITO
    "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",  # HNT
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektEV",  # ORCA
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",  # RENDER
}

# Programs / hubs / routers / CEX — never candidates
SKIP_WALLETS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM authority
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # CLMM
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # CPMM
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcCanq",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # pump.fun
    "pfeeUxB6jkeY1Hxd7CsFCGUgBfgf1G7BYnvyxZrBssu",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",  # LaunchLab
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",
    "WhirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca whirlpool
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca v1
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1",
    "routeUGWgWzqBWFcrCfv8tritsquVsuZ8bXPd1BXbXz",
    "MEViEnscUm6tsQRoGd9h6nLQaQspKj7DB2M5FwM3Xvz",
    "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNytnA4Dy",
    "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi7hCJOzii2tN",  # Tensor
    "5tzFkiKscXHK5ZXCGbXZoukaSfVnF3Y8R9o6t1w5hB9",  # Binance hot (example pattern)
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dQKvQT",  # Coinbase
    "2AQdpHJ2JpcEgPiATUXjpxFGXsfW4b9gZvzWcJPcQ9jX",
    "5VCwKtCXgCJ6kit5FybXjvriW3xEynqrB6EVKBEgt55",  # Binance
    "ASTyfSima4LLAdDgoFMkgmxrKrKn8RH5wRYnh66Wy4WC",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5",  # Kraken
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",  # Binance
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE",
    "5nnLqyxkEmgJhRn5nQVr8ouLm8q6xBqWKvQCk8T7u3vH",
}

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MAJOR_SYMS = {
    "RAY", "JUP", "PUMP", "BONK", "WIF", "PYTH", "JITO", "HNT", "ORCA", "RENDER",
    "SOL", "WSOL", "USDC", "USDT", "MSOL", "JITOSOL", "BSOL", "ETH", "BTC", "WBTC",
    "USDT", "UXD", "DAI", "USD1",
}


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
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} sol-smart {msg}", flush=True)


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
    if a.startswith("0x"):
        return None
    if _BASE58_RE.match(a):
        return a
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            wait = min(20.0, 1.2 * (2**attempt))
            if e.code in (429, 403, 502, 503, 504):
                time.sleep(wait)
                continue
            time.sleep(wait * 0.5)
        except Exception as e:
            last = type(e).__name__
            time.sleep(min(12.0, 1.0 * (2**attempt)))
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
    url = api_url
    if url.startswith("https://"):
        relay = JINA + url[len("https://") :]
    elif url.startswith("http://"):
        relay = JINA + url[len("http://") :]
    else:
        relay = JINA + url
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


def load_exclude_set() -> set[str]:
    """Hubs + optional stonkfun diggers (keep sol-wide list distinct)."""
    known: set[str] = set(SKIP_WALLETS)
    exclude_stonk = os.environ.get("SOL_TREND_EXCLUDE_STONK", "1").strip() not in (
        "0",
        "false",
        "no",
    )
    if exclude_stonk:
        known |= load_jsonl_addrs(OUT_DIR / "stonkfun_diggers.jsonl")
    # Prior sol-wide watch candidates are NOT excluded (we refresh them)
    return known


def _token_id_to_mint(token_id: str) -> str | None:
    if not isinstance(token_id, str):
        return None
    tid = token_id
    if "_" in tid:
        tid = tid.split("_", 1)[-1]
    return norm_sol(tid)


def fetch_gecko_trending() -> list[dict]:
    out: list[dict] = []
    for page in (1, 2):
        data, err = jina_get_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}"
        )
        if err or not isinstance(data, dict):
            log(f"gecko trending page={page} err={err}")
            break
        rows = data.get("data") or []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            attrs = row.get("attributes") or {}
            rel = row.get("relationships") or {}
            mint = _token_id_to_mint(((rel.get("base_token") or {}).get("data") or {}).get("id"))
            if not mint or mint in SKIP_MINTS:
                continue
            pool = attrs.get("address") or None
            if not pool and isinstance(row.get("id"), str) and "_" in row["id"]:
                pool = row["id"].split("_", 1)[-1]
            pool = norm_sol(pool) if pool else None
            name = attrs.get("name") or ""
            sym = name.split("/")[0].strip() if "/" in name else name
            vol = _f((attrs.get("volume_usd") or {}).get("h24")) or 0.0
            chg = _f((attrs.get("price_change_percentage") or {}).get("h24")) or 0.0
            out.append(
                {
                    "ca": mint,
                    "symbol": sym[:48] or mint[:8],
                    "pool": pool,
                    "vol_h24": vol,
                    "chg_h24": chg,
                    "created_at": attrs.get("pool_created_at"),
                    "sources": ["gecko_trending"],
                    "hot": vol / 1e5 + max(chg, 0) / 10.0 + 5.0,
                }
            )
        time.sleep(0.55)
    return out


def fetch_dex_sol_tokens() -> list[dict]:
    out: list[dict] = []
    for endpoint, src in (
        ("https://api.dexscreener.com/token-boosts/top/v1", "dex_boosts"),
        ("https://api.dexscreener.com/token-profiles/latest/v1", "dex_profiles"),
    ):
        data, err = jina_get_json(endpoint)
        if err:
            # try direct once
            data, err2 = direct_get_json(endpoint)
            if err2:
                log(f"{src} err={err}/{err2}")
                continue
        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            chain = (row.get("chainId") or "").lower()
            if chain != "solana":
                continue
            mint = norm_sol(row.get("tokenAddress") or row.get("token_address"))
            if not mint or mint in SKIP_MINTS:
                continue
            desc = row.get("description") or ""
            sym = (desc.split()[0] if desc else "")[:32] or mint[:8]
            amt = _f(row.get("totalAmount")) or 0.0
            out.append(
                {
                    "ca": mint,
                    "symbol": sym,
                    "pool": None,
                    "vol_h24": 0.0,
                    "chg_h24": 0.0,
                    "created_at": None,
                    "sources": [src],
                    "hot": (8.0 if src == "dex_boosts" else 4.0) + min(amt, 500) / 100.0,
                }
            )
        time.sleep(0.45)
    return out


def fetch_pump_graduated() -> list[dict]:
    """Recent completed pump.fun coins (free frontend API)."""
    if os.environ.get("SOL_TREND_USE_PUMP", "1").strip() in ("0", "false", "no"):
        return []
    out: list[dict] = []
    urls = [
        "https://frontend-api-v3.pump.fun/coins?offset=0&limit=30&sort=last_trade_timestamp&order=DESC&includeNsfw=false&complete=true",
        "https://frontend-api-v3.pump.fun/coins?offset=0&limit=20&sort=market_cap&order=DESC&includeNsfw=false&complete=true",
    ]
    seen: set[str] = set()
    for url in urls:
        data, err = direct_get_json(url, timeout=25)
        if err or not isinstance(data, list):
            log(f"pump.fun err={err}")
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            mint = norm_sol(row.get("mint"))
            if not mint or mint in SKIP_MINTS or mint in seen:
                continue
            mcap = _f(row.get("usd_market_cap")) or 0.0
            if mcap < 20_000:
                continue
            seen.add(mint)
            pool = norm_sol(row.get("raydium_pool") or row.get("pump_swap_pool"))
            out.append(
                {
                    "ca": mint,
                    "symbol": (row.get("symbol") or mint[:8])[:48],
                    "pool": pool,
                    "vol_h24": 0.0,
                    "chg_h24": 0.0,
                    "created_at": None,
                    "sources": ["pump_graduated"],
                    "hot": 6.0 + min(mcap, 2_000_000) / 200_000.0,
                    "mcap_usd": mcap,
                }
            )
        time.sleep(0.35)
    return out


def merge_cas(rows: list[dict], cap: int) -> list[dict]:
    by: dict[str, dict] = {}
    for r in rows:
        ca = r["ca"]
        sym_u = str(r.get("symbol") or "").upper().split()[0] if r.get("symbol") else ""
        if ca in SKIP_MINTS or sym_u in MAJOR_SYMS:
            continue
        if ca not in by:
            by[ca] = dict(r)
            by[ca]["sources"] = list(r.get("sources") or [])
            continue
        cur = by[ca]
        cur["hot"] = max(float(cur.get("hot") or 0), float(r.get("hot") or 0))
        cur["vol_h24"] = max(float(cur.get("vol_h24") or 0), float(r.get("vol_h24") or 0))
        a, b = float(cur.get("chg_h24") or 0), float(r.get("chg_h24") or 0)
        cur["chg_h24"] = b if abs(b) > abs(a) else a
        for s in r.get("sources") or []:
            if s not in cur["sources"]:
                cur["sources"].append(s)
        if not cur.get("pool") and r.get("pool"):
            cur["pool"] = r["pool"]
        if r.get("mcap_usd"):
            cur["mcap_usd"] = max(float(cur.get("mcap_usd") or 0), float(r["mcap_usd"]))
        if r.get("symbol") and (
            not cur.get("symbol") or str(cur["symbol"]).startswith(ca[:4])
        ):
            cur["symbol"] = r["symbol"]
    merged = sorted(by.values(), key=lambda x: float(x.get("hot") or 0), reverse=True)
    live, pump, other = [], [], []
    for r in merged:
        srcs = set(r.get("sources") or [])
        if srcs & {"gecko_trending", "dex_boosts", "dex_profiles"}:
            live.append(r)
        elif "pump_graduated" in srcs:
            pump.append(r)
        else:
            other.append(r)
    live_slots = max(cap // 2, min(len(live), cap))
    out = live[:live_slots]
    for bucket in (pump, other, live[live_slots:]):
        for r in bucket:
            if len(out) >= cap:
                break
            if r not in out:
                out.append(r)
        if len(out) >= cap:
            break
    return out[: max(5, cap)]


def resolve_pool_for_ca(ca: str) -> str | None:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{ca}/pools?page=1"
    )
    if err or not isinstance(data, dict):
        log(f"pools for {ca[:10]}… err={err}")
        return None
    rows = data.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None
    scored = []
    for row in rows:
        attrs = row.get("attributes") or {}
        rel = row.get("relationships") or {}
        base_id = (((rel.get("base_token") or {}).get("data") or {}).get("id") or "")
        is_base = 1 if ca in base_id else 0
        reserve = _f(attrs.get("reserve_in_usd")) or 0.0
        addr = attrs.get("address")
        if not addr and isinstance(row.get("id"), str) and "_" in row["id"]:
            addr = row["id"].split("_", 1)[-1]
        addr = norm_sol(addr)
        if addr:
            scored.append((is_base, reserve, addr))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def fetch_pool_buyers(pool: str, ca: str) -> list[dict]:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades"
    )
    if err or not isinstance(data, dict):
        log(f"trades pool={pool[:12]}… err={err}")
        return []
    rows = data.get("data") or []
    buyers: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        attrs = row.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        kind = (attrs.get("kind") or "").lower()
        to_tok = attrs.get("to_token_address") or ""
        from_tok = attrs.get("from_token_address") or ""
        is_buy = kind == "buy" or to_tok == ca
        if not is_buy:
            if from_tok == ca:
                continue
            if kind and kind != "buy":
                continue
        addr = norm_sol(
            attrs.get("tx_from_address") or attrs.get("from_address") or attrs.get("maker")
        )
        if not addr or addr in SKIP_WALLETS:
            continue
        vol = _f(attrs.get("volume_in_usd")) or 0.0
        buyers.append(
            {
                "address": addr,
                "vol_usd": vol,
                "ts": attrs.get("block_timestamp"),
                "block": attrs.get("block_number"),
                "tx": attrs.get("tx_hash"),
                "source": "gecko_pool_trades",
            }
        )
    return buyers


def score_candidate(row: dict, now: datetime) -> float:
    n_cas = int(row.get("n_cas") or 0)
    n_buys = int(row.get("n_buys") or 0)
    vol = float(row.get("sum_vol_usd") or 0)
    recent = 0.0
    last_ts = row.get("last_seen")
    if isinstance(last_ts, str) and last_ts:
        try:
            t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            age_h = (now - t).total_seconds() / 3600.0
            if age_h <= 6:
                recent = 14.0
            elif age_h <= 24:
                recent = 9.0
            elif age_h <= 72:
                recent = 4.0
            else:
                recent = 1.0
        except Exception:
            recent = 2.0
    multi = n_cas * 16.0 if n_cas >= 2 else (7.0 if n_cas == 1 else 0.0)
    depth = min(n_buys, 25) * 0.7
    dust_pen = 5.0 if vol > 0 and vol < 8 and n_cas < 2 else 0.0
    vol_term = min(vol, 8000) / 180.0
    best_early = row.get("best_early_rank") or 999
    early_boost = 10.0 if best_early <= 5 else (6.0 if best_early <= 12 else (2.0 if best_early <= 25 else 0.0))
    trend_boost = 2.5 * min(int(row.get("n_trend_cas") or 0), 5)
    pump_boost = 1.5 * min(int(row.get("n_pump_cas") or 0), 3)
    return multi + depth + recent + vol_term + early_boost + trend_boost + pump_boost - dust_pen


def hunt_once() -> dict:
    t0 = time.time()
    ca_cap = max(8, min(40, env_int("SOL_TREND_CA_CAP", 25)))
    top_watch = max(5, min(80, env_int("SOL_TREND_TOP_WATCH", 25)))
    min_score = env_float("SOL_TREND_MIN_SCORE", 10.0)
    min_vol = env_float("SOL_TREND_MIN_VOL", 15.0)

    log("loading exclude set (hubs + optional stonkfun diggers)…")
    known = load_exclude_set()
    log(f"exclude_addrs={len(known)}")

    log("fetching Solana trending CAs (jina / free)…")
    rows: list[dict] = []
    rows.extend(fetch_gecko_trending())
    rows.extend(fetch_dex_sol_tokens())
    rows.extend(fetch_pump_graduated())
    cas = merge_cas(rows, ca_cap)
    log(f"trending_cas={len(cas)} (cap={ca_cap})")
    for i, c in enumerate(cas[:12]):
        log(
            f"  CA#{i+1} {c.get('symbol')} {c['ca'][:12]}… "
            f"hot={float(c.get('hot') or 0):.1f} src={','.join(c.get('sources') or [])} "
            f"pool={'yes' if c.get('pool') else 'no'}"
        )

    by_wallet: dict[str, dict] = {}
    hub_counts: dict[str, int] = defaultdict(int)

    for i, meta in enumerate(cas):
        ca = meta["ca"]
        pool = meta.get("pool")
        log(f"[{i+1}/{len(cas)}] buyers for {meta.get('symbol')} {ca[:12]}…")
        buyers: list[dict] = []
        if not pool:
            pool = resolve_pool_for_ca(ca)
            meta["pool"] = pool
            time.sleep(0.5)
        if pool:
            buyers.extend(fetch_pool_buyers(pool, ca))
            time.sleep(0.55)

        seen_local: dict[str, int] = {}
        for b in buyers:
            addr = b["address"]
            hub_counts[addr] += 1
            if addr in known or addr in SKIP_WALLETS:
                continue
            if addr not in seen_local:
                seen_local[addr] = len(seen_local) + 1
            w = by_wallet.get(addr)
            if not w:
                w = {
                    "address": addr,
                    "cas": [],
                    "ca_meta": {},
                    "n_buys": 0,
                    "sum_vol_usd": 0.0,
                    "sources": set(),
                    "first_seen": None,
                    "last_seen": None,
                    "last_block": 0,
                    "first_seen_cas": None,
                    "early_ranks": [],
                }
                by_wallet[addr] = w
            w["n_buys"] += 1
            w["sum_vol_usd"] += float(b.get("vol_usd") or 0)
            w["sources"].add(b.get("source") or "unknown")
            if ca not in w["cas"]:
                w["cas"].append(ca)
                if w["first_seen_cas"] is None:
                    w["first_seen_cas"] = ca
                w["ca_meta"][ca] = {
                    "symbol": meta.get("symbol"),
                    "sources": list(meta.get("sources") or []),
                    "mcap_usd": meta.get("mcap_usd"),
                }
                w["early_ranks"].append(seen_local[addr])
            ts = b.get("ts")
            if isinstance(ts, str):
                if w["first_seen"] is None or ts < w["first_seen"]:
                    w["first_seen"] = ts
                if w["last_seen"] is None or ts > w["last_seen"]:
                    w["last_seen"] = ts
            blk = b.get("block")
            if isinstance(blk, int) and blk > (w["last_block"] or 0):
                w["last_block"] = blk

        log(
            f"  buyers_raw={len(buyers)} unique_new_on_ca={len(seen_local)} "
            f"wallet_pool={len(by_wallet)}"
        )

    hub_thresh = max(35, len(cas) * 7)
    hubs = {a for a, n in hub_counts.items() if n >= hub_thresh}
    if hubs:
        log(f"dropping hub/router-like addrs={len(hubs)} thresh={hub_thresh}")

    now = datetime.now(timezone.utc)
    candidates: list[dict] = []
    for addr, w in by_wallet.items():
        if addr in hubs or addr in known:
            continue
        n_cas = len(w["cas"])
        if n_cas < 1:
            continue
        best_early = min(w["early_ranks"]) if w["early_ranks"] else 999
        strong_single = n_cas == 1 and (
            w["n_buys"] >= 3
            or w["sum_vol_usd"] >= max(min_vol, 40)
            or best_early <= 12
        )
        if n_cas < 2 and not strong_single:
            continue
        # Aggregator / sniper bot across too many CAs this run
        if n_cas >= max(7, int(len(cas) * 0.35)):
            continue
        trend_cas = [
            c
            for c, m in w["ca_meta"].items()
            if any(
                s.startswith("gecko_") or s.startswith("dex_")
                for s in (m or {}).get("sources") or []
            )
        ]
        pump_cas = [
            c
            for c, m in w["ca_meta"].items()
            if "pump_graduated" in ((m or {}).get("sources") or [])
        ]
        row = {
            "address": addr,
            "score": 0.0,
            "n_cas": n_cas,
            "cas": w["cas"],
            "first_seen_cas": w["first_seen_cas"],
            "symbols": [((w["ca_meta"].get(c) or {}).get("symbol") or c[:8]) for c in w["cas"]],
            "n_buys": w["n_buys"],
            "sum_vol_usd": round(w["sum_vol_usd"], 4),
            "sources": sorted(w["sources"]),
            "first_seen": w["first_seen"],
            "last_seen": w["last_seen"],
            "last_block": w["last_block"] or None,
            "best_early_rank": best_early if best_early < 999 else None,
            "n_trend_cas": len(trend_cas),
            "n_pump_cas": len(pump_cas),
            "discovered_at": now.isoformat().replace("+00:00", "Z"),
            "chain": "solana",
            "source": "solana_trend",
            "tags": ["solana", "solana_trend", "smart_unknown"],
        }
        row["score"] = round(score_candidate(row, now), 3)
        if row["score"] < min_score and n_cas < 2:
            continue
        if n_cas < 2 and float(row["sum_vol_usd"]) < min_vol and (best_early or 999) > 8:
            continue
        candidates.append(row)

    candidates.sort(key=lambda r: (r["score"], r["n_cas"], r["n_buys"]), reverse=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_ALL.open("w", encoding="utf-8") as f:
        for r in candidates:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    published = []
    for r in candidates:
        n_cas = int(r.get("n_cas") or 0)
        if n_cas >= 2:
            published.append(r)
        elif (
            (r.get("best_early_rank") or 999) <= 8
            and float(r.get("sum_vol_usd") or 0) >= min_vol
            and int(r.get("n_buys") or 0) >= 3
        ):
            published.append(r)

    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in published:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    watch = [
        r
        for r in published
        if r["n_cas"] >= 2 and r["score"] >= max(min_score, 14.0) and float(r.get("sum_vol_usd") or 0) >= min_vol
    ][:top_watch]
    with OUT_CAND.open("w", encoding="utf-8") as f:
        for r in watch:
            f.write(
                json.dumps(
                    {
                        "address": r["address"],
                        "score": r["score"],
                        "n_cas": r["n_cas"],
                        "cas": r["cas"],
                        "symbols": r["symbols"],
                        "sum_vol_usd": r["sum_vol_usd"],
                        "sources": r["sources"],
                        "discovered_at": r["discovered_at"],
                        "chain": "solana",
                        "source": "solana_trend",
                        "tags": ["watch_candidate_sol", "solana_trend"],
                        "note": "NOT merged into stonkfun_diggers or RH wallets — collection only",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    jst_now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        f"# Solana-wide smart unknown wallets — {jst_now}",
        "",
        f"- Trending/graduating CAs scanned: **{len(cas)}**",
        f"- Excluded (hubs + stonkfun diggers): **{len(known)}**",
        f"- Unknown candidates (published): **{len(published)}** (full dump {len(candidates)} in raw/)",
        f"- Watch candidates (quality gate): **{len(watch)}** (not Discord-wired)",
        f"- Elapsed: **{time.time() - t0:.1f}s**",
        "",
        "## CA sources (top)",
        "",
    ]
    for c in cas[:20]:
        lines.append(
            f"- `{c.get('symbol')}` `{c['ca']}` hot={float(c.get('hot') or 0):.1f} "
            f"src={','.join(c.get('sources') or [])}"
        )
    lines += ["", "## Top 10 unknown addresses", ""]
    for i, r in enumerate(published[:10]):
        lines.append(
            f"{i+1}. `{r['address']}` score={r['score']} n_cas={r['n_cas']} "
            f"buys={r['n_buys']} vol=${r['sum_vol_usd']:.1f} "
            f"syms={','.join(r.get('symbols') or [])[:60]}"
        )
    if not published:
        lines.append("_No unknown candidates passed filters this run._")
    lines += [
        "",
        "## Notes",
        "",
        "- Buyers from Gecko Solana pool trades via jina; DexScreener boosts/profiles; optional pump.fun graduated.",
        "- Excludes CEX/router hubs and (by default) `stonkfun_diggers.jsonl` so lists stay distinct.",
        "- Outputs are **Solana-wide** (`solana_trend`) — never written into `stonkfun_*` or RH `wallets.jsonl`.",
        "- Discord not wired (collection only).",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = {
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "n_cas": len(cas),
        "n_exclude": len(known),
        "n_candidates": len(published),
        "n_candidates_all": len(candidates),
        "n_watch": len(watch),
        "top": [
            {"address": r["address"], "score": r["score"], "n_cas": r["n_cas"], "symbols": r.get("symbols")}
            for r in published[:15]
        ],
        "cas": [
            {"ca": c["ca"], "symbol": c.get("symbol"), "sources": c.get("sources"), "hot": c.get("hot")}
            for c in cas
        ],
        "elapsed_s": round(time.time() - t0, 2),
        "source": "solana_trend",
        "chain": "solana",
    }
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(
        f"done candidates={len(published)} watch={len(watch)} "
        f"cas={len(cas)} elapsed={time.time()-t0:.1f}s"
    )
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt Solana-wide smart wallets from trending CAs")
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    ap.add_argument("--daemon", action="store_true", help="loop")
    ap.add_argument("--interval", type=int, default=0, help="daemon interval sec")
    args = ap.parse_args()
    daemon = args.daemon or os.environ.get("SOL_TREND_DAEMON", "").strip() in ("1", "true", "yes")
    if os.environ.get("SOL_TREND_ONCE", "").strip() in ("1", "true", "yes"):
        daemon = False
    interval = args.interval or env_int("SOL_TREND_INTERVAL_SEC", 1800)

    if not daemon:
        hunt_once()
        return 0
    log(f"daemon interval={interval}s")
    while True:
        try:
            hunt_once()
        except Exception as e:
            log(f"hunt_once FAIL {type(e).__name__}: {e}")
        time.sleep(max(120, interval))


if __name__ == "__main__":
    sys.exit(main())

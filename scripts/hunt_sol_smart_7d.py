#!/usr/bin/env python3
"""Solana 7-day active smart-wallet hunt via official public RPC.

Separate from StonkFun diggers (never writes stonkfun_* / RH wallets.jsonl).

Pipeline (LIVE_TRADING=0, no box GMGN):
  1) Sample trending / boost / graduated mints (DexScreener, GeckoTerminal, pump.fun)
  2) Resolve pools (Dex / Gecko)
  3) getSignaturesForAddress over ~7d + getTransaction buyers (official RPC)
  4) Score multi-mint 7d activity; exclude hubs/CEX + stonkfun diggers
  5) Write sol_smart_7d_active.jsonl + summary + optional watch merge

Default RPC: https://api.mainnet-beta.solana.com (override SOLANA_RPC_URL)
"""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
UA = os.environ.get("SOL7D_HTTP_UA", "Mozilla/5.0 (compatible; meme-signal-bot/sol7d)")
RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com").strip()
JINA = "https://r.jina.ai/http://"

OUT_DIR = ROOT / "sol-wallets"
OUT_JSONL = OUT_DIR / "sol_smart_7d_active.jsonl"
OUT_MD = OUT_DIR / "summary_sol_smart_7d.md"
OUT_CAND = OUT_DIR / "watch_candidates_sol.jsonl"
STATE_PATH = OUT_DIR / "raw" / "sol_smart_7d_state.json"
RAW_ALL = OUT_DIR / "raw" / "sol_smart_7d_all.jsonl"
CACHE_DIR = OUT_DIR / "raw" / "sol7d_pools"

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "bSo13r4TkiE4KumL2BsLoAxnnYCuKQykLfQQUx1WEe",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
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
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcCanq",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pfeeUxB6jkeY1Hxd7CsFCGUgBfgf1G7BYnvyxZrBssu",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",
    "WhirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "5VCwKtCXgCJ6kit5FybXjvriW3xEynqrB6EVKBEgt55",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dQKvQT",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5",
}
_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MAJOR = {"RAY","JUP","PUMP","BONK","WIF","PYTH","JITO","HNT","ORCA","RENDER","SOL","WSOL","USDC","USDT","MSOL","ETH","BTC"}

_rpc_sleep = [0.0]
_rpc_429 = [0]
_rpc_n = [0]


def env_int(n, d):
    try: return int(float(os.environ.get(n, str(d))))
    except Exception: return d

def env_float(n, d):
    try: return float(os.environ.get(n, str(d)))
    except Exception: return d

def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} sol7d {msg}", flush=True)

def _f(v):
    try:
        return None if v is None or v == "" else float(v)
    except Exception:
        return None

def norm(a):
    if not isinstance(a, str): return None
    a = a.strip()
    return a if not a.startswith("0x") and _BASE58.match(a) else None

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def http_get(url, timeout=35.0, retries=4):
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            time.sleep(min(20, 1.2*(2**i)) if e.code in (429,403,502,503,504) else min(8, 0.6*(2**i)))
        except Exception as e:
            last = type(e).__name__; time.sleep(min(12, 1.0*(2**i)))
    return None, last

def extract_json(raw):
    if not raw: return None
    raw = raw.strip()
    try: return json.loads(raw)
    except Exception: pass
    content = raw
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[-1]
    content = re.sub(r"^```\w*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content)
    prefer = None
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, content)
        if not m: continue
        try:
            p = json.loads(m.group(0))
            if isinstance(p, dict) and "data" in p: return p
            if prefer is None and isinstance(p, (dict, list)): prefer = p
        except Exception: continue
    return prefer

def jina_json(url, timeout=40.0):
    relay = JINA + (url[8:] if url.startswith("https://") else url[7:] if url.startswith("http://") else url)
    raw, err = http_get(relay, timeout=timeout)
    if raw is None: return None, err or "empty"
    data = extract_json(raw)
    return (data, None) if data is not None else (None, "parse_fail")

def direct_json(url, timeout=30.0):
    raw, err = http_get(url, timeout=timeout, retries=3)
    if raw is None: return None, err or "empty"
    try: return json.loads(raw), None
    except Exception:
        data = extract_json(raw)
        return (data, None) if data is not None else (None, "parse_fail")

def load_addrs(path, key="address"):
    out = set()
    if not path.exists(): return out
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: o = json.loads(line)
        except Exception: continue
        a = norm(o.get(key) or o.get("wallet")) if isinstance(o, dict) else None
        if a: out.add(a)
    return out

def load_exclude():
    known = set(SKIP_WALLETS)
    if os.environ.get("SOL7D_EXCLUDE_STONK", "1").strip() not in ("0", "false", "no"):
        known |= load_addrs(OUT_DIR / "stonkfun_diggers.jsonl")
    return known

def rpc(method, params, timeout=50):
    base = env_float("SOL7D_RPC_SLEEP", 0.2)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last = None
    for attempt in range(10):
        gap = base + min(8.0, _rpc_sleep[0])
        if gap > 0: time.sleep(gap)
        try:
            req = urllib.request.Request(RPC_URL, data=payload.encode(), headers={"content-type": "application/json", "User-Agent": UA}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode() or "{}")
            _rpc_n[0] += 1
            if body.get("error"):
                err = body["error"]; msg = str(err); code = err.get("code") if isinstance(err, dict) else None
                if code in (-32005, 429) or "429" in msg or "rate" in msg.lower() or "Too many" in msg:
                    _rpc_429[0] += 1
                    _rpc_sleep[0] = min(12.0, max(0.4, _rpc_sleep[0]*1.6) + 0.3)
                    time.sleep(min(25.0, 0.8*(2**attempt))); continue
                raise RuntimeError(err)
            _rpc_sleep[0] = max(0.0, _rpc_sleep[0]*0.85 - 0.02)
            return body.get("result")
        except urllib.error.HTTPError as e:
            last = e; _rpc_n[0] += 1
            if e.code == 429:
                _rpc_429[0] += 1
                _rpc_sleep[0] = min(15.0, max(0.5, _rpc_sleep[0]*1.8) + 0.5)
                time.sleep(min(30.0, 1.0*(2**attempt))); continue
            time.sleep(min(12.0, 0.5*(2**attempt)))
        except Exception as e:
            last = e; time.sleep(min(10.0, 0.4*(1.7**attempt)))
    r = subprocess.run(["curl","-sS","-m",str(timeout),"-A",UA,"-X","POST",RPC_URL,"-H","content-type: application/json","-d",payload], capture_output=True, text=True)
    _rpc_n[0] += 1
    if r.returncode == 0 and r.stdout.strip():
        body = json.loads(r.stdout)
        if body.get("error"): raise RuntimeError(body["error"])
        return body.get("result")
    raise RuntimeError(f"RPC {method} failed: {last} / {r.stderr}")

def mint_from_tid(tid):
    if not isinstance(tid, str): return None
    return norm(tid.split("_", 1)[-1] if "_" in tid else tid)

def fetch_gecko():
    out = []
    for page in (1, 2, 3):
        data, err = jina_json(f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}")
        if err or not isinstance(data, dict):
            data, err2 = direct_json(f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}")
            if err2 or not isinstance(data, dict):
                log(f"gecko page={page} err={err}/{err2}"); break
        rows = data.get("data") or []
        if not rows: break
        for row in rows:
            if not isinstance(row, dict): continue
            attrs = row.get("attributes") or {}; rel = row.get("relationships") or {}
            mint = mint_from_tid(((rel.get("base_token") or {}).get("data") or {}).get("id"))
            if not mint or mint in SKIP_MINTS: continue
            pool = attrs.get("address")
            if not pool and isinstance(row.get("id"), str) and "_" in row["id"]:
                pool = row["id"].split("_", 1)[-1]
            pool = norm(pool) if pool else None
            name = attrs.get("name") or ""
            sym = name.split("/")[0].strip() if "/" in name else name
            vol = _f((attrs.get("volume_usd") or {}).get("h24")) or 0.0
            chg = _f((attrs.get("price_change_percentage") or {}).get("h24")) or 0.0
            out.append({"ca": mint, "symbol": (sym[:48] or mint[:8]), "pool": pool, "sources": ["gecko_trending"], "hot": vol/1e5 + max(chg,0)/10 + 5})
        time.sleep(0.4)
    return out

def fetch_dex():
    out = []
    for url, src in (("https://api.dexscreener.com/token-boosts/top/v1","dex_boosts"), ("https://api.dexscreener.com/token-profiles/latest/v1","dex_profiles")):
        data, err = direct_json(url)
        if err:
            data, err2 = jina_json(url)
            if err2: log(f"{src} err={err}/{err2}"); continue
        for row in (data if isinstance(data, list) else []):
            if not isinstance(row, dict) or (row.get("chainId") or "").lower() != "solana": continue
            mint = norm(row.get("tokenAddress") or row.get("token_address"))
            if not mint or mint in SKIP_MINTS: continue
            desc = row.get("description") or ""
            sym = (desc.split()[0] if desc else "")[:32] or mint[:8]
            amt = _f(row.get("totalAmount")) or 0.0
            out.append({"ca": mint, "symbol": sym, "pool": None, "sources": [src], "hot": (8 if src=="dex_boosts" else 4) + min(amt,500)/100})
        time.sleep(0.35)
    return out

def dex_pair(mint):
    data, err = direct_json(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", timeout=20)
    if err or not isinstance(data, list) or not data: return None
    best, liq = None, -1.0
    for p in data:
        pair = norm(p.get("pairAddress")); L = _f((p.get("liquidity") or {}).get("usd")) or 0.0
        if pair and L >= liq: best, liq = pair, L
    return best

def fetch_pump():
    if os.environ.get("SOL7D_USE_PUMP", "1").strip() in ("0","false","no"): return []
    out, seen = [], set()
    for url in (
        "https://frontend-api-v3.pump.fun/coins?offset=0&limit=40&sort=last_trade_timestamp&order=DESC&includeNsfw=false&complete=true",
        "https://frontend-api-v3.pump.fun/coins?offset=0&limit=30&sort=market_cap&order=DESC&includeNsfw=false&complete=true",
    ):
        data, err = direct_json(url, timeout=25)
        if err or not isinstance(data, list): log(f"pump err={err}"); continue
        for row in data:
            if not isinstance(row, dict): continue
            mint = norm(row.get("mint"))
            if not mint or mint in SKIP_MINTS or mint in seen: continue
            mcap = _f(row.get("usd_market_cap")) or 0.0
            if mcap < 15000: continue
            seen.add(mint)
            out.append({"ca": mint, "symbol": (row.get("symbol") or mint[:8])[:48], "pool": norm(row.get("raydium_pool") or row.get("pump_swap_pool")), "sources": ["pump_graduated"], "hot": 6 + min(mcap, 2e6)/2e5, "mcap_usd": mcap})
        time.sleep(0.3)
    return out

def merge_cas(rows, cap):
    by = {}
    for r in rows:
        ca = r["ca"]; sym = str(r.get("symbol") or "").upper().split()[0] if r.get("symbol") else ""
        if ca in SKIP_MINTS or sym in MAJOR: continue
        if ca not in by:
            by[ca] = dict(r); by[ca]["sources"] = list(r.get("sources") or []); continue
        cur = by[ca]
        cur["hot"] = max(float(cur.get("hot") or 0), float(r.get("hot") or 0))
        for s in r.get("sources") or []:
            if s not in cur["sources"]: cur["sources"].append(s)
        if not cur.get("pool") and r.get("pool"): cur["pool"] = r["pool"]
        if r.get("symbol") and (not cur.get("symbol") or str(cur["symbol"]).startswith(ca[:4])):
            cur["symbol"] = r["symbol"]
    return sorted(by.values(), key=lambda x: float(x.get("hot") or 0), reverse=True)[:max(1, cap)]

def resolve_pool(ca):
    p = dex_pair(ca)
    if p: return p
    data, err = jina_json(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{ca}/pools?page=1")
    if err or not isinstance(data, dict):
        data, err = direct_json(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{ca}/pools?page=1")
    if err or not isinstance(data, dict): return None
    scored = []
    for row in data.get("data") or []:
        attrs = row.get("attributes") or {}; rel = row.get("relationships") or {}
        base = (((rel.get("base_token") or {}).get("data") or {}).get("id") or "")
        addr = attrs.get("address")
        if not addr and isinstance(row.get("id"), str) and "_" in row["id"]: addr = row["id"].split("_",1)[-1]
        addr = norm(addr)
        if addr: scored.append((1 if ca in base else 0, _f(attrs.get("reserve_in_usd")) or 0.0, addr))
    if not scored: return None
    scored.sort(reverse=True); return scored[0][2]

def fetch_sigs(pool, max_pages, min_bt):
    before, all_sigs = None, []
    for page in range(max_pages):
        params = {"limit": 1000}
        if before: params["before"] = before
        try: sigs = rpc("getSignaturesForAddress", [pool, params]) or []
        except Exception as e:
            log(f"sigs fail {pool[:10]}… p={page}: {e}"); break
        if not sigs: break
        all_sigs.extend(sigs)
        oldest = min((s.get("blockTime") or 0) for s in sigs)
        before = sigs[-1]["signature"]
        if len(sigs) < 1000 or (oldest and oldest < min_bt): break
    return [s for s in all_sigs if (s.get("blockTime") or 0) >= min_bt and s.get("err") is None]

def tb_amt(tb):
    ui = tb.get("uiTokenAmount") or {}
    try:
        if ui.get("uiAmount") is not None: return float(ui["uiAmount"])
        return float(ui.get("uiAmountString") or 0)
    except Exception: return 0.0

def buyers_from_tx(res, mint, skip, pool=None):
    meta = res.get("meta") or {}
    if meta.get("err"): return []
    msg = (res.get("transaction") or {}).get("message") or {}
    aks = msg.get("accountKeys") or []
    signers = [a["pubkey"] for a in aks if isinstance(a, dict) and a.get("signer")]
    if not signers and aks and isinstance(aks[0], str): signers = [aks[0]]
    fee = signers[0] if signers else None
    local = set(skip); 
    if pool: local.add(pool)
    pre = {(tb.get("owner"), tb.get("mint")): tb_amt(tb) for tb in (meta.get("preTokenBalances") or [])}
    post = {(tb.get("owner"), tb.get("mint")): tb_amt(tb) for tb in (meta.get("postTokenBalances") or [])}
    gained = []
    for o in {k[0] for k in list(pre)+list(post)}:
        if not o or o in local: continue
        d = post.get((o, mint), 0.0) - pre.get((o, mint), 0.0)
        if d > 0: gained.append((o, d))
    if not gained and fee and fee not in local:
        mint_seen = any(m == mint for (_, m) in list(pre)+list(post))
        wsol = "So11111111111111111111111111111111111111112"
        spent = pre.get((fee, wsol), 0.0) - post.get((fee, wsol), 0.0)
        try: drop = ((meta.get("preBalances") or [0])[0] - (meta.get("postBalances") or [0])[0]) / 1e9
        except Exception: drop = 0.0
        if mint_seen and (spent > 0.001 or drop > 0.005): gained.append((fee, max(spent, drop)))
    if not gained: return []
    ordered, used = [], set()
    for o,d in gained:
        if o == fee and o not in used: ordered.append((o,d,fee)); used.add(o)
    for o,d in gained:
        if o in signers and o not in used: ordered.append((o,d,fee)); used.add(o)
    for o,d in sorted(gained, key=lambda x: -x[1]):
        if o not in used: ordered.append((o,d,fee)); used.add(o)
    return ordered

def select_sigs(sigs, budget):
    if not sigs: return []
    ordered = sorted(sigs, key=lambda x: (x.get("blockTime") or 0, x.get("signature") or ""))
    recent_n = max(10, (budget*3)//4); early_n = max(4, budget - recent_n)
    recent = list(reversed(ordered))[:recent_n]; early = ordered[:early_n]
    seen, out = set(), []
    for s in recent + early:
        sig = s.get("signature")
        if not sig or sig in seen: continue
        seen.add(sig); out.append(s)
        if len(out) >= budget: break
    return out

def hunt_ca(meta, window_sec, max_pages, tx_budget, skip):
    ca = meta["ca"]; pool = meta.get("pool")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{ca}.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            age = time.time() - float(c.get("cached_at_unix") or 0)
            ttl = 300 if not (c.get("buyers") or []) else 7200
            if age < ttl and c.get("buyers") is not None and c.get("tx_budget") == tx_budget:
                return c.get("buyers") or []
        except Exception: pass
    if not pool:
        pool = resolve_pool(ca); meta["pool"] = pool; time.sleep(0.25)
    if not pool:
        log(f"  no pool for {meta.get('symbol')} {ca[:12]}…"); return []
    min_bt = int(time.time()) - window_sec
    sigs = fetch_sigs(pool, max_pages, min_bt)
    log(f"  pool={pool[:12]}… sigs_7d={len(sigs)}")
    if len(sigs) < 3:
        try:
            sm = fetch_sigs(ca, min(3, max_pages), min_bt)
            if len(sm) > len(sigs): sigs = sm; log(f"  mint-sigs={len(sigs)}")
        except Exception: pass
    if not sigs: return []
    to_parse = select_sigs(sigs, tx_budget)
    buyers, seen, rank = [], set(), 0
    for s in to_parse:
        try:
            res = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}], timeout=45)
        except Exception as e:
            log(f"  tx fail {s['signature'][:10]}…: {type(e).__name__}"); continue
        if not res: continue
        ordered = buyers_from_tx(res, ca, skip, pool=pool)
        if not ordered: continue
        wallet, dlt, fee = ordered[0]
        if wallet in seen or wallet in skip:
            picked = next(((w,d,f) for w,d,f in ordered if w not in seen and w not in skip), None)
            if not picked: continue
            wallet, dlt, fee = picked
        seen.add(wallet); rank += 1
        bt = s.get("blockTime")
        buyers.append({
            "address": wallet, "delta": dlt, "sig": s["signature"], "blockTime": bt,
            "ts": datetime.fromtimestamp(bt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if bt else None,
            "fee_payer": fee, "early_rank": rank, "source": "solana_rpc_7d",
        })
    try:
        cache.write_text(json.dumps({"ca": ca, "symbol": meta.get("symbol"), "pool": pool, "tx_budget": tx_budget, "buyers": buyers, "n_sigs": len(sigs), "cached_at": now_iso(), "cached_at_unix": time.time()}, ensure_ascii=False) + "\n")
    except Exception: pass
    return buyers

def score_row(row, now):
    n_cas = int(row.get("n_cas") or 0); n_buys = int(row.get("n_buys") or 0)
    recent = 2.0
    ls = row.get("last_seen")
    if isinstance(ls, str) and ls:
        try:
            age = (now - datetime.fromisoformat(ls.replace("Z", "+00:00"))).total_seconds()/3600
            recent = 16 if age <= 12 else (10 if age <= 48 else (5 if age <= 168 else 1))
        except Exception: pass
    multi = n_cas*18 if n_cas >= 2 else (8 if n_cas == 1 else 0)
    depth = min(n_buys, 40)*0.85
    be = row.get("best_early_rank") or 999
    early = 12 if be <= 5 else (7 if be <= 15 else (3 if be <= 30 else 0))
    span = 0
    fs = row.get("first_seen")
    if isinstance(fs, str) and isinstance(ls, str) and fs and ls:
        try:
            a = datetime.fromisoformat(fs.replace("Z","+00:00")); b = datetime.fromisoformat(ls.replace("Z","+00:00"))
            h = abs((b-a).total_seconds())/3600; span = 6 if h >= 24 else (3 if h >= 6 else 0)
        except Exception: pass
    return multi + depth + recent + early + span

def write_outputs(by_wallet, known, cas, ca_stats, window_sec, min_cas, min_score, top_watch, update_watch, t0, partial=False):
    hubs = {a for a,w in by_wallet.items() if len(w["cas"]) >= max(10, int(len(cas)*0.4))}
    now = datetime.now(timezone.utc)
    candidates = []
    for addr, w in by_wallet.items():
        if addr in hubs or addr in known: continue
        n_cas = len(w["cas"]); be = min(w["early_ranks"]) if w["early_ranks"] else 999
        if n_cas < min_cas and not (n_cas == 1 and w["n_buys"] >= 2 and be <= 12): continue
        if n_cas >= max(10, int(len(cas)*0.4)): continue
        row = {
            "address": addr, "score": 0.0, "n_cas": n_cas, "cas": w["cas"],
            "symbols": [((w["ca_meta"].get(c) or {}).get("symbol") or c[:8]) for c in w["cas"]],
            "n_buys": w["n_buys"], "sources": sorted(w["sources"]),
            "first_seen": w["first_seen"], "last_seen": w["last_seen"],
            "best_early_rank": be if be < 999 else None, "sample_sigs": w["sigs"][:5],
            "window_days": round(window_sec/86400, 2),
            "discovered_at": now.isoformat().replace("+00:00","Z"),
            "chain": "solana", "source": "solana_rpc_7d",
            "tags": ["solana","solana_rpc_7d","smart_active_7d"] + (["partial"] if partial else []),
            "rpc": RPC_URL,
        }
        row["score"] = round(score_row(row, now), 3)
        if row["score"] < min_score and n_cas < 2: continue
        candidates.append(row)
    candidates.sort(key=lambda r: (r["score"], r["n_cas"], r["n_buys"]), reverse=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True); STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_ALL.open("w", encoding="utf-8") as f:
        for r in candidates: f.write(json.dumps(r, ensure_ascii=False)+"\n")
    published = []
    for r in candidates:
        if r["n_cas"] >= 2: published.append(r)
        elif (r.get("best_early_rank") or 999) <= 8 and r["score"] >= min_score:
            published.append(r)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in published: f.write(json.dumps(r, ensure_ascii=False)+"\n")
    watch_new = [r for r in published if r["n_cas"] >= 2 and r["score"] >= max(min_score, 18)][:top_watch]
    merged = watch_new
    if update_watch:
        existing = []
        if OUT_CAND.exists():
            for line in OUT_CAND.open(encoding="utf-8"):
                line = line.strip()
                if not line: continue
                try: o = json.loads(line)
                except Exception: continue
                if isinstance(o, dict) and o.get("address") and o.get("source") != "solana_rpc_7d":
                    existing.append(o)
        merged, seen = [], set()
        for r in watch_new:
            if r["address"] in seen or r["address"] in known: continue
            seen.add(r["address"])
            merged.append({"address": r["address"], "score": r["score"], "n_cas": r["n_cas"], "cas": r["cas"], "symbols": r["symbols"], "sources": r["sources"], "discovered_at": r["discovered_at"], "chain": "solana", "source": "solana_rpc_7d", "tags": ["watch_candidate_sol","solana_rpc_7d"], "note": "7d RPC hunt — NOT merged into stonkfun/RH"})
        for o in existing:
            if o["address"] in seen or o["address"] in known: continue
            seen.add(o["address"]); merged.append(o)
        merged = merged[:max(top_watch, 50)]
        with OUT_CAND.open("w", encoding="utf-8") as f:
            for r in merged: f.write(json.dumps(r, ensure_ascii=False)+"\n")
    jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    tag = " (partial)" if partial else ""
    lines = [f"# Solana 7d active smart wallets (official RPC){tag} — {jst}", "",
             f"- RPC: `{RPC_URL}`", f"- Window: **{window_sec/86400:.0f} days**",
             f"- CAs scanned / done: **{len(cas)}** / **{len(ca_stats)}**",
             f"- Excluded: **{len(known)}**", f"- Published: **{len(published)}** (raw {len(candidates)})",
             f"- Watch (merged): **{len(merged)}**", f"- RPC calls: **{_rpc_n[0]}** · 429s: **{_rpc_429[0]}**",
             f"- Elapsed: **{time.time()-t0:.1f}s**", "", "## CA scan", ""]
    for c in ca_stats[:30]:
        lines.append(f"- `{c.get('symbol')}` `{c['ca']}` buyers={c.get('n_buyers')} src={','.join(c.get('sources') or [])}")
    lines += ["", "## Top 20", ""]
    for i, r in enumerate(published[:20]):
        lines.append(f"{i+1}. `{r['address']}` score={r['score']} n_cas={r['n_cas']} buys={r['n_buys']} early={r.get('best_early_rank')} syms={','.join(r.get('symbols') or [])[:70]} last={r.get('last_seen')}")
    if not published: lines.append("_none yet_")
    lines += ["", "## Notes", "", "- Official Solana RPC getSignaturesForAddress + getTransaction ~7d.",
              "- CA discovery: DexScreener / GeckoTerminal / pump.fun.",
              "- Excludes hubs + stonkfun_diggers. Never into stonkfun_*/RH wallets.", ""]
    OUT_MD.write_text("\n".join(lines)+"\n", encoding="utf-8")
    state = {"updated_at": now.isoformat().replace("+00:00","Z"), "partial": partial, "rpc": RPC_URL,
             "window_sec": window_sec, "n_cas": len(cas), "n_cas_done": len(ca_stats), "n_exclude": len(known),
             "n_published": len(published), "n_candidates_all": len(candidates), "n_watch": len(merged),
             "rpc_calls": _rpc_n[0], "rpc_429s": _rpc_429[0],
             "top": [{"address": r["address"], "score": r["score"], "n_cas": r["n_cas"], "symbols": r.get("symbols")} for r in published[:20]],
             "ca_stats": ca_stats, "elapsed_s": round(time.time()-t0, 2), "source": "solana_rpc_7d", "chain": "solana"}
    STATE_PATH.write_text(json.dumps(state, indent=2)+"\n", encoding="utf-8")
    log(f"{'checkpoint' if partial else 'done'} published={len(published)} watch={len(merged)} cas={len(ca_stats)}/{len(cas)} rpc={_rpc_n[0]} 429={_rpc_429[0]} elapsed={time.time()-t0:.1f}s")
    return state

def hunt_once():
    t0 = time.time()
    ca_cap = max(3, min(120, env_int("SOL7D_CA_CAP", 48)))
    tx_per = max(8, min(150, env_int("SOL7D_TX_PER_CA", 28)))
    sig_pages = max(2, min(20, env_int("SOL7D_SIG_PAGES", 5)))
    min_cas = max(1, env_int("SOL7D_MIN_CAS", 2))
    min_score = env_float("SOL7D_MIN_SCORE", 12.0)
    top_watch = max(5, min(80, env_int("SOL7D_TOP_WATCH", 40)))
    window_sec = max(86400, env_int("SOL7D_WINDOW_SEC", 604800))
    update_watch = os.environ.get("SOL7D_UPDATE_WATCH", "1").strip() not in ("0","false","no")
    log(f"RPC={RPC_URL} window={window_sec}s ca_cap={ca_cap} tx/ca={tx_per}")
    known = load_exclude(); log(f"exclude_addrs={len(known)}")
    log("fetching trending CAs…")
    rows = fetch_gecko() + fetch_dex() + fetch_pump()
    cas = merge_cas(rows, ca_cap)
    log(f"trending_cas={len(cas)} (cap={ca_cap})")
    for i, c in enumerate(cas[:15]):
        log(f"  CA#{i+1} {c.get('symbol')} {c['ca'][:12]}… hot={float(c.get('hot') or 0):.1f} src={','.join(c.get('sources') or [])}")
    by_wallet, ca_stats = {}, []
    for i, meta in enumerate(cas):
        ca = meta["ca"]
        log(f"[{i+1}/{len(cas)}] RPC buyers {meta.get('symbol')} {ca[:12]}… rpc={_rpc_n[0]} 429={_rpc_429[0]}")
        try:
            buyers = hunt_ca(meta, window_sec, sig_pages, tx_per, known)
        except Exception as e:
            log(f"  FAIL {type(e).__name__}: {e}"); buyers = []
        ca_stats.append({"ca": ca, "symbol": meta.get("symbol"), "pool": meta.get("pool"), "n_buyers": len(buyers), "sources": meta.get("sources")})
        seen_local = {}
        for b in buyers:
            addr = b["address"]
            if addr in known: continue
            if addr not in seen_local: seen_local[addr] = len(seen_local)+1
            w = by_wallet.get(addr)
            if not w:
                w = {"address": addr, "cas": [], "ca_meta": {}, "n_buys": 0, "sources": set(), "first_seen": None, "last_seen": None, "early_ranks": [], "sigs": []}
                by_wallet[addr] = w
            w["n_buys"] += 1; w["sources"].add(b.get("source") or "solana_rpc_7d")
            if ca not in w["cas"]:
                w["cas"].append(ca); w["ca_meta"][ca] = {"symbol": meta.get("symbol"), "sources": list(meta.get("sources") or [])}
                w["early_ranks"].append(seen_local[addr])
            ts = b.get("ts")
            if isinstance(ts, str):
                if w["first_seen"] is None or ts < w["first_seen"]: w["first_seen"] = ts
                if w["last_seen"] is None or ts > w["last_seen"]: w["last_seen"] = ts
            if b.get("sig") and len(w["sigs"]) < 8: w["sigs"].append(b["sig"])
        log(f"  buyers={len(buyers)} unique={len(seen_local)} wallet_pool={len(by_wallet)}")
        if (i+1) % 5 == 0:
            try: write_outputs(by_wallet, known, cas, ca_stats, window_sec, min_cas, min_score, top_watch, update_watch, t0, partial=True)
            except Exception as e: log(f"  checkpoint skip: {type(e).__name__}: {e}")
    return write_outputs(by_wallet, known, cas, ca_stats, window_sec, min_cas, min_score, top_watch, update_watch, t0, partial=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=0)
    args = ap.parse_args()
    daemon = args.daemon or os.environ.get("SOL7D_DAEMON","").strip() in ("1","true","yes")
    if os.environ.get("SOL7D_ONCE","").strip() in ("1","true","yes"): daemon = False
    interval = args.interval or env_int("SOL7D_INTERVAL_SEC", 3600)
    if not daemon:
        hunt_once(); return 0
    log(f"daemon interval={interval}s")
    while True:
        try: hunt_once()
        except Exception as e: log(f"hunt_once FAIL {type(e).__name__}: {e}")
        time.sleep(max(300, interval))

if __name__ == "__main__":
    sys.exit(main())

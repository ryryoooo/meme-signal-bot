#!/usr/bin/env python3
"""StonkFun (Solana) skilled-digger wallet hunter — free paths only.

Discovers graduated / high-peak StonkFun launches via the public keyless API,
pulls early buyers from each LaunchLab bonding-curve pool via free Solana RPC
(getSignaturesForAddress + getTransaction), scores wallets that dig early on
multiple winners, and writes:

  sol-wallets/stonkfun_diggers.jsonl
  sol-wallets/summary_stonkfun_diggers.md
  sol-wallets/raw/stonkfun_hunt_state.json

Does NOT merge into rh-wallets/wallets.jsonl (wrong chain). LIVE_TRADING=0.
No box GMGN.

Env knobs:
  STONKFUN_API              default https://www.stonkfun.xyz/api/public/v1
  SOLANA_RPC_URL            default https://solana-rpc.publicnode.com
  STONK_PEAK_MIN_USD        min peak mcap to treat as winner (default 40000)
  STONK_TOKEN_CAP           max winners to process per run (default 18)
  STONK_FIRST_N             early buyer slots per mint (default 30)
  STONK_EARLY_SEC           early window seconds from createdAt (default 600)
  STONK_MIN_HITS            min distinct winner mints for digger list (default 2)
  STONK_MAX_SIG_PAGES       pool signature pages (default 20)
  STONK_MAX_TX_FETCH        txs to parse per pool (default 90)
  STONK_PAGES_NEWEST        graduated newest pages (default 4)
  STONK_PAGES_MCAP          graduated mcap pages (default 2)
  STONK_DAEMON=1            loop; STONK_INTERVAL_SEC (default 1800)
  STONK_ONCE=1              one pass (default when not daemon)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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
OUT_DIR = ROOT / "sol-wallets"
OUT_JSONL = OUT_DIR / "stonkfun_diggers.jsonl"
OUT_MD = OUT_DIR / "summary_stonkfun_diggers.md"
STATE_PATH = OUT_DIR / "raw" / "stonkfun_hunt_state.json"
CACHE_DIR = OUT_DIR / "raw" / "token_early"

SF_API = (os.environ.get("STONKFUN_API") or "https://www.stonkfun.xyz/api/public/v1").rstrip("/")
RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
UA = os.environ.get(
    "STONK_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/stonkfun-digger; +https://github.com/ryryoooo/meme-signal-bot)",
)

LAUNCHLAB_PROGRAM = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"

# Programs / hubs / routers — never diggers
SKIP_WALLETS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    LAUNCHLAB_PROGRAM,
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",  # StonkFun reward platform
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",  # StonkFun standard platform
    "So11111111111111111111111111111111111111112",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM authority
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # CLMM
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # CPMM
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcCanq",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # pump.fun
    "pfeeUxB6jkeY1Hxd7CsFCGUgBfgf1G7BYnvyxZrBssu",
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
    print(f"{ts} stonkfun {msg}", flush=True)


def iso_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jst_now_label() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def http_get_json(url: str, timeout: int = 40) -> dict:
    """GET JSON with urllib; fall back to curl on timeout/SSL flakes."""
    last: Exception | None = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as e:
            last = e
            time.sleep(0.4 * (1.7**attempt))
    # curl fallback
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), "-A", UA, "-H", "Accept: application/json", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        last = RuntimeError(r.stderr or f"curl rc={r.returncode}")
    except Exception as e:
        last = e
    raise RuntimeError(f"GET failed {url}: {last}")


def rpc(method: str, params: list, timeout: int = 45) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last: Exception | None = None
    for attempt in range(7):
        try:
            req = urllib.request.Request(
                RPC_URL,
                data=payload.encode(),
                headers={"content-type": "application/json", "User-Agent": UA},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode() or "{}")
            if body.get("error"):
                raise RuntimeError(body["error"])
            return body.get("result")
        except Exception as e:
            last = e
            time.sleep(0.35 * (1.7**attempt))
    # curl fallback
    r = subprocess.run(
        [
            "curl", "-sS", "-m", str(timeout), "-A", UA, "-X", "POST", RPC_URL,
            "-H", "content-type: application/json", "-d", payload,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        body = json.loads(r.stdout)
        if body.get("error"):
            raise RuntimeError(body["error"])
        return body.get("result")
    raise RuntimeError(f"RPC {method} failed: {last} / {r.stderr}")


def rpc_batch(calls: list[tuple[str, list]], timeout: int = 70) -> list:
    payload = json.dumps(
        [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    )
    last: Exception | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                RPC_URL,
                data=payload.encode(),
                headers={"content-type": "application/json", "User-Agent": UA},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode() or "[]")
            if isinstance(body, dict):
                if body.get("error"):
                    raise RuntimeError(body["error"])
                return [body.get("result")]
            out: list = [None] * len(calls)
            for item in body:
                idx = item.get("id")
                if isinstance(idx, int) and 0 <= idx < len(out):
                    out[idx] = item.get("result")
            return out
        except Exception as e:
            last = e
            time.sleep(0.45 * (1.8**attempt))
    # sequential fallback
    log(f"batch fallback ({last})")
    results = []
    for m, p in calls:
        try:
            results.append(rpc(m, p, timeout=min(40, timeout)))
        except Exception:
            results.append(None)
        time.sleep(0.05)
    return results


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "processed_mints": {},
        "wallet_hits": {},
        "hub_wallets": sorted(SKIP_WALLETS),
        "runs": [],
    }


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_winner_tokens(peak_min: float, pages_newest: int, pages_mcap: int) -> list[dict]:
    by_mint: dict[str, dict] = {}
    for sort, pages in (("newest", pages_newest), ("marketCap", pages_mcap)):
        for page in range(1, pages + 1):
            url = f"{SF_API}/tokens?status=graduated&sort={sort}&pageSize=50&page={page}"
            try:
                data = http_get_json(url)
            except Exception as e:
                log(f"tokens fetch fail sort={sort} page={page}: {e}")
                continue
            for t in (data.get("data") or {}).get("tokens") or []:
                mint = t.get("mint")
                if not mint:
                    continue
                by_mint[mint] = t
            time.sleep(0.12)
    winners = []
    for t in by_mint.values():
        if t.get("symbol") == "STONK":
            continue
        m = t.get("market") or {}
        peak = float(m.get("peakMarketCapUsd") or 0)
        if peak < peak_min:
            continue
        if not t.get("pool"):
            continue
        winners.append(t)
    winners.sort(key=lambda x: -float((x.get("market") or {}).get("peakMarketCapUsd") or 0))
    return winners


def fetch_pool_signatures(pool: str, max_pages: int) -> list[dict]:
    before = None
    all_sigs: list[dict] = []
    for _ in range(max_pages):
        params: dict = {"limit": 1000}
        if before:
            params["before"] = before
        sigs = rpc("getSignaturesForAddress", [pool, params]) or []
        if not sigs:
            break
        all_sigs.extend(sigs)
        before = sigs[-1]["signature"]
        if len(sigs) < 1000:
            break
        time.sleep(0.04)
    return all_sigs


def _tb_amount(tb: dict) -> float:
    ui = tb.get("uiTokenAmount") or {}
    try:
        if ui.get("uiAmount") is not None:
            return float(ui["uiAmount"])
        return float(ui.get("uiAmountString") or 0)
    except (TypeError, ValueError):
        return 0.0


def buyers_from_tx(res: dict, mint: str, skip: set[str]) -> list[tuple[str, float, str | None]]:
    """Return [(owner, delta, fee_payer)] who gained `mint` in this tx."""
    meta = res.get("meta") or {}
    if meta.get("err"):
        return []
    msg = (res.get("transaction") or {}).get("message") or {}
    aks = msg.get("accountKeys") or []
    signers: list[str] = []
    for a in aks:
        if isinstance(a, dict) and a.get("signer"):
            signers.append(a["pubkey"])
    if not signers and aks and isinstance(aks[0], str):
        signers = [aks[0]]
    fee_payer = signers[0] if signers else None
    pre = {
        (tb.get("owner"), tb.get("mint")): _tb_amount(tb)
        for tb in (meta.get("preTokenBalances") or [])
    }
    post = {
        (tb.get("owner"), tb.get("mint")): _tb_amount(tb)
        for tb in (meta.get("postTokenBalances") or [])
    }
    gained: list[tuple[str, float]] = []
    owners = {k[0] for k in list(pre) + list(post)}
    for o in owners:
        if not o or o in skip:
            continue
        dlt = post.get((o, mint), 0.0) - pre.get((o, mint), 0.0)
        if dlt > 0:
            gained.append((o, dlt))
    if not gained:
        return []
    # Prefer fee-payer / signer who actually received the mint
    ordered: list[tuple[str, float, str | None]] = []
    used = set()
    for o, dlt in gained:
        if o == fee_payer and o not in used:
            ordered.append((o, dlt, fee_payer))
            used.add(o)
    for o, dlt in gained:
        if o in signers and o not in used:
            ordered.append((o, dlt, fee_payer))
            used.add(o)
    for o, dlt in sorted(gained, key=lambda x: -x[1]):
        if o not in used:
            ordered.append((o, dlt, fee_payer))
            used.add(o)
    return ordered


def early_buyers_for_token(
    token: dict,
    *,
    first_n: int,
    early_sec: int,
    max_sig_pages: int,
    max_tx_fetch: int,
    skip: set[str],
) -> dict:
    mint = token["mint"]
    pool = token["pool"]
    created = iso_ts(token["createdAt"])
    early_end = created + early_sec
    creator = (token.get("creator") or (token.get("launch") or {}).get("creator") or "")
    local_skip = set(skip)
    if creator:
        local_skip.add(creator)

    cache_path = CACHE_DIR / f"{mint}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("first_n") == first_n and cached.get("early_sec") == early_sec:
                age = time.time() - float(cached.get("cached_at_unix") or 0)
                ttl = 3600 if cached.get("skipped") or not cached.get("buyers") else 86400 * 3
                if age < ttl:
                    return cached
        except Exception:
            pass

    # Probe first page — skip pruned / migrated pools with no curve history
    probe = rpc("getSignaturesForAddress", [pool, {"limit": 200}]) or []
    probe_ok = [s for s in probe if s.get("err") is None]
    if len(probe) < 30 or len(probe_ok) < 8:
        return {
            "mint": mint,
            "pool": pool,
            "symbol": token.get("symbol"),
            "name": token.get("name"),
            "launchpad": token.get("launchpad"),
            "creator": creator or None,
            "createdAt": token.get("createdAt"),
            "graduatedAt": token.get("graduatedAt"),
            "peakMarketCapUsd": float((token.get("market") or {}).get("peakMarketCapUsd") or 0),
            "marketCapUsd": float((token.get("market") or {}).get("marketCapUsd") or 0),
            "pool_sigs": len(probe),
            "pool_ok": len(probe_ok),
            "early_parsed": 0,
            "first_n": first_n,
            "early_sec": early_sec,
            "buyers": [],
            "skipped": "thin_pool_history",
            "cached_at": now_iso(),
            "cached_at_unix": time.time(),
            "chain": "solana",
            "source": "stonkfun",
        }

    sigs = fetch_pool_signatures(pool, max_sig_pages)
    ok = [s for s in sigs if s.get("err") is None]
    early = [
        s
        for s in ok
        if (s.get("blockTime") or 0) and created - 15 <= int(s["blockTime"]) <= early_end
    ]
    if len(early) < max(8, first_n // 3):
        # Bonding-curve pools often only live until graduation — take oldest overall
        early = sorted(ok, key=lambda x: (x.get("blockTime") or 0, x.get("transactionIndex") or 0))
    else:
        early = sorted(early, key=lambda x: (x.get("blockTime") or 0, x.get("transactionIndex") or 0))
    early = early[:max_tx_fetch]

    buyers: list[dict] = []
    seen: set[str] = set()
    for i in range(0, len(early), 4):
        chunk = early[i : i + 4]
        calls = [
            (
                "getTransaction",
                [
                    s["signature"],
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 1,
                    },
                ],
            )
            for s in chunk
        ]
        results = []
        for _m, _p in calls:
            try:
                results.append(rpc(_m, _p, timeout=40))
            except Exception:
                results.append(None)
            time.sleep(0.035)
        for s, res in zip(chunk, results):
            if not res:
                continue
            ordered = buyers_from_tx(res, mint, local_skip)
            if not ordered:
                continue
            wallet, dlt, fee_payer = ordered[0]
            if wallet in seen or wallet in local_skip:
                # try next gainer in same tx
                picked = None
                for w2, d2, fp in ordered:
                    if w2 not in seen and w2 not in local_skip:
                        picked = (w2, d2, fp)
                        break
                if not picked:
                    continue
                wallet, dlt, fee_payer = picked
            seen.add(wallet)
            buyers.append(
                {
                    "wallet": wallet,
                    "rank": len(buyers) + 1,
                    "delta": dlt,
                    "sig": s["signature"],
                    "blockTime": s.get("blockTime"),
                    "fee_payer": fee_payer,
                }
            )
            if len(buyers) >= first_n:
                break
        if len(buyers) >= first_n:
            break
        time.sleep(0.06)

    out = {
        "mint": mint,
        "pool": pool,
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "launchpad": token.get("launchpad"),
        "creator": creator or None,
        "createdAt": token.get("createdAt"),
        "graduatedAt": token.get("graduatedAt"),
        "peakMarketCapUsd": float((token.get("market") or {}).get("peakMarketCapUsd") or 0),
        "marketCapUsd": float((token.get("market") or {}).get("marketCapUsd") or 0),
        "pool_sigs": len(sigs),
        "pool_ok": len(ok),
        "early_parsed": len(early),
        "first_n": first_n,
        "early_sec": early_sec,
        "buyers": buyers,
        "cached_at": now_iso(),
        "cached_at_unix": time.time(),
        "chain": "solana",
        "source": "stonkfun",
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def score_diggers(
    mint_results: list[dict],
    *,
    min_hits: int,
    hub_extra: set[str],
) -> list[dict]:
    hits: dict[str, list[dict]] = defaultdict(list)
    for mr in mint_results:
        for b in mr.get("buyers") or []:
            w = b["wallet"]
            if w in SKIP_WALLETS or w in hub_extra:
                continue
            if w == mr.get("creator"):
                continue
            hits[w].append(
                {
                    "mint": mr["mint"],
                    "symbol": mr.get("symbol"),
                    "rank": b["rank"],
                    "delta": b.get("delta"),
                    "peakMarketCapUsd": mr.get("peakMarketCapUsd"),
                    "sig": b.get("sig"),
                    "blockTime": b.get("blockTime"),
                    "launchpad": mr.get("launchpad"),
                }
            )

    # Hub heuristic: appears early on many mints with very low median rank
    hub_auto: set[str] = set()
    n_mints = max(1, len(mint_results))
    for w, hs in hits.items():
        mints = {h["mint"] for h in hs}
        if len(mints) >= max(8, int(n_mints * 0.55)):
            ranks = sorted(h["rank"] for h in hs)
            med = ranks[len(ranks) // 2]
            if med <= 2:
                hub_auto.add(w)

    diggers = []
    for w, hs in hits.items():
        if w in hub_auto:
            continue
        uniq = {}
        for h in hs:
            # keep best (lowest) rank per mint
            prev = uniq.get(h["mint"])
            if prev is None or h["rank"] < prev["rank"]:
                uniq[h["mint"]] = h
        if len(uniq) < min_hits:
            continue
        rows = list(uniq.values())
        ranks = [r["rank"] for r in rows]
        peaks = [float(r.get("peakMarketCapUsd") or 0) for r in rows]
        early15 = sum(1 for r in ranks if r <= 15)
        early5 = sum(1 for r in ranks if r <= 5)
        score = (
            len(rows) * 40.0
            + early15 * 12.0
            + early5 * 18.0
            + sum(1.0 for p in peaks if p >= 100_000) * 8.0
            + sum(1.0 for p in peaks if p >= 500_000) * 12.0
            - (sum(ranks) / len(ranks)) * 1.5
        )
        diggers.append(
            {
                "address": w,
                "chain": "solana",
                "source": "stonkfun_digger",
                "platform": "stonkfun",
                "tags": ["solana", "stonkfun", "digger", "launchlab"],
                "hit_mints": len(rows),
                "hit_early15": early15,
                "hit_early5": early5,
                "avg_rank": round(sum(ranks) / len(ranks), 2),
                "best_rank": min(ranks),
                "score": round(score, 2),
                "peak_sum_usd": round(sum(peaks), 2),
                "launches": [
                    {
                        "mint": r["mint"],
                        "symbol": r.get("symbol"),
                        "rank": r["rank"],
                        "peakMarketCapUsd": r.get("peakMarketCapUsd"),
                        "sig": r.get("sig"),
                        "blockTime": r.get("blockTime"),
                    }
                    for r in sorted(rows, key=lambda x: x["rank"])
                ],
                "scanned_at": now_iso(),
            }
        )
    diggers.sort(key=lambda d: (-d["score"], -d["hit_mints"], d["avg_rank"]))
    return diggers, hub_auto


def write_outputs(diggers: list[dict], mint_results: list[dict], meta: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for d in diggers:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    lines = [
        f"# StonkFun diggers — {jst_now_label()}",
        "",
        f"- chain: **solana** (do not merge into RH `wallets.jsonl`)",
        f"- source: StonkFun public API + free Solana RPC (`{RPC_URL}`)",
        f"- winners processed: **{meta.get('processed')}** (peak ≥ ${meta.get('peak_min'):,.0f})",
        f"- diggers (hits ≥ {meta.get('min_hits')}): **{len(diggers)}**",
        f"- hubs auto-excluded: **{meta.get('hubs_auto', 0)}**",
        f"- LIVE_TRADING=0 / no box GMGN",
        "",
        "## Top diggers",
        "",
        "| # | address | hits | early≤15 | avg_rank | score | sample |",
        "|---|---------|------|----------|----------|-------|--------|",
    ]
    for i, d in enumerate(diggers[:40], 1):
        sample = ", ".join(
            f"{x.get('symbol')}#{x['rank']}" for x in (d.get("launches") or [])[:4]
        )
        lines.append(
            f"| {i} | `{d['address']}` | {d['hit_mints']} | {d['hit_early15']} | "
            f"{d['avg_rank']} | {d['score']} | {sample} |"
        )
    lines.extend(["", "## Winners scanned", ""])
    for mr in sorted(mint_results, key=lambda x: -float(x.get("peakMarketCapUsd") or 0))[:40]:
        lines.append(
            f"- **{mr.get('symbol')}** peak=${float(mr.get('peakMarketCapUsd') or 0):,.0f} "
            f"buyers={len(mr.get('buyers') or [])} pool_sigs={mr.get('pool_sigs')} "
            f"`{mr.get('mint')}`"
        )
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_once(args: argparse.Namespace) -> int:
    peak_min = env_float("STONK_PEAK_MIN_USD", args.peak_min)
    token_cap = env_int("STONK_TOKEN_CAP", args.token_cap)
    first_n = env_int("STONK_FIRST_N", args.first_n)
    early_sec = env_int("STONK_EARLY_SEC", args.early_sec)
    min_hits = env_int("STONK_MIN_HITS", args.min_hits)
    max_sig_pages = env_int("STONK_MAX_SIG_PAGES", args.max_sig_pages)
    max_tx_fetch = env_int("STONK_MAX_TX_FETCH", args.max_tx_fetch)
    pages_newest = env_int("STONK_PAGES_NEWEST", args.pages_newest)
    pages_mcap = env_int("STONK_PAGES_MCAP", args.pages_mcap)

    log(
        f"start peak_min={peak_min} cap={token_cap} first_n={first_n} early_sec={early_sec} "
        f"rpc={RPC_URL}"
    )
    winners = fetch_winner_tokens(peak_min, pages_newest, pages_mcap)
    log(f"winners available={len(winners)}")

    # Prefer recent launchlab (curve pool still fully indexed) then older / raydium
    launchlab = [t for t in winners if t.get("launchpad") == "launchlab"]
    other = [t for t in winners if t.get("launchpad") != "launchlab"]
    cutoff = time.time() - 5 * 86400
    recent = [t for t in launchlab if iso_ts(t.get("createdAt") or "1970-01-01T00:00:00Z") >= cutoff]
    recent.sort(key=lambda t: -float((t.get("market") or {}).get("peakMarketCapUsd") or 0))
    older = [t for t in launchlab if t not in recent]
    older.sort(key=lambda t: -float((t.get("market") or {}).get("peakMarketCapUsd") or 0))
    ordered = recent + older + other
    # dedupe preserve order
    seen_m = set()
    pick: list[dict] = []
    for t in ordered:
        if t["mint"] in seen_m:
            continue
        seen_m.add(t["mint"])
        pick.append(t)
        if len(pick) >= token_cap * 3:  # oversample; thin pools skipped later
            break

    st = load_state()
    hub_extra = set(st.get("hub_wallets") or []) | set(SKIP_WALLETS)

    mint_results: list[dict] = []
    for i, t in enumerate(pick, 1):
        if len([m for m in mint_results if m.get("buyers")]) >= token_cap:
            log(f"reached token_cap={token_cap} with buyers; stop")
            break
        sym = t.get("symbol")
        peak = float((t.get("market") or {}).get("peakMarketCapUsd") or 0)
        log(f"[{i}/{len(pick)}] {sym} peak=${peak:,.0f} mint={t['mint'][:8]}…")
        # enrich creator from detail if missing
        if not t.get("creator"):
            try:
                detail = http_get_json(f"{SF_API}/tokens/{t['mint']}")
                launch = (detail.get("data") or {}).get("launch") or {}
                tok = (detail.get("data") or {}).get("token") or {}
                t["creator"] = launch.get("creator") or tok.get("creator")
                time.sleep(0.08)
            except Exception:
                pass
        try:
            mr = early_buyers_for_token(
                t,
                first_n=first_n,
                early_sec=early_sec,
                max_sig_pages=max_sig_pages,
                max_tx_fetch=max_tx_fetch,
                skip=hub_extra,
            )
        except Exception as e:
            log(f"  FAIL {sym}: {e}")
            continue
        mint_results.append(mr)
        st.setdefault("processed_mints", {})[t["mint"]] = {
            "symbol": sym,
            "peak": peak,
            "buyers": len(mr.get("buyers") or []),
            "at": now_iso(),
        }
        log(f"  buyers={len(mr.get('buyers') or [])} pool_sigs={mr.get('pool_sigs')}")
        save_state(st)

    diggers, hub_auto = score_diggers(mint_results, min_hits=min_hits, hub_extra=hub_extra)
    if hub_auto:
        st["hub_wallets"] = sorted(set(st.get("hub_wallets") or []) | hub_auto | SKIP_WALLETS)
        log(f"auto-hubs excluded: {len(hub_auto)}")
    st.setdefault("runs", []).append(
        {
            "at": now_iso(),
            "processed": len(mint_results),
            "diggers": len(diggers),
            "hubs_auto": len(hub_auto),
        }
    )
    st["runs"] = st["runs"][-40:]
    st["last_diggers_top"] = [
        {"address": d["address"], "hits": d["hit_mints"], "score": d["score"]}
        for d in diggers[:20]
    ]
    save_state(st)

    write_outputs(
        diggers,
        mint_results,
        {
            "processed": len(mint_results),
            "peak_min": peak_min,
            "min_hits": min_hits,
            "hubs_auto": len(hub_auto),
        },
    )
    log(f"done diggers={len(diggers)} -> {OUT_JSONL}")
    for d in diggers[:12]:
        sample = ",".join(f"{x.get('symbol')}#{x['rank']}" for x in (d.get("launches") or [])[:3])
        log(f"  TOP {d['address']} hits={d['hit_mints']} score={d['score']} {sample}")
    return 0 if diggers else (0 if mint_results else 2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt StonkFun digger wallets (Solana)")
    ap.add_argument("--once", action="store_true", help="single pass")
    ap.add_argument("--daemon", action="store_true", help="loop")
    ap.add_argument("--peak-min", type=float, default=40000)
    ap.add_argument("--token-cap", type=int, default=18)
    ap.add_argument("--first-n", type=int, default=30)
    ap.add_argument("--early-sec", type=int, default=600)
    ap.add_argument("--min-hits", type=int, default=2)
    ap.add_argument("--max-sig-pages", type=int, default=20)
    ap.add_argument("--max-tx-fetch", type=int, default=90)
    ap.add_argument("--pages-newest", type=int, default=4)
    ap.add_argument("--pages-mcap", type=int, default=2)
    args = ap.parse_args()

    daemon = args.daemon or os.environ.get("STONK_DAEMON") == "1"
    once = args.once or os.environ.get("STONK_ONCE") == "1" or not daemon
    interval = env_int("STONK_INTERVAL_SEC", 1800)

    if once and not args.daemon:
        return run_once(args)

    while True:
        try:
            run_once(args)
        except Exception as e:
            log(f"run error: {e}")
        log(f"sleep {interval}s")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

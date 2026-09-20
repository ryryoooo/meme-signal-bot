#!/usr/bin/env python3
"""Vet StonkFun diggers for estimated profit + recent activity; merge passers.

Reads candidates from sol-wallets/stonkfun_diggers.jsonl (and optional
token_early caches), estimates StonkFun-mint PnL without box GMGN
(Gecko/Dex prices + peak/entry heuristic + optional pool-trade match),
gates on realized_est > 0 (or multi-hit early winners), recent activity,
hub/creator exclusion — then appends/merges passers into
sol-wallets/stonkfun_diggers.jsonl (dedupe by address).

Does NOT touch rh-wallets/ or sol_smart_unknown. LIVE_TRADING=0.

Env:
  STONK_PROMOTE_ACTIVE_HOURS   default 168 (7d)
  STONK_PROMOTE_MIN_HITS       default 2 (or 1 if realized_est >> 0)
  STONK_PROMOTE_MIN_REALIZED   default 0  (must be > this)
  STONK_PROMOTE_ENTRY_MCAP     default 20000  early-curve mcap assumption
  STONK_PROMOTE_BUY_USD        default 40     assumed size per hit
  STONK_PROMOTE_MAX_PER_RUN    default 80
  STONK_PROMOTE_DRY_RUN=1
  STONK_PROMOTE_REQUIRE_ACTIVITY=1
  SOLANA_RPC_URL
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
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
OUT_DIR = ROOT / "sol-wallets"
DIGGERS_PATH = Path(
    os.environ.get("STONKFUN_DIGGERS_PATH") or str(OUT_DIR / "stonkfun_diggers.jsonl")
)
CACHE_DIR = OUT_DIR / "raw" / "token_early"
LOG_PATH = OUT_DIR / "promote_stonkfun_log.jsonl"
SUMMARY_PATH = OUT_DIR / "summary_promote_stonkfun.md"
STATE_PATH = OUT_DIR / "raw" / "stonkfun_promote_state.json"
HUNT_STATE = OUT_DIR / "raw" / "stonkfun_hunt_state.json"

RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
UA = os.environ.get(
    "STONK_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/stonkfun-promote; +https://github.com/ryryoooo/meme-signal-bot)",
)
JINA = "https://r.jina.ai/http://"

SKIP_WALLETS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",
    "So11111111111111111111111111111111111111112",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcCanq",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
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


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} stonk-promote {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jst_label() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def _f(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rpc(method: str, params: list, timeout: int = 35) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
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
                body = json.loads(resp.read().decode() or "{}")
            if body.get("error"):
                raise RuntimeError(body["error"])
            return body.get("result")
        except Exception as e:
            last = e
            time.sleep(0.3 * (1.6**attempt))
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
    raise RuntimeError(f"RPC {method} failed: {last}")


def jina_get_json(url: str, timeout: float = 40.0) -> tuple[dict | None, str | None]:
    """GET via jina relay (box Dex/Gecko often 429)."""
    target = url
    if target.startswith("https://"):
        relay = JINA + target[len("https://") :]
    elif target.startswith("http://"):
        relay = JINA + target[len("http://") :]
    else:
        relay = JINA + target
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                relay,
                headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            # jina wraps: {"data":{"content":"..."}}
            try:
                outer = json.loads(raw)
                content = (outer.get("data") or {}).get("content")
                if isinstance(content, str) and content.strip():
                    raw = content
            except json.JSONDecodeError:
                pass
            raw = raw.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            # find first JSON object/array
            for i, ch in enumerate(raw):
                if ch in "{[":
                    try:
                        return json.loads(raw[i:]), None
                    except json.JSONDecodeError:
                        break
            try:
                return json.loads(raw), None
            except json.JSONDecodeError as e:
                last = str(e)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(0.4 * (1.7**attempt))
    return None, str(last)


def dex_token_price(mint: str) -> float | None:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=18) as resp:
            data = json.loads(resp.read().decode() or "{}")
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        return _f(best.get("priceUsd"))
    except Exception:
        return None


def gecko_price(mint: str) -> float | None:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{mint}"
    )
    if err or not isinstance(data, dict):
        return None
    attrs = (data.get("data") or {}).get("attributes") or {}
    tp = attrs.get("token_prices") if isinstance(attrs, dict) else None
    if isinstance(tp, dict):
        for k, v in tp.items():
            if k == mint or k.endswith(mint):
                return _f(v)
        if len(tp) == 1:
            return _f(next(iter(tp.values())))
    return _f(attrs.get("price_usd"))


def gecko_pool_trades_for_wallet(mint: str, wallet: str, max_pools: int = 2) -> dict:
    """Match wallet in recent gecko pool trades (bonus signal; often empty for old digs)."""
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
    )
    buy_usd = 0.0
    sell_usd = 0.0
    n = 0
    if err or not isinstance(data, dict):
        return {"buy_usd": 0.0, "sell_usd": 0.0, "n": 0}
    rows = data.get("data") or []
    scored: list[tuple[float, str]] = []
    for row in rows if isinstance(rows, list) else []:
        attrs = row.get("attributes") or {}
        reserve = _f(attrs.get("reserve_in_usd")) or 0.0
        addr = attrs.get("address")
        if not addr and isinstance(row.get("id"), str) and "_" in row["id"]:
            addr = row["id"].split("_", 1)[-1]
        if addr:
            scored.append((reserve, addr))
    scored.sort(reverse=True)
    for _res, pool in scored[:max_pools]:
        tdata, terr = jina_get_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades"
        )
        time.sleep(0.35)
        if terr or not isinstance(tdata, dict):
            continue
        for row in tdata.get("data") or []:
            a = row.get("attributes") or {}
            w = a.get("tx_from_address") or a.get("from_address") or a.get("maker")
            if w != wallet:
                continue
            vol = _f(a.get("volume_in_usd")) or 0.0
            to_tok = a.get("to_token_address") or ""
            from_tok = a.get("from_token_address") or ""
            kind = (a.get("kind") or "").lower()
            if to_tok == mint or kind == "buy":
                buy_usd += vol
                n += 1
            elif from_tok == mint or kind == "sell":
                sell_usd += vol
                n += 1
    return {"buy_usd": buy_usd, "sell_usd": sell_usd, "n": n}


def wallet_last_active_unix(addr: str) -> int | None:
    try:
        sigs = rpc("getSignaturesForAddress", [addr, {"limit": 5}]) or []
    except Exception as e:
        log(f"activity fail {addr[:8]}… {type(e).__name__}")
        return None
    best = None
    for s in sigs:
        bt = s.get("blockTime")
        if isinstance(bt, int):
            best = bt if best is None else max(best, bt)
    return best


def peak_entry_pnl(launches: list[dict], *, entry_mcap: float, buy_usd: float) -> dict:
    """Paper PnL from early dig on winners: peak/entry multiple × assumed buy size."""
    realized = 0.0
    wins = 0
    details = []
    for L in launches:
        peak = float(L.get("peakMarketCapUsd") or 0)
        rank = int(L.get("rank") or 99)
        # later ranks → slightly higher entry mcap
        entry = entry_mcap * (1.0 + max(0, rank - 1) * 0.08)
        if peak <= 0 or entry <= 0:
            continue
        mult = peak / entry
        # Cap fantasy multiples; require early-ish + 2x+
        if rank <= 20 and mult >= 2.0:
            paper = buy_usd * (min(mult, 80.0) - 1.0)
            realized += paper
            wins += 1
            details.append(
                {
                    "mint": L.get("mint"),
                    "symbol": L.get("symbol"),
                    "rank": rank,
                    "peak": peak,
                    "mult": round(mult, 2),
                    "paper_usd": round(paper, 2),
                }
            )
        elif rank <= 10 and peak >= entry_mcap:
            # early hit that at least reached entry mcap again
            paper = buy_usd * 0.25
            realized += paper
            wins += 1
            details.append(
                {
                    "mint": L.get("mint"),
                    "symbol": L.get("symbol"),
                    "rank": rank,
                    "peak": peak,
                    "mult": round(mult, 2),
                    "paper_usd": round(paper, 2),
                    "note": "early_hold_proxy",
                }
            )
    return {
        "realized_pnl_usd_est": round(realized, 2),
        "win_mints": wins,
        "legs": details,
        "pnl_source": "stonkfun_peak_entry_est",
    }


def estimate_digger_pnl(row: dict, *, entry_mcap: float, buy_usd: float, use_gecko: bool) -> dict:
    launches = list(row.get("launches") or [])
    base = peak_entry_pnl(launches, entry_mcap=entry_mcap, buy_usd=buy_usd)
    gecko_buy = 0.0
    gecko_sell = 0.0
    gecko_n = 0
    if use_gecko and launches:
        # Sample up to 2 highest-peak mints for trade match (slow)
        top = sorted(launches, key=lambda x: -float(x.get("peakMarketCapUsd") or 0))[:2]
        for L in top:
            mint = L.get("mint")
            if not mint:
                continue
            hit = gecko_pool_trades_for_wallet(mint, row["address"], max_pools=1)
            gecko_buy += hit["buy_usd"]
            gecko_sell += hit["sell_usd"]
            gecko_n += hit["n"]
            time.sleep(0.2)
    gecko_realized = gecko_sell - gecko_buy
    # Prefer gecko when we actually saw legs; else peak/entry paper
    if gecko_n >= 1 and (gecko_sell > 0 or gecko_buy > 0):
        # blend: gecko realized if positive, else keep paper if paper>0
        if gecko_realized > 0:
            base["realized_pnl_usd_est"] = round(gecko_realized, 2)
            base["pnl_source"] = "gecko_trades+peak"
        else:
            base["realized_pnl_usd_est"] = round(
                max(base["realized_pnl_usd_est"], gecko_realized), 2
            )
            base["pnl_source"] = "peak_entry_est+gecko_neg"
        base["gecko_buy_usd"] = round(gecko_buy, 2)
        base["gecko_sell_usd"] = round(gecko_sell, 2)
        base["gecko_legs"] = gecko_n
    return base


def load_hubs() -> set[str]:
    hubs = set(SKIP_WALLETS)
    if HUNT_STATE.exists():
        try:
            st = json.loads(HUNT_STATE.read_text(encoding="utf-8"))
            hubs |= set(st.get("hub_wallets") or [])
        except Exception:
            pass
    return hubs


def candidates_from_caches(existing: dict[str, dict]) -> list[dict]:
    """Rebuild digger-like rows from token_early if diggers file thin."""
    if not CACHE_DIR.exists():
        return []
    hits: dict[str, list[dict]] = defaultdict(list)
    creators: set[str] = set()
    for path in CACHE_DIR.glob("*.json"):
        try:
            mr = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        creator = mr.get("creator")
        if creator:
            creators.add(creator)
        for b in mr.get("buyers") or []:
            w = b.get("wallet")
            if not w or w in SKIP_WALLETS or w == creator:
                continue
            hits[w].append(
                {
                    "mint": mr.get("mint"),
                    "symbol": mr.get("symbol"),
                    "rank": b.get("rank"),
                    "peakMarketCapUsd": mr.get("peakMarketCapUsd"),
                    "sig": b.get("sig"),
                    "blockTime": b.get("blockTime"),
                }
            )
    out = []
    for w, hs in hits.items():
        if w in existing:
            continue
        uniq = {}
        for h in hs:
            m = h.get("mint")
            if not m:
                continue
            prev = uniq.get(m)
            if prev is None or int(h.get("rank") or 99) < int(prev.get("rank") or 99):
                uniq[m] = h
        if len(uniq) < 2:
            continue
        rows = list(uniq.values())
        ranks = [int(r.get("rank") or 99) for r in rows]
        peaks = [float(r.get("peakMarketCapUsd") or 0) for r in rows]
        early15 = sum(1 for r in ranks if r <= 15)
        early5 = sum(1 for r in ranks if r <= 5)
        score = (
            len(rows) * 40.0
            + early15 * 12.0
            + early5 * 18.0
            + sum(1.0 for p in peaks if p >= 100_000) * 8.0
            - (sum(ranks) / len(ranks)) * 1.5
        )
        out.append(
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
                "launches": sorted(rows, key=lambda x: int(x.get("rank") or 99)),
                "scanned_at": now_iso(),
            }
        )
    return out


def gate_passer(
    row: dict,
    est: dict,
    last_active: int | None,
    *,
    min_hits: int,
    min_realized: float,
    active_hours: int,
    require_activity: bool,
    hubs: set[str],
) -> tuple[bool, str]:
    addr = row.get("address") or ""
    if not addr or addr in hubs or addr in SKIP_WALLETS:
        return False, "hub_or_skip"
    hits = int(row.get("hit_mints") or len(row.get("launches") or []))
    early15 = int(row.get("hit_early15") or 0)
    realized = float(est.get("realized_pnl_usd_est") or 0)
    win_mints = int(est.get("win_mints") or 0)

    if require_activity:
        if last_active is None:
            return False, "no_activity_ts"
        age_h = (time.time() - last_active) / 3600.0
        if age_h > active_hours:
            return False, f"stale_{age_h:.0f}h"

    # Primary: realized_est > floor
    if realized > min_realized and (hits >= min_hits or (hits >= 1 and realized >= 50)):
        return True, "realized_est"
    # Multi-hit early winners even if paper est thin
    if hits >= max(2, min_hits) and early15 >= 2 and win_mints >= 2 and realized > min_realized:
        return True, "multi_hit_winners"
    if hits >= 3 and early15 >= 2 and realized > min_realized:
        return True, "triple_early"
    return False, f"fail hits={hits} early15={early15} real={realized:.1f} wins={win_mints}"


def merge_row(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    out = dict(old)
    # Prefer richer / higher score
    if float(new.get("score") or 0) >= float(old.get("score") or 0):
        for k in (
            "hit_mints", "hit_early15", "hit_early5", "avg_rank", "best_rank",
            "score", "peak_sum_usd", "launches", "scanned_at",
        ):
            if k in new:
                out[k] = new[k]
    for k in (
        "realized_pnl_usd_est", "pnl_source", "pnl_est_meta", "vetted_at",
        "last_active", "last_active_at", "promote_reason", "pass_pnl",
        "tags", "source", "platform", "chain",
    ):
        if k in new and new[k] is not None:
            out[k] = new[k]
    # Union tags
    tags = list(dict.fromkeys(list(old.get("tags") or []) + list(new.get("tags") or [])))
    out["tags"] = tags
    return out


def write_summary(passers: list[dict], stats: dict) -> None:
    lines = [
        f"# StonkFun promote diggers — {jst_label()}",
        "",
        f"- candidates: **{stats.get('candidates')}**",
        f"- vetted: **{stats.get('vetted')}**",
        f"- passers this run: **{stats.get('passed')}** (new/updated: **{stats.get('added')}**)",
        f"- rejected: **{stats.get('rejected')}**",
        f"- diggers file total: **{stats.get('total')}**",
        f"- active_hours≤{stats.get('active_hours')} · min_hits={stats.get('min_hits')} · min_realized>{stats.get('min_realized')}",
        f"- LIVE_TRADING=0 / no box GMGN / separate from sol_smart_unknown & RH",
        "",
        "## Passers",
        "",
        "| # | address | hits | realized_est | reason | last_active |",
        "|---|---------|------|--------------|--------|-------------|",
    ]
    for i, d in enumerate(passers[:50], 1):
        la = d.get("last_active_at") or "—"
        lines.append(
            f"| {i} | `{d.get('address')}` | {d.get('hit_mints')} | "
            f"{d.get('realized_pnl_usd_est')} | {d.get('promote_reason')} | {la} |"
        )
    lines.append("")
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    active_hours = env_int("STONK_PROMOTE_ACTIVE_HOURS", args.active_hours)
    min_hits = env_int("STONK_PROMOTE_MIN_HITS", args.min_hits)
    min_realized = env_float("STONK_PROMOTE_MIN_REALIZED", args.min_realized)
    entry_mcap = env_float("STONK_PROMOTE_ENTRY_MCAP", args.entry_mcap)
    buy_usd = env_float("STONK_PROMOTE_BUY_USD", args.buy_usd)
    max_per = env_int("STONK_PROMOTE_MAX_PER_RUN", args.max_per)
    dry = env_bool("STONK_PROMOTE_DRY_RUN", args.dry_run)
    require_activity = env_bool("STONK_PROMOTE_REQUIRE_ACTIVITY", not args.no_activity)
    use_gecko = env_bool("STONK_PROMOTE_GECKO", args.gecko)

    hubs = load_hubs()
    existing_rows = load_jsonl(DIGGERS_PATH)
    by_addr: dict[str, dict] = {}
    for r in existing_rows:
        a = (r.get("address") or "").strip()
        if a:
            by_addr[a] = r

    extras = candidates_from_caches(by_addr)
    if extras:
        log(f"cache extras candidates={len(extras)}")

    # Build worklist: existing + extras, prefer unscored / stale vet
    work: list[dict] = []
    seen = set()
    for r in list(by_addr.values()) + extras:
        a = r.get("address")
        if not a or a in seen:
            continue
        seen.add(a)
        work.append(r)
    work.sort(key=lambda r: (-float(r.get("score") or 0), -int(r.get("hit_mints") or 0)))
    work = work[: max(5, max_per)]

    log(
        f"start candidates={len(work)} existing={len(by_addr)} "
        f"active_h={active_hours} min_hits={min_hits} gecko={int(use_gecko)} dry={int(dry)}"
    )

    passed_rows: list[dict] = []
    rejected = 0
    added = 0
    vetted = 0

    for i, row in enumerate(work, 1):
        addr = row["address"]
        log(f"[{i}/{len(work)}] vet {addr[:8]}… hits={row.get('hit_mints')} score={row.get('score')}")
        est = estimate_digger_pnl(row, entry_mcap=entry_mcap, buy_usd=buy_usd, use_gecko=use_gecko)
        last_active = wallet_last_active_unix(addr)
        time.sleep(0.05)
        ok, reason = gate_passer(
            row,
            est,
            last_active,
            min_hits=min_hits,
            min_realized=min_realized,
            active_hours=active_hours,
            require_activity=require_activity,
            hubs=hubs,
        )
        vetted += 1
        if not ok:
            rejected += 1
            log(f"  REJECT {reason} real={est.get('realized_pnl_usd_est')}")
            continue

        la_iso = None
        if last_active:
            la_iso = datetime.fromtimestamp(last_active, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tags = list(row.get("tags") or ["solana", "stonkfun", "digger"])
        for t in ("pass_pnl", "stonkfun_vetted", "active"):
            if t not in tags:
                tags.append(t)
        updated = dict(row)
        updated.update(
            {
                "realized_pnl_usd_est": est.get("realized_pnl_usd_est"),
                "pnl_source": est.get("pnl_source"),
                "pnl_est_meta": {
                    "win_mints": est.get("win_mints"),
                    "legs": est.get("legs"),
                    "gecko_buy_usd": est.get("gecko_buy_usd"),
                    "gecko_sell_usd": est.get("gecko_sell_usd"),
                    "entry_mcap": entry_mcap,
                    "buy_usd": buy_usd,
                },
                "vetted_at": now_iso(),
                "last_active": last_active,
                "last_active_at": la_iso,
                "promote_reason": reason,
                "pass_pnl": True,
                "tags": tags,
                "source": "stonkfun_digger",
                "platform": "stonkfun",
                "chain": "solana",
            }
        )
        prev = by_addr.get(addr)
        was_new = prev is None or not prev.get("pass_pnl")
        by_addr[addr] = merge_row(prev, updated)
        passed_rows.append(by_addr[addr])
        if was_new:
            added += 1
        log(
            f"  PASS {reason} real=${est.get('realized_pnl_usd_est')} "
            f"last_active={la_iso or '—'} new={int(was_new)}"
        )

    # Keep prior passers that weren't re-vetted this run (still marked pass_pnl)
    for a, r in list(by_addr.items()):
        if r.get("pass_pnl") and a not in {p["address"] for p in passed_rows}:
            # Re-check activity freshness lightly
            la = r.get("last_active")
            if require_activity and isinstance(la, (int, float)):
                if (time.time() - float(la)) / 3600.0 > active_hours:
                    # demote stale
                    r = dict(r)
                    r["pass_pnl"] = False
                    tags = [t for t in (r.get("tags") or []) if t not in ("pass_pnl", "active")]
                    r["tags"] = tags
                    by_addr[a] = r
                    continue
            passed_rows.append(r)

    # Final diggers file = all pass_pnl True, sorted by score
    final = [r for r in by_addr.values() if r.get("pass_pnl")]
    # Also keep high-score multi-hit that passed this run already in final
    # If promote emptied everything due to activity, fall back to this-run passers only
    if not final and passed_rows:
        final = passed_rows
    # Dedupe
    uniq: dict[str, dict] = {}
    for r in final:
        a = r.get("address")
        if not a:
            continue
        prev = uniq.get(a)
        if prev is None or float(r.get("score") or 0) >= float(prev.get("score") or 0):
            uniq[a] = r
    final = sorted(
        uniq.values(),
        key=lambda d: (-float(d.get("realized_pnl_usd_est") or 0), -float(d.get("score") or 0)),
    )

    stats = {
        "candidates": len(work),
        "vetted": vetted,
        "passed": len(passed_rows),
        "added": added,
        "rejected": rejected,
        "total": len(final),
        "active_hours": active_hours,
        "min_hits": min_hits,
        "min_realized": min_realized,
        "at": now_iso(),
        "dry": dry,
    }

    write_summary(final, stats)
    append_jsonl(
        LOG_PATH,
        {
            **stats,
            "pass_addrs": [r.get("address") for r in final[:40]],
            "jst": jst_label(),
        },
    )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    if dry:
        log(f"DRY_RUN would write diggers={len(final)} added={added}")
        return 0

    write_jsonl(DIGGERS_PATH, final)
    log(f"wrote diggers={len(final)} added/updated_new={added} -> {DIGGERS_PATH}")
    for d in final[:10]:
        log(
            f"  TOP {d.get('address')} hits={d.get('hit_mints')} "
            f"real=${d.get('realized_pnl_usd_est')} score={d.get('score')}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote profitable active StonkFun diggers")
    ap.add_argument("--apply", action="store_true", help="write diggers file (default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--active-hours", type=int, default=168)
    ap.add_argument("--min-hits", type=int, default=2)
    ap.add_argument("--min-realized", type=float, default=0.0)
    ap.add_argument("--entry-mcap", type=float, default=20000.0)
    ap.add_argument("--buy-usd", type=float, default=40.0)
    ap.add_argument("--max-per", type=int, default=80)
    ap.add_argument("--gecko", action="store_true", help="also match gecko pool trades")
    ap.add_argument("--no-activity", action="store_true", help="skip recent-activity gate")
    args = ap.parse_args()
    if not args.dry_run:
        args.dry_run = False
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

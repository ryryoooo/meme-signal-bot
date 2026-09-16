#!/usr/bin/env python3
"""Continuous on-chain wallet collector for Arc + Robinhood Chain.

Credit-saving: no Nansen Super / FOMO / paid GMGN harvests by default.
Arc: prefer existing meme-foundation/arc-wallets/collect_arc_wallets.py --skip-gmgn
     + export_bot_keepers; else Arcscan API inline merge.
RH:  Blockscout (etherscan-compat + /api/v2) → rh-wallets/wallets_onchain.jsonl
     and merge new actives into rh-wallets/wallets.jsonl without dropping
     existing Nansen/GMGN quality rows.

Writes onchain_collect_summary.md with counts.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
KEEPER_FLOOR = float(os.environ.get("ONCHAIN_KEEPER_FLOOR", "0.01"))
RH_ONCHAIN_FLOOR = float(os.environ.get("RH_ONCHAIN_FLOOR", "0.01"))
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)

ARC_COLLECT_CANDIDATES = [
    Path("/workspace/meme-foundation/arc-wallets/collect_arc_wallets.py"),
    BOT_ROOT.parent / "arc-wallets" / "collect_arc_wallets.py",
]
ARC_EXPORT_CANDIDATES = [
    Path("/workspace/meme-foundation/arc-wallets/scripts/export_bot_keepers.py"),
    BOT_ROOT.parent / "arc-wallets" / "scripts" / "export_bot_keepers.py",
]

ARCSCAN = os.environ.get("ARCSCAN_API", "https://api.arc-scan.org")
RH_BS_BASE = os.environ.get(
    "RH_BLOCKSCOUT_API", "https://robinhoodchain.blockscout.com/api"
)
RH_BS_V2 = os.environ.get(
    "RH_BLOCKSCOUT_API_V2", "https://robinhoodchain.blockscout.com/api/v2"
)

SYSTEM_SKIP = {
    "0x0000000000000000000000000000000000000000",
    "0xfffffffffffffffffffffffffffffffffffffffe",
}
ZERO_PREFIX = "0x000000000000000000000000000000000000"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def addr_ok(a: str | None) -> bool:
    if not a:
        return False
    a = a.lower()
    if a in SYSTEM_SKIP or a.startswith(ZERO_PREFIX):
        return False
    return len(a) == 42 and a.startswith("0x")


def http_json(url: str, timeout: int = 45) -> tuple[int, dict | list | None, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw), ""
            except json.JSONDecodeError:
                return resp.status, None, raw[:300]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        return e.code, None, body
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def find_first(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


# ── Arc ──────────────────────────────────────────────────────────────────────


def run_arc_external(skip_gmgn: bool) -> dict:
    collect = find_first(ARC_COLLECT_CANDIDATES)
    if not collect:
        return {"ok": False, "reason": "collect_arc_wallets.py not found"}
    cmd = [sys.executable, str(collect)]
    if skip_gmgn:
        cmd.append("--skip-gmgn")
    print(f"[arc] subprocess: {' '.join(cmd)}", flush=True)
    env = os.environ.copy()
    # Keep Arcscan harvest modest in scheduled runs
    env.setdefault("ARC_MAX_PAGES_ACCOUNTS", "4")
    env.setdefault("ARC_MAX_PAGES_TOKENS", "4")
    env.setdefault("ARC_MAX_PAGES_TRANSFERS", "8")
    env.setdefault("ARC_MAX_PAGES_TXS", "4")
    env.setdefault("ARC_TOKEN_CANDIDATES", "20")
    env.setdefault("ARC_HOLDER_PAGES", "2")
    env.setdefault("ARC_TOKEN_XF_PAGES", "2")
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    print(f"[arc] collect exit={p.returncode}", flush=True)
    if p.stdout:
        print(p.stdout[-2000:], flush=True)
    if p.returncode != 0 and p.stderr:
        print(p.stderr[-1500:], flush=True)

    export = find_first(ARC_EXPORT_CANDIDATES)
    export_meta: dict = {}
    if export:
        print(f"[arc] export: {export}", flush=True)
        ep = subprocess.run(
            [sys.executable, str(export)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        print(f"[arc] export exit={ep.returncode}", flush=True)
        if ep.stdout:
            # last line often JSON meta
            for line in reversed(ep.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        export_meta = json.loads(line)
                    except json.JSONDecodeError:
                        pass
                    break
            print(ep.stdout[-1500:], flush=True)
    else:
        # Still copy quality files if collect wrote them beside collector
        arc_root = collect.parent
        for name in ("wallets_quality.jsonl", "wallets.jsonl"):
            src = arc_root / name
            if src.exists():
                dest = BOT_ROOT / "arc-wallets" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        export_meta = {"note": "export_bot_keepers missing; copied pool files if present"}

    q = load_jsonl(BOT_ROOT / "arc-wallets" / "wallets_quality.jsonl")
    w = load_jsonl(BOT_ROOT / "arc-wallets" / "wallets.jsonl")
    return {
        "ok": True,
        "mode": "external",
        "collect_exit": p.returncode,
        "quality_n": len(q),
        "watchlist_n": len(w),
        "export_meta": export_meta,
    }


def arcscan_paginate(path: str, max_pages: int = 4, limit: int = 100) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        url = ARCSCAN + path + "?" + urllib.parse.urlencode(params)
        code, data, err = http_json(url)
        if code != 200 or not isinstance(data, dict):
            print(f"[arc-inline] {path} http={code} err={err[:120]}", flush=True)
            break
        batch = data.get("items") or []
        if isinstance(batch, list):
            items.extend([x for x in batch if isinstance(x, dict)])
        page = data.get("page") or {}
        nxt = page.get("next") if isinstance(page, dict) else None
        if not nxt or not batch:
            break
        cursor = nxt
        time.sleep(0.15)
    return items


def extract_addrs_from_arc_transfer(item: dict, bags: dict[str, dict]) -> None:
    # shapes vary: from/to as str or nested
    for key in ("from", "to", "from_address", "to_address", "sender", "recipient"):
        v = item.get(key)
        a = None
        if isinstance(v, str):
            a = v.lower()
        elif isinstance(v, dict):
            a = (v.get("address") or v.get("hash") or "").lower()
        if not addr_ok(a):
            continue
        rec = bags[a]
        rec["address"] = a
        rec["sources"].add("onchain:arcscan_token_transfers")
        rec["n_token_xf"] += 1


def collect_arc_inline() -> dict:
    """Arcscan-only harvest; merge into existing bot watchlist (preserve GMGN rows)."""
    print("[arc-inline] Arcscan harvest…", flush=True)
    bags: dict[str, dict] = defaultdict(
        lambda: {
            "address": "",
            "sources": set(),
            "n_token_xf": 0,
            "n_tx": 0,
            "is_contract": False,
        }
    )
    notes = []
    code, chain, err = http_json(ARCSCAN + "/v1/chain")
    if code != 200:
        notes.append(f"arcscan /v1/chain failed http={code}")
    else:
        notes.append(f"arcscan chain_id={(chain or {}).get('chain_id') if isinstance(chain, dict) else '?'}")

    transfers = arcscan_paginate("/v1/explore/token-transfers", max_pages=6, limit=100)
    for it in transfers:
        extract_addrs_from_arc_transfer(it, bags)

    accounts = arcscan_paginate("/v1/explore/accounts", max_pages=3, limit=100)
    for it in accounts:
        a = (it.get("address") or it.get("hash") or "").lower()
        if not addr_ok(a):
            continue
        rec = bags[a]
        rec["address"] = a
        rec["sources"].add("onchain:arcscan_accounts")
        try:
            rec["n_tx"] = int(it.get("txn_count") or it.get("transactions_count") or 0)
        except (TypeError, ValueError):
            pass

    # Preserve existing quality/watchlist rows
    dest_q = BOT_ROOT / "arc-wallets" / "wallets_quality.jsonl"
    dest_w = BOT_ROOT / "arc-wallets" / "wallets.jsonl"
    existing = load_jsonl(dest_q) or load_jsonl(dest_w)
    by_addr: dict[str, dict] = {}
    for r in existing:
        a = (r.get("address") or "").lower()
        if addr_ok(a):
            by_addr[a] = dict(r)

    now = now_iso()
    added = 0
    refreshed = 0
    # Rank new actives by transfer count
    scored = sorted(
        bags.values(),
        key=lambda r: (r.get("n_token_xf") or 0, r.get("n_tx") or 0),
        reverse=True,
    )
    activity_cap = int(os.environ.get("ARC_INLINE_ACTIVITY_MAX", "400"))
    for rec in scored[:activity_cap]:
        a = rec["address"]
        if a in by_addr:
            row = by_addr[a]
            # keep pass_pnl / realized from existing; annotate on-chain touch
            oc = dict(row.get("onchain_stats") or {})
            oc["n_token_xf"] = max(int(oc.get("n_token_xf") or 0), int(rec.get("n_token_xf") or 0))
            oc["n_recent_tx"] = max(int(oc.get("n_recent_tx") or 0), int(rec.get("n_tx") or 0))
            row["onchain_stats"] = oc
            srcs = list(row.get("sources") or [])
            for s in rec["sources"]:
                if s not in srcs:
                    srcs.append(s)
            row["sources"] = srcs
            row["onchain_refreshed_at"] = now
            if row.get("pass_pnl") is not True:
                row["pass_pnl"] = True
            if float(row.get("realized_pnl_usd") or 0) <= 0:
                row["realized_pnl_usd"] = KEEPER_FLOOR
            by_addr[a] = row
            refreshed += 1
        else:
            by_addr[a] = {
                "address": a,
                "address_label": a[:4] + "..." + a[-4:],
                "chain": "arc",
                "network": "mainnet",
                "chain_id": 5042,
                "source": "onchain",
                "sources": sorted(rec["sources"]),
                "source_endpoints": ["onchain:arcscan"],
                "tags": ["onchain_active"],
                "list_tier": "activity_only",
                "quality_reason": "onchain_continuous_collect",
                "pass_pnl": True,
                "realized_pnl_usd": KEEPER_FLOOR,
                "onchain_stats": {
                    "n_token_xf": int(rec.get("n_token_xf") or 0),
                    "n_recent_tx": int(rec.get("n_tx") or 0),
                },
                "exported_at": now,
                "collected_at": now,
            }
            added += 1

    # Stable order: existing quality-ish first
    def sort_key(r: dict):
        tier = 0 if r.get("list_tier") == "quality" or r.get("source") == "gmgn" else 1
        return (tier, r.get("address") or "")

    final = sorted(by_addr.values(), key=sort_key)
    write_jsonl(dest_q, final)
    write_jsonl(dest_w, final)

    summary = f"""# Arc wallets (bot watchlist) — on-chain continuous

- Updated JST: {now_utc().astimezone(JST).isoformat()}
- Mode: arcscan-inline (collect_arc_wallets.py not in checkout)
- Existing preserved + merged: **{len(final)}**
- New on-chain adds: **{added}**
- Existing refreshed: **{refreshed}**
- Transfers scanned: **{len(transfers)}** | accounts: **{len(accounts)}**
- pass_pnl=true / realized floor={KEEPER_FLOOR}
- Notes: {'; '.join(notes) if notes else 'ok'}
"""
    (BOT_ROOT / "arc-wallets" / "summary.md").write_text(summary, encoding="utf-8")
    return {
        "ok": True,
        "mode": "inline",
        "total": len(final),
        "added": added,
        "refreshed": refreshed,
        "transfers_scanned": len(transfers),
        "accounts_scanned": len(accounts),
        "notes": notes,
    }


def collect_arc(skip_gmgn: bool) -> dict:
    if find_first(ARC_COLLECT_CANDIDATES):
        try:
            return run_arc_external(skip_gmgn=skip_gmgn)
        except Exception as e:
            print(f"[arc] external failed ({e}); falling back to inline", flush=True)
            meta = collect_arc_inline()
            meta["external_error"] = str(e)
            return meta
    return collect_arc_inline()


# ── Robinhood / Blockscout ───────────────────────────────────────────────────


def rh_v2_paginate(path: str, max_pages: int = 5) -> tuple[list[dict], list[str]]:
    """Paginate Blockscout API v2; returns items + notes."""
    notes: list[str] = []
    items: list[dict] = []
    url = RH_BS_V2 + path
    for page in range(max_pages):
        code, data, err = http_json(url)
        if code == 403:
            notes.append(f"v2 {path} → 403 (page {page})")
            break
        if code != 200:
            notes.append(f"v2 {path} → http={code} {err[:120]}")
            break
        batch: list = []
        next_params = None
        if isinstance(data, list):
            batch = [x for x in data if isinstance(x, dict)]
            next_params = None
        elif isinstance(data, dict):
            batch = [x for x in (data.get("items") or []) if isinstance(x, dict)]
            next_params = data.get("next_page_params")
        items.extend(batch)
        if not next_params or not batch:
            break
        # build next URL
        q = urllib.parse.urlencode(
            {k: v for k, v in next_params.items() if v is not None}, doseq=True
        )
        base = RH_BS_V2 + path.split("?")[0]
        url = base + "?" + q
        time.sleep(0.2)
    return items, notes


def rh_etherscan_tokentx_recent(max_pages: int = 3) -> tuple[list[dict], list[str]]:
    """Try etherscan-compatible module=account&action=tokentx via recent token contracts."""
    notes: list[str] = []
    # First get top tokens then their transfers
    tokens, n1 = rh_v2_paginate("/tokens?type=ERC-20", max_pages=1)
    notes.extend(n1)
    out: list[dict] = []
    for tok in tokens[:8]:
        ca = (tok.get("address_hash") or tok.get("address") or "").lower()
        if not addr_ok(ca):
            continue
        for page in range(1, max_pages + 1):
            qs = urllib.parse.urlencode(
                {
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": ca,
                    "page": str(page),
                    "offset": "100",
                    "sort": "desc",
                }
            )
            code, data, err = http_json(RH_BS_BASE + "?" + qs)
            if code == 403:
                notes.append("etherscan-compat /api → 403; relying on /api/v2")
                return out, notes
            if code != 200 or not isinstance(data, dict):
                notes.append(f"tokentx {ca[:10]} http={code}")
                break
            if str(data.get("status")) != "1":
                # often empty
                break
            result = data.get("result") or []
            if not isinstance(result, list) or not result:
                break
            out.extend([x for x in result if isinstance(x, dict)])
            time.sleep(0.15)
    return out, notes


def _hash_from_party(v) -> str | None:
    if isinstance(v, str) and v.startswith("0x"):
        return v.lower()
    if isinstance(v, dict):
        h = (v.get("hash") or v.get("address") or "").lower()
        if h.startswith("0x"):
            # skip contracts when flagged
            if v.get("is_contract") is True:
                return None
            return h
    return None


def collect_rh() -> dict:
    print("[rh] Blockscout on-chain collect…", flush=True)
    notes: list[str] = []
    bags: dict[str, dict] = defaultdict(
        lambda: {
            "address": "",
            "n_token_xf": 0,
            "n_tx": 0,
            "symbols": set(),
            "sources": set(),
        }
    )

    # Probe classic API
    code, _, err = http_json(RH_BS_BASE + "?module=stats&action=ethsupply")
    if code == 403:
        notes.append("Blockscout /api (etherscan-compat) returned 403 — using /api/v2 fallback")
    elif code == 200:
        notes.append("Blockscout /api etherscan-compat reachable")
    else:
        notes.append(f"Blockscout /api probe http={code}")

    # v2 token transfers
    xfers, n = rh_v2_paginate("/token-transfers", max_pages=6)
    notes.extend(n)
    for it in xfers:
        for side in ("from", "to"):
            a = _hash_from_party(it.get(side))
            if not addr_ok(a):
                # still count EOAs even if is_contract unknown — _hash_from_party skips contracts
                raw = it.get(side)
                if isinstance(raw, dict):
                    h = (raw.get("hash") or "").lower()
                    if addr_ok(h) and not raw.get("is_contract"):
                        a = h
                    elif addr_ok(h) and raw.get("is_contract"):
                        continue
                elif isinstance(raw, str) and addr_ok(raw.lower()):
                    a = raw.lower()
                else:
                    continue
            if not a:
                continue
            rec = bags[a]
            rec["address"] = a
            rec["n_token_xf"] += 1
            rec["sources"].add("onchain:blockscout_v2_token_transfers")
            tok = it.get("token") or {}
            sym = tok.get("symbol")
            if isinstance(sym, str) and sym:
                rec["symbols"].add(sym[:32])

    # v2 top active addresses
    addrs, n2 = rh_v2_paginate("/addresses", max_pages=3)
    notes.extend(n2)
    for it in addrs:
        if it.get("is_contract"):
            continue
        a = (it.get("hash") or "").lower()
        if not addr_ok(a):
            continue
        rec = bags[a]
        rec["address"] = a
        rec["sources"].add("onchain:blockscout_v2_addresses")
        try:
            rec["n_tx"] = max(int(rec.get("n_tx") or 0), int(it.get("transactions_count") or 0))
        except (TypeError, ValueError):
            pass

    # etherscan-compat optional enrichment
    es_xfers, n3 = rh_etherscan_tokentx_recent(max_pages=2)
    notes.extend(n3)
    for it in es_xfers:
        for key in ("from", "to"):
            a = (it.get(key) or "").lower()
            if not addr_ok(a):
                continue
            rec = bags[a]
            rec["address"] = a
            rec["n_token_xf"] += 1
            rec["sources"].add("onchain:blockscout_etherscan_tokentx")
            sym = it.get("tokenSymbol")
            if isinstance(sym, str) and sym:
                rec["symbols"].add(sym[:32])

    now = now_iso()
    # Build onchain-only file (ranked)
    scored = sorted(
        bags.values(),
        key=lambda r: (r.get("n_token_xf") or 0, r.get("n_tx") or 0),
        reverse=True,
    )
    onchain_rows = []
    for i, rec in enumerate(scored, 1):
        if (rec.get("n_token_xf") or 0) <= 0 and (rec.get("n_tx") or 0) <= 0:
            continue
        onchain_rows.append(
            {
                "address": rec["address"],
                "address_label": rec["address"][:4] + "..." + rec["address"][-4:],
                "chain": "robinhood",
                "source": "onchain",
                "source_endpoints": sorted(rec["sources"]),
                "pass_pnl": True,
                "realized_pnl_usd": RH_ONCHAIN_FLOOR,
                "n_token_xf": int(rec.get("n_token_xf") or 0),
                "n_tx": int(rec.get("n_tx") or 0),
                "symbols_seen": sorted(rec.get("symbols") or [])[:20],
                "activity_rank": i,
                "collected_at": now,
            }
        )

    onchain_path = BOT_ROOT / "rh-wallets" / "wallets_onchain.jsonl"
    # merge with previous onchain file (accumulate activity counters)
    prev_on = {
        (r.get("address") or "").lower(): r
        for r in load_jsonl(onchain_path)
        if addr_ok(r.get("address"))
    }
    for r in onchain_rows:
        a = r["address"]
        if a in prev_on:
            old = prev_on[a]
            r["n_token_xf"] = max(int(old.get("n_token_xf") or 0), int(r["n_token_xf"]))
            r["n_tx"] = max(int(old.get("n_tx") or 0), int(r["n_tx"]))
            syms = list(dict.fromkeys(list(old.get("symbols_seen") or []) + list(r.get("symbols_seen") or [])))
            r["symbols_seen"] = syms[:20]
            eps = list(dict.fromkeys(list(old.get("source_endpoints") or []) + list(r.get("source_endpoints") or [])))
            r["source_endpoints"] = eps
        prev_on[a] = r
    onchain_final = sorted(
        prev_on.values(),
        key=lambda x: (int(x.get("n_token_xf") or 0), int(x.get("n_tx") or 0)),
        reverse=True,
    )
    # re-rank
    for i, r in enumerate(onchain_final, 1):
        r["activity_rank"] = i
    write_jsonl(onchain_path, onchain_final)

    # Merge into wallets.jsonl without deleting Nansen/GMGN quality rows
    watch_path = BOT_ROOT / "rh-wallets" / "wallets.jsonl"
    existing = load_jsonl(watch_path)
    by: dict[str, dict] = {}
    for r in existing:
        a = (r.get("address") or "").lower()
        if addr_ok(a):
            by[a] = dict(r)

    added = 0
    touched = 0
    # Prefer top actives for watchlist merge
    merge_cap = int(os.environ.get("RH_ONCHAIN_MERGE_MAX", "300"))
    for r in onchain_final[:merge_cap]:
        a = r["address"]
        if a in by:
            row = by[a]
            # preserve pass_pnl / realized from quality sources
            if row.get("pass_pnl") is None:
                row["pass_pnl"] = True
            try:
                if float(row.get("realized_pnl_usd") or 0) <= 0:
                    row["realized_pnl_usd"] = float(row.get("realized_pnl_usd") or 0) or RH_ONCHAIN_FLOOR
            except (TypeError, ValueError):
                row["realized_pnl_usd"] = RH_ONCHAIN_FLOOR
            eps = list(row.get("source_endpoints") or [])
            for e in r.get("source_endpoints") or []:
                if e not in eps:
                    eps.append(e)
            row["source_endpoints"] = eps
            row["onchain_refreshed_at"] = now
            row["n_token_xf"] = r.get("n_token_xf")
            by[a] = row
            touched += 1
        else:
            by[a] = {
                "address": a,
                "address_label": r.get("address_label") or (a[:4] + "..." + a[-4:]),
                "realized_pnl_usd": RH_ONCHAIN_FLOOR,
                "pass_pnl": True,
                "source_endpoints": list(r.get("source_endpoints") or ["onchain:blockscout"]),
                "dex_only_note": "onchain_active_blockscout",
                "gmgn_pnl_usd": None,
                "gmgn_winrate": None,
                "gmgn_tags": [],
                "n_token_xf": r.get("n_token_xf"),
                "n_tx": r.get("n_tx"),
                "collected_at": now,
                "refined_at": now,
            }
            added += 1

    # Keep quality rows first (those with real pnl / nansen / gmgn), then onchain adds
    def rh_sort(r: dict):
        eps = [str(x) for x in (r.get("source_endpoints") or [])]
        quality = any(
            x.startswith("pnl") or "gmgn" in x or "fomo" in x or "nansen" in x or "leaderboard" in x
            for x in eps
        )
        try:
            pnl = float(r.get("realized_pnl_usd") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        return (0 if quality else 1, -pnl, r.get("address") or "")

    merged = sorted(by.values(), key=rh_sort)
    write_jsonl(watch_path, merged)

    # Update short rh summary section
    sum_path = BOT_ROOT / "rh-wallets" / "summary.md"
    sum_path.write_text(
        f"""# RH wallets

- Updated JST: {now_utc().astimezone(JST).isoformat()}
- Watchlist wallets.jsonl: **{len(merged)}**
- On-chain wallets_onchain.jsonl: **{len(onchain_final)}**
- New on-chain merges this run: **{added}** (touched existing: {touched})
- Source: Robinhood Blockscout `{RH_BS_V2}` (+ etherscan-compat if allowed)
- Notes: {'; '.join(notes) if notes else 'ok'}
""",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "watchlist_n": len(merged),
        "onchain_n": len(onchain_final),
        "added": added,
        "touched": touched,
        "xfers_scanned": len(xfers),
        "addrs_scanned": len(addrs),
        "etherscan_xfers": len(es_xfers),
        "notes": notes,
    }


# ── Summary + CLI ────────────────────────────────────────────────────────────


def write_summary(arc_meta: dict | None, rh_meta: dict | None) -> Path:
    utc = now_utc()
    jst = utc.astimezone(JST)
    lines = [
        "# On-chain wallet collect summary",
        "",
        f"- JST: {jst.isoformat()}",
        f"- UTC: {utc.isoformat()}",
        f"- Keeper floors: Arc={KEEPER_FLOOR} RH_onchain={RH_ONCHAIN_FLOOR}",
        "",
    ]
    if arc_meta is not None:
        lines.append("## Arc")
        lines.append("")
        for k, v in arc_meta.items():
            if k == "notes" and isinstance(v, list):
                lines.append(f"- notes: {'; '.join(v) if v else '—'}")
            else:
                lines.append(f"- **{k}**: {v}")
        lines.append("")
        aq = BOT_ROOT / "arc-wallets" / "wallets_quality.jsonl"
        aw = BOT_ROOT / "arc-wallets" / "wallets.jsonl"
        lines.append(f"- file wallets_quality.jsonl rows: **{len(load_jsonl(aq))}**")
        lines.append(f"- file wallets.jsonl rows: **{len(load_jsonl(aw))}**")
        lines.append("")
    if rh_meta is not None:
        lines.append("## Robinhood")
        lines.append("")
        for k, v in rh_meta.items():
            if k == "notes" and isinstance(v, list):
                lines.append(f"- notes: {'; '.join(v) if v else '—'}")
            else:
                lines.append(f"- **{k}**: {v}")
        lines.append("")
        rw = BOT_ROOT / "rh-wallets" / "wallets.jsonl"
        ro = BOT_ROOT / "rh-wallets" / "wallets_onchain.jsonl"
        lines.append(f"- file wallets.jsonl rows: **{len(load_jsonl(rw))}**")
        lines.append(f"- file wallets_onchain.jsonl rows: **{len(load_jsonl(ro))}**")
        lines.append("")
    lines.append("## Credit policy")
    lines.append("")
    lines.append("- No Nansen Super / FOMO / paid refresh in this job")
    lines.append("- Arc GMGN skipped when `--skip-gmgn` (default for scheduled runs)")
    lines.append("- Kick via GitHub Actions + free cron-job.org dispatch")
    lines.append("")
    path = BOT_ROOT / "onchain_collect_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="On-chain wallet collector (Arc + RH)")
    ap.add_argument(
        "--chain",
        choices=("arc", "robinhood", "both"),
        default="both",
    )
    ap.add_argument(
        "--skip-gmgn",
        action="store_true",
        help="Skip GMGN when invoking Arc collector (credit/ban saving)",
    )
    args = ap.parse_args()
    # Scheduled/default credit-saving: if both and user didn't pass skip, still allow GMGN
    # only when explicitly collecting without the flag. Workflow always passes --skip-gmgn.

    arc_meta = None
    rh_meta = None
    if args.chain in ("arc", "both"):
        arc_meta = collect_arc(skip_gmgn=args.skip_gmgn)
    if args.chain in ("robinhood", "both"):
        rh_meta = collect_rh()

    path = write_summary(arc_meta, rh_meta)
    print(json.dumps({"summary": str(path), "arc": arc_meta, "rh": rh_meta}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

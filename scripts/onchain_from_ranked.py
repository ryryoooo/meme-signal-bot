#!/usr/bin/env python3
"""Discover co-buyers / co-holders from S/A ranked seed wallets via Blockscout.

Uses tokens those seeds touched (scout_token_cas + recent token transfers).
Merges promising new addresses into rh-wallets/wallets_onchain.jsonl.
Merges into wallets.jsonl only when basic quality gates pass
(not bot-like, no $0.01 fake pnl). Prefer free on-chain (GMGN_DISABLED=1 on box).

Env:
  RANKED_PATH=rh-wallets/wallets_ranked_all.jsonl
  ONCHAIN_SEED_TIERS=S,A
  ONCHAIN_SEED_MAX=30
  ONCHAIN_TOKEN_MAX=40
  ONCHAIN_BS_PAGES=4
  ONCHAIN_MERGE_WATCH=0
  ONCHAIN_MIN_COSEED=2
  GMGN_DISABLED=1
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RANKED_DEFAULT = ROOT / "rh-wallets" / "wallets_ranked_all.jsonl"
WATCH_DEFAULT = ROOT / "rh-wallets" / "wallets.jsonl"
ONCHAIN_PATH = ROOT / "rh-wallets" / "wallets_onchain.jsonl"
SUMMARY_PATH = ROOT / "rh-wallets" / "summary_onchain_ranked.md"

UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)
RH_BS_V2 = os.environ.get(
    "RH_BLOCKSCOUT_API_V2", "https://robinhoodchain.blockscout.com/api/v2"
)
SYSTEM_SKIP = {
    "0x0000000000000000000000000000000000000000",
    "0xfffffffffffffffffffffffffffffffffffffffe",
}


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def addr_ok(a: str | None) -> bool:
    if not a:
        return False
    a = a.lower()
    if a in SYSTEM_SKIP or a.startswith("0x000000000000000000000000000000000000"):
        return False
    return len(a) == 42 and a.startswith("0x")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def http_json(url: str, timeout: int = 30) -> tuple[int, dict | list | None, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if raw.lstrip().startswith("<"):
                return resp.status, None, "html_cf"
            try:
                return resp.status, json.loads(raw), ""
            except json.JSONDecodeError:
                return resp.status, None, raw[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return e.code, None, body
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def party(v) -> str | None:
    if isinstance(v, str) and v.startswith("0x"):
        a = v.lower()
        return a[:42] if addr_ok(a) else None
    if isinstance(v, dict):
        if v.get("is_contract") is True:
            return None
        h = v.get("hash") or v.get("address") or ""
        if isinstance(h, str):
            h = h.lower()
            return h[:42] if addr_ok(h) else None
    return None


def paginate(path_suffix: str, max_pages: int) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    url = RH_BS_V2.rstrip("/") + path_suffix
    items: list[dict] = []
    for page in range(max_pages):
        code, data, err = http_json(url)
        if code == 403:
            notes.append(f"403:{path_suffix[:40]}")
            break
        if code != 200 or not isinstance(data, dict):
            notes.append(f"http={code}:{err[:60]}")
            break
        batch = [x for x in (data.get("items") or []) if isinstance(x, dict)]
        items.extend(batch)
        nxt = data.get("next_page_params") or data.get("next_page_params")
        if not nxt or not batch:
            break
        q = urllib.parse.urlencode({k: v for k, v in nxt.items() if v is not None})
        base = RH_BS_V2.rstrip("/") + path_suffix.split("?")[0]
        url = base + "?" + q
        time.sleep(0.25)
    return items, notes


def collect_seed_tokens(seeds: list[dict], token_max: int) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    cas: list[str] = []
    seen: set[str] = set()
    for s in seeds:
        for ca in list(s.get("scout_token_cas") or []) + list(s.get("scout_token_cas") or []):
            c = str(ca).lower()
            if c.startswith("0x") and len(c) == 42 and c not in seen:
                seen.add(c)
                cas.append(c)
                if len(cas) >= token_max:
                    return cas, notes
    pages = int(os.environ.get("ONCHAIN_SEED_TX_PAGES", "2"))
    for s in seeds[:12]:
        a = (s.get("address") or "").lower()
        if not addr_ok(a):
            continue
        items, n = paginate(f"/addresses/{a}/token-transfers", max_pages=pages)
        notes.extend(n)
        for it in items:
            tok = it.get("token") or {}
            c = tok.get("address_hash") or tok.get("address") or ""
            if isinstance(c, dict):
                c = c.get("hash") or c.get("address") or ""
            c = str(c).lower()
            if c.startswith("0x") and len(c) == 42 and c not in seen:
                seen.add(c)
                cas.append(c)
                if len(cas) >= token_max:
                    return cas, notes
        time.sleep(0.2)
    return cas, notes


def harvest_cobuyers(
    token_cas: list[str], seed_set: set[str], max_pages: int
) -> tuple[dict[str, dict], list[str]]:
    notes: list[str] = []
    bags: dict[str, dict] = defaultdict(
        lambda: {
            "address": "",
            "co_seed_tokens": set(),
            "n_token_xf": 0,
            "symbols": set(),
            "sources": set(),
        }
    )
    for ca in token_cas:
        holders, n1 = paginate(f"/tokens/{ca}/holders", max_pages=max_pages)
        xfers, n2 = paginate(f"/tokens/{ca}/transfers?type=token_transfer", max_pages=max_pages)
        notes.extend(n1)
        notes.extend(n2)
        addrs_here: set[str] = set()
        seed_on = False
        for it in holders + xfers:
            for key in ("address", "from", "to", "token_holder"):
                a = party(it.get(key))
                if not a:
                    continue
                if a in seed_set:
                    seed_on = True
                    continue
                addrs_here.add(a)
                rec = bags[a]
                rec["address"] = a
                rec["n_token_xf"] += 1
                rec["sources"].add("onchain:ranked_cobuy")
                tok = it.get("token") or {}
                sym = tok.get("symbol")
                if isinstance(sym, str) and sym:
                    rec["symbols"].add(sym[:32])
        for a in addrs_here:
            bags[a]["co_seed_tokens"].add(ca)
            if seed_on:
                bags[a]["sources"].add("onchain:seed_token_overlap")
        print(
            f"cobuy token={ca[:10]}… holders={len(holders)} xfers={len(xfers)} addrs={len(addrs_here)}"
        )
        time.sleep(0.3)
    return bags, notes


def basic_quality_ok(row: dict) -> bool:
    try:
        rp = float(row.get("realized_pnl_usd")) if row.get("realized_pnl_usd") is not None else None
    except (TypeError, ValueError):
        rp = None
    if rp is not None and 0 < rp < 1.0:
        return False
    if int(row.get("co_seed_token_n") or 0) < int(os.environ.get("ONCHAIN_MIN_COSEED", "2")):
        return False
    if int(row.get("n_token_xf") or 0) < 2:
        return False
    label = str(row.get("address_label") or "").lower()
    if any(x in label for x in ("router", "pool", "pair", "factory", "bridge", "mev")):
        return False
    return True


def main() -> int:
    ranked_path = Path(os.environ.get("RANKED_PATH", str(RANKED_DEFAULT)))
    if not ranked_path.is_absolute():
        ranked_path = ROOT / ranked_path
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(WATCH_DEFAULT)))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path

    tiers = {
        t.strip().upper()
        for t in os.environ.get("ONCHAIN_SEED_TIERS", "S,A").split(",")
        if t.strip()
    }
    seed_max = int(os.environ.get("ONCHAIN_SEED_MAX", "30"))
    token_max = int(os.environ.get("ONCHAIN_TOKEN_MAX", "40"))
    bs_pages = int(os.environ.get("ONCHAIN_BS_PAGES", "4"))
    merge_watch = env_bool("ONCHAIN_MERGE_WATCH", False)

    ranked = load_jsonl(ranked_path)
    seeds = [r for r in ranked if str(r.get("rank_tier") or r.get("tier") or "").upper() in tiers]
    seeds.sort(key=lambda r: -float(r.get("rank_score") or r.get("score") or 0))
    seeds = seeds[:seed_max]
    seed_set = {
        (r.get("address") or "").lower()
        for r in seeds
        if addr_ok(r.get("address"))
    }
    print(f"onchain_ranked seeds={len(seeds)} tiers={sorted(tiers)}")
    if not seeds:
        print("onchain_ranked no seeds; skip")
        return 0

    token_cas, notes = collect_seed_tokens(seeds, token_max)
    print(f"onchain_ranked tokens={len(token_cas)}")
    bags, notes2 = harvest_cobuyers(token_cas, seed_set, bs_pages)
    notes.extend(notes2)

    now = now_iso()
    new_rows = []
    for rec in bags.values():
        a = rec["address"]
        if a in seed_set:
            continue
        new_rows.append(
            {
                "address": a,
                "address_label": a[:4] + "..." + a[-4:],
                "chain": "robinhood",
                "source": "onchain_ranked",
                "source_endpoints": sorted(rec["sources"]),
                "tags": ["onchain_cobuy", "from_rank_seed"],
                "list_tier": "activity_only",
                "pass_pnl": False,
                "realized_pnl_usd": None,
                "n_token_xf": int(rec.get("n_token_xf") or 0),
                "co_seed_token_n": len(rec.get("co_seed_tokens") or []),
                "co_seed_tokens": sorted(rec.get("co_seed_tokens") or [])[:20],
                "symbols_seen": sorted(rec.get("symbols") or [])[:20],
                "collected_at": now,
                "quality_reason": "ranked_seed_cobuy",
            }
        )
    new_rows.sort(
        key=lambda r: (-int(r.get("co_seed_token_n") or 0), -int(r.get("n_token_xf") or 0))
    )

    prev = {
        (r.get("address") or "").lower(): r
        for r in load_jsonl(ONCHAIN_PATH)
        if addr_ok(r.get("address"))
    }
    added_onchain = 0
    for r in new_rows:
        a = r["address"]
        if a not in prev:
            prev[a] = r
            added_onchain += 1
            continue
        old = prev[a]
        old["n_token_xf"] = max(int(old.get("n_token_xf") or 0), int(r["n_token_xf"]))
        old["co_seed_token_n"] = max(
            int(old.get("co_seed_token_n") or 0), int(r["co_seed_token_n"])
        )
        tags = list(old.get("tags") or [])
        for t in r.get("tags") or []:
            if t not in tags:
                tags.append(t)
        old["tags"] = tags
        eps = list(old.get("source_endpoints") or [])
        for e in r.get("source_endpoints") or []:
            if e not in eps:
                eps.append(e)
        old["source_endpoints"] = eps
        old["onchain_ranked_at"] = now
        prev[a] = old

    onchain_final = sorted(
        prev.values(),
        key=lambda x: (int(x.get("co_seed_token_n") or 0), int(x.get("n_token_xf") or 0)),
        reverse=True,
    )
    write_jsonl(ONCHAIN_PATH, onchain_final)

    merged_watch = 0
    if merge_watch:
        watch = {
            (r.get("address") or "").lower(): r
            for r in load_jsonl(watch_path)
            if addr_ok(r.get("address"))
        }
        for r in new_rows:
            if not basic_quality_ok(r):
                continue
            a = r["address"]
            if a in watch:
                o = watch[a]
                tags = list(o.get("tags") or [])
                for t in ("onchain_cobuy", "from_rank_seed", "rank_discovered"):
                    if t not in tags:
                        tags.append(t)
                o["tags"] = tags
                if o.get("realized_pnl_usd") is None:
                    o["pass_pnl"] = False
                o["co_seed_token_n"] = max(
                    int(o.get("co_seed_token_n") or 0), int(r.get("co_seed_token_n") or 0)
                )
                watch[a] = o
            else:
                row = dict(r)
                row["pass_pnl"] = False
                row["realized_pnl_usd"] = None
                row["tags"] = list(row.get("tags") or []) + ["rank_discovered"]
                row["list_tier"] = "scout"
                row["quality_reason"] = "ranked_cobuy_pending_vet"
                watch[a] = row
            merged_watch += 1
        write_jsonl(watch_path, [watch[a] for a in sorted(watch)])

    blockers = []
    if any("403" in n for n in notes):
        blockers.append("blockscout_403_cf")
    if not token_cas:
        blockers.append("no_seed_tokens")
    if not new_rows and token_cas:
        blockers.append("no_cobuyers_found")

    SUMMARY_PATH.write_text(
        f"""# On-chain expand from ranked S/A seeds

- Updated (UTC): {now}
- Seeds used: **{len(seeds)}** (tiers={sorted(tiers)})
- Tokens scanned: **{len(token_cas)}**
- Co-buyer candidates: **{len(new_rows)}**
- Added to onchain file: +**{added_onchain}** (file n={len(onchain_final)})
- Merged to watch: **{merged_watch}** (ONCHAIN_MERGE_WATCH={int(merge_watch)})
- Min co-seed tokens: {os.environ.get("ONCHAIN_MIN_COSEED", "2")}
- Blockers: {", ".join(blockers) if blockers else "none"}
- Notes: {"; ".join(notes[:8]) if notes else "ok"}
""",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "seeds": len(seeds),
                "tokens": len(token_cas),
                "candidates": len(new_rows),
                "added_onchain": added_onchain,
                "onchain_n": len(onchain_final),
                "merged_watch": merged_watch,
                "blockers": blockers,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

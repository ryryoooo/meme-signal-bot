#!/usr/bin/env python3
"""Free harvest: 985monitor + degentape (TheMaran stack). No GMGN / no Super."""
from __future__ import annotations
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FOMO_DIR = ROOT / "fomo-wallets"
WATCH = Path(os.environ.get("WATCHLIST_PATH") or (ROOT / "rh-wallets" / "wallets.jsonl"))
UA = {"User-Agent": "meme-signal-bot/themaran-stack"}
LB_URL = "https://985monitor.xyz/fomo-leaderboards.json"
PROFILE_URL = "https://985monitor.xyz/api/fomo-watch/profile"
TAPE_URL = "https://degentape.com/api/tape"
CATCHUP_URL = "https://degentape.com/api/catchup"
STATS_URL = "https://degentape.com/api/stats"
PROFILE_CAP = int(os.environ.get("THEMARAN_PROFILE_CAP") or "60")
PROFILE_SLEEP = float(os.environ.get("THEMARAN_PROFILE_SLEEP") or "0.35")
TAPE_MIN_BUY_USD = float(os.environ.get("THEMARAN_TAPE_MIN_BUY_USD") or "80")
MERGE_WATCH = os.environ.get("THEMARAN_MERGE_WATCH", "1") != "0"
MIN_BOARD_PNL = float(os.environ.get("THEMARAN_MIN_BOARD_PNL") or "10000")
MIN_TAPE_BUY_USD = float(os.environ.get("THEMARAN_MIN_TAPE_BUY_USD") or "400")
MIN_TAPE_BUYS = int(os.environ.get("THEMARAN_MIN_TAPE_BUYS") or "2")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def http_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out

def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

def addr_key(a: str | None) -> str:
    return (a or "").strip().lower()

def harvest_985() -> tuple[list[dict], list[dict], dict]:
    data = http_json(LB_URL)
    boards = data.get("boards") or {}
    lb_rows: list[dict] = []
    handles: dict[str, dict] = {}
    for window, rows in boards.items():
        for r in rows or []:
            handle = (r.get("handle") or "").strip()
            if not handle:
                continue
            row = {
                "handle": handle,
                "displayName": r.get("name") or handle,
                "window": window,
                "pnlUsd": float(r.get("pnl") or 0),
                "volumeUsd": float(r.get("volume") or 0),
                "numTrades": int(r.get("numTrades") or 0),
                "followers": int(r.get("followers") or 0),
                "twitter": r.get("twitter") or "",
                "rank": r.get("rank"),
                "source": "985monitor_leaderboard",
                "updatedAt": data.get("updatedAt"),
            }
            lb_rows.append(row)
            prev = handles.get(handle.lower())
            if not prev or float(row["pnlUsd"]) > float(prev.get("bestPnl") or 0):
                handles[handle.lower()] = {
                    "handle": handle,
                    "displayName": row["displayName"],
                    "bestPnl": row["pnlUsd"],
                    "bestWindow": window,
                    "volumeUsd": row["volumeUsd"],
                    "numTrades": row["numTrades"],
                    "followers": row["followers"],
                }
    ordered = sorted(
        handles.values(),
        key=lambda o: (0 if o.get("bestWindow") in ("24h", "7d") else 1, -float(o.get("bestPnl") or 0)),
    )
    resolved: list[dict] = []
    stats = {"boards": {k: len(v or []) for k, v in boards.items()}, "profiles_ok": 0, "profiles_fail": 0, "evm": 0}
    for i, h in enumerate(ordered[:PROFILE_CAP]):
        handle = h["handle"]
        try:
            prof = http_json(f"{PROFILE_URL}?handle={urllib.parse.quote(handle)}")
            wallets = (prof.get("wallets") or {}) if isinstance(prof, dict) else {}
            evm = [addr_key(x) for x in (wallets.get("evm") or []) if addr_key(x).startswith("0x") and len(addr_key(x)) == 42]
            sol = [x for x in (wallets.get("sol") or []) if isinstance(x, str) and len(x) >= 32]
            if not evm:
                stats["profiles_fail"] += 1
            else:
                stats["profiles_ok"] += 1
                stats["evm"] += len(evm)
                for a in evm:
                    resolved.append({
                        "address": a,
                        "handle": handle,
                        "displayName": h.get("displayName") or handle,
                        "pnlUsd": float(h.get("bestPnl") or 0),
                        "volumeUsd": float(h.get("volumeUsd") or 0),
                        "numTrades": int(h.get("numTrades") or 0),
                        "followers": int(h.get("followers") or 0),
                        "bestWindow": h.get("bestWindow"),
                        "solana": sol[0] if sol else None,
                        "source": "985monitor_profile",
                        "source_endpoints": ["985monitor:leaderboard", "985monitor:profile"],
                        "tags": ["fomo", "985monitor", "themaran"],
                        "harvested_at": now_iso(),
                    })
        except Exception:
            stats["profiles_fail"] += 1
        time.sleep(PROFILE_SLEEP)
        if i and i % 20 == 0:
            print(f"985 profiles {i}/{min(PROFILE_CAP, len(ordered))}", flush=True)
    return lb_rows, resolved, stats

def harvest_degentape() -> tuple[list[dict], dict]:
    tape = http_json(TAPE_URL)
    catchup, stats_api = {}, {}
    try:
        catchup = http_json(CATCHUP_URL)
    except Exception as e:
        catchup = {"error": str(e)}
    try:
        stats_api = http_json(STATS_URL)
    except Exception as e:
        stats_api = {"error": str(e)}
    rows = tape.get("rows") or []
    agg: dict[str, dict] = {}
    rh = 0
    for r in rows:
        if r.get("chain") != "robinhood":
            continue
        rh += 1
        w = addr_key(r.get("wallet"))
        if not (w.startswith("0x") and len(w) == 42):
            continue
        cur = agg.setdefault(w, {
            "address": w, "handle": r.get("handle"), "displayName": r.get("display_name") or r.get("handle"),
            "solana": r.get("solana"), "buy_usd": 0.0, "sell_usd": 0.0, "buys": 0, "sells": 0,
            "first_buys": 0, "tokens": set(), "max_buy_usd": 0.0, "last_ts": 0,
            "source": "degentape_tape", "tags": ["fomo", "degentape", "themaran", "live_tape"],
        })
        side = (r.get("side") or "").lower()
        usd = float(r.get("usd") or 0)
        if side == "buy":
            cur["buys"] += 1; cur["buy_usd"] += usd; cur["max_buy_usd"] = max(cur["max_buy_usd"], usd)
            if int(r.get("first_buy") or 0) == 1:
                cur["first_buys"] += 1
        elif side == "sell":
            cur["sells"] += 1; cur["sell_usd"] += usd
        if r.get("token"):
            cur["tokens"].add(str(r.get("token")).lower())
        if r.get("handle"):
            cur["handle"] = r.get("handle")
        if r.get("display_name"):
            cur["displayName"] = r.get("display_name")
        if r.get("solana"):
            cur["solana"] = r.get("solana")
        cur["last_ts"] = max(int(cur["last_ts"] or 0), int(r.get("ts") or 0))
    for ran in catchup.get("ran") or []:
        if not isinstance(ran, dict):
            continue
        for tb in ran.get("top_buyers") or []:
            w = addr_key(tb.get("wallet") or tb.get("address"))
            if not (w.startswith("0x") and len(w) == 42):
                continue
            cur = agg.setdefault(w, {
                "address": w, "handle": tb.get("handle"), "displayName": tb.get("display_name") or tb.get("handle"),
                "solana": tb.get("solana"), "buy_usd": 0.0, "sell_usd": 0.0, "buys": 0, "sells": 0,
                "first_buys": 0, "tokens": set(), "max_buy_usd": 0.0, "last_ts": 0,
                "source": "degentape_catchup", "tags": ["fomo", "degentape", "themaran", "catchup"],
            })
            cur["buy_usd"] += float(tb.get("buy_usd") or tb.get("usd") or 0)
            cur["buys"] += int(tb.get("buys") or 1)
            if tb.get("handle"):
                cur["handle"] = tb.get("handle")
            tags = list(cur.get("tags") or [])
            if "catchup" not in tags:
                tags.append("catchup")
            cur["tags"] = tags
    out = []
    for w, cur in agg.items():
        if float(cur["buy_usd"]) < TAPE_MIN_BUY_USD and int(cur["buys"]) < 1:
            continue
        out.append({
            "address": w, "handle": cur.get("handle"), "displayName": cur.get("displayName") or cur.get("handle"),
            "solana": cur.get("solana"), "buyUsd": round(float(cur["buy_usd"]), 2),
            "sellUsd": round(float(cur["sell_usd"]), 2), "buys": int(cur["buys"]), "sells": int(cur["sells"]),
            "firstBuys": int(cur["first_buys"]), "tokenCount": len(cur["tokens"]),
            "maxBuyUsd": round(float(cur["max_buy_usd"]), 2), "lastTs": cur.get("last_ts"), "pnlUsd": None,
            "source": cur.get("source"), "source_endpoints": ["degentape:tape", "degentape:catchup"],
            "tags": list(cur.get("tags") or []), "harvested_at": now_iso(),
        })
    out.sort(key=lambda o: (-float(o.get("buyUsd") or 0), -int(o.get("buys") or 0)))
    meta = {
        "tape_rows": len(rows), "rh_rows": rh, "wallets": len(out),
        "stats_fills": ((stats_api.get("stats") or {}).get("fills") if isinstance(stats_api, dict) else None),
        "catchup_fills": ((catchup.get("summary") or {}).get("fills") if isinstance(catchup, dict) else None),
    }
    return out, meta

def merge_fomo_evm(existing: list[dict], news: list[dict]) -> list[dict]:
    by = {addr_key(o.get("address")): dict(o) for o in existing if addr_key(o.get("address")).startswith("0x")}
    for n in news:
        a = addr_key(n.get("address"))
        if not a.startswith("0x"):
            continue
        cur = by.get(a)
        if not cur:
            by[a] = {
                "address": a, "handle": n.get("handle"), "displayName": n.get("displayName") or n.get("handle"),
                "pnlUsd": n.get("pnlUsd"), "solana": n.get("solana"), "source": n.get("source") or "themaran",
                "tags": list(n.get("tags") or ["fomo", "themaran"]), "buyUsd": n.get("buyUsd"), "buys": n.get("buys"),
                "bestWindow": n.get("bestWindow"), "harvested_at": n.get("harvested_at") or now_iso(),
            }
            continue
        if n.get("handle") and not cur.get("handle"):
            cur["handle"] = n.get("handle")
        if n.get("displayName"):
            cur["displayName"] = n.get("displayName")
        if n.get("solana") and not cur.get("solana"):
            cur["solana"] = n.get("solana")
        if n.get("pnlUsd") is not None:
            try:
                cur["pnlUsd"] = max(float(cur.get("pnlUsd") or 0), float(n["pnlUsd"]))
            except Exception:
                cur["pnlUsd"] = n.get("pnlUsd")
        if n.get("buyUsd") is not None:
            try:
                cur["buyUsd"] = max(float(cur.get("buyUsd") or 0), float(n["buyUsd"]))
            except Exception:
                pass
        tags = list(cur.get("tags") or [])
        for t in n.get("tags") or []:
            if t and t not in tags:
                tags.append(t)
        cur["tags"] = tags
        src = cur.get("source") or ""
        if "themaran" not in str(src):
            cur["source"] = f"{src},themaran" if src else "themaran"
        cur["harvested_at"] = now_iso()
        by[a] = cur
    rows = list(by.values())
    rows.sort(key=lambda o: (-float(o.get("pnlUsd") or o.get("buyUsd") or 0), addr_key(o.get("address"))))
    return rows

def maybe_merge_watch(resolved_985: list[dict], tape: list[dict]) -> dict:
    if not MERGE_WATCH or not WATCH.exists():
        return {"merged": 0, "skipped": "disabled_or_missing"}
    watch = load_jsonl(WATCH)
    by = {addr_key(o.get("address") or o.get("wallet")): dict(o) for o in watch if addr_key(o.get("address") or o.get("wallet")).startswith("0x")}
    added = updated = 0
    def promote(addr: str, meta: dict, reason: str) -> None:
        nonlocal added, updated
        a = addr_key(addr)
        tags_extra = ["fomo", "themaran"] + (["985monitor"] if reason == "985" else ["degentape", "live_tape"])
        cur = by.get(a)
        if not cur:
            by[a] = {
                "address": a, "labels": [meta.get("handle") or meta.get("displayName") or "themaran"],
                "tags": tags_extra, "sources": ["themaran_stack"],
                "source_endpoints": list(meta.get("source_endpoints") or [meta.get("source") or "themaran"]),
                "fomo_handle": meta.get("handle"), "fomo_pnl_usd": meta.get("pnlUsd"),
                "realized_pnl_usd": meta.get("pnlUsd"), "notes": f"themaran {reason}", "added_at": now_iso(),
            }
            added += 1
            return
        tags = list(cur.get("tags") or [])
        for t in tags_extra:
            if t not in tags:
                tags.append(t)
        cur["tags"] = tags
        srcs = list(cur.get("sources") or [])
        if "themaran_stack" not in srcs:
            srcs.append("themaran_stack")
        cur["sources"] = srcs
        if meta.get("handle"):
            cur["fomo_handle"] = meta.get("handle")
        if meta.get("pnlUsd") is not None:
            try:
                cur["fomo_pnl_usd"] = max(float(cur.get("fomo_pnl_usd") or 0), float(meta["pnlUsd"]))
            except Exception:
                cur["fomo_pnl_usd"] = meta.get("pnlUsd")
        updated += 1
        by[a] = cur
    for r in resolved_985:
        if float(r.get("pnlUsd") or 0) >= MIN_BOARD_PNL:
            promote(r["address"], r, "985")
    for r in tape:
        if float(r.get("buyUsd") or 0) >= MIN_TAPE_BUY_USD or int(r.get("buys") or 0) >= MIN_TAPE_BUYS:
            promote(r["address"], r, "tape")
    write_jsonl(WATCH, list(by.values()))
    return {"merged": added, "updated": updated, "watch_total": len(by)}

def main() -> int:
    FOMO_DIR.mkdir(parents=True, exist_ok=True)
    print("harvest 985monitor…", flush=True)
    lb_rows, resolved_985, s985 = harvest_985()
    print("harvest degentape…", flush=True)
    tape_rows, sdt = harvest_degentape()
    write_jsonl(FOMO_DIR / "leaderboard.jsonl", lb_rows)
    existing = load_jsonl(FOMO_DIR / "wallets_evm.jsonl")
    news = resolved_985 + [{
        "address": t["address"], "handle": t.get("handle"), "displayName": t.get("displayName"),
        "pnlUsd": None, "solana": t.get("solana"), "source": t.get("source"), "tags": t.get("tags"),
        "buyUsd": t.get("buyUsd"), "buys": t.get("buys"), "harvested_at": t.get("harvested_at"),
    } for t in tape_rows]
    merged_fomo = merge_fomo_evm(existing, news)
    write_jsonl(FOMO_DIR / "wallets_evm.jsonl", merged_fomo)
    write_jsonl(FOMO_DIR / "degentape_wallets.jsonl", tape_rows)
    watch_stats = maybe_merge_watch(resolved_985, tape_rows)
    summary = [
        "# TheMaran stack harvest", "",
        f"- Updated: **{now_iso()}**",
        "- Sources: 985monitor + degentape (free)",
        f"- 985 boards: `{s985.get('boards')}` profiles ok/fail **{s985.get('profiles_ok')}**/{s985.get('profiles_fail')}",
        f"- degentape RH wallets **{sdt.get('wallets')}** (tape {sdt.get('tape_rows')})",
        f"- fomo evm total **{len(merged_fomo)}** watch `{watch_stats}`", "", "## Top 985"
    ]
    for r in sorted(resolved_985, key=lambda o: -float(o.get("pnlUsd") or 0))[:12]:
        summary.append(f"- `{r['address'][:10]}…` @{r.get('handle')} pnl=${float(r.get('pnlUsd') or 0):,.0f}")
    summary += ["", "## Top tape"]
    for r in tape_rows[:12]:
        summary.append(f"- `{r['address'][:10]}…` @{r.get('handle')} buy=${float(r.get('buyUsd') or 0):,.0f}")
    (FOMO_DIR / "summary_themaran.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(json.dumps({"985": s985, "degentape": sdt, "fomo_total": len(merged_fomo), "watch": watch_stats}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

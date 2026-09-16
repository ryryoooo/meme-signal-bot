#!/usr/bin/env python3
"""Harvest wallets with realized profit on a token (GMGN traders) into Arc/RH watchlists."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gmgn_raw(args: list[str]) -> dict | list | None:
    cmd = ["gmgn-cli", *args, "--raw"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        print(f"gmgn fail rc={p.returncode}: {err[:500]}", file=sys.stderr)
        return None
    out = (p.stdout or "").strip()
    if not out:
        return None
    return json.loads(out)


def extract_list(data) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for k in ("data", "list", "traders", "holders", "result"):
        v = data.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in ("list", "traders", "holders", "data", "items"):
                if isinstance(v.get(k2), list):
                    return v[k2]
    return []


def wallet_addr(row: dict) -> str | None:
    for k in ("address", "wallet_address", "walletAddress", "owner", "account_address"):
        v = row.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            return v.lower()
        if isinstance(v, dict):
            a = v.get("address") or v.get("wallet_address")
            if isinstance(a, str) and a.startswith("0x"):
                return a.lower()
    return None


def num(x):
    try:
        if x is None:
            return None
        if isinstance(x, str):
            s = x.replace("$", "").replace(",", "").replace("%", "").strip()
            if not s:
                return None
            return float(s)
        return float(x)
    except Exception:
        return None


def profit_of(row: dict) -> float:
    for k in (
        "profit",
        "realized_profit",
        "realized_pnl",
        "realized_profit_usd",
        "pnl",
        "total_profit",
        "profit_usd",
    ):
        v = num(row.get(k))
        if v is not None:
            return v
    for nest in ("pnl", "stats", "profit_info"):
        d = row.get(nest)
        if isinstance(d, dict):
            for k in ("profit", "realized_profit", "realized_pnl", "pnl", "usd"):
                v = num(d.get(k))
                if v is not None:
                    return v
    return 0.0


def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        a = (o.get("address") or "").lower()
        if a:
            out[a] = o
    return out


def write_jsonl(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rows[a], ensure_ascii=False) for a in sorted(rows)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default=os.environ.get("CHAIN", "arc"))
    ap.add_argument("--token", required=True)
    ap.add_argument("--symbol", default=os.environ.get("TOKEN_SYMBOL", ""))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--min-profit", type=float, default=float(os.environ.get("MIN_PROFIT_USD", "1")))
    ap.add_argument("--max-avg-cost", type=float, default=float(os.environ.get("MAX_AVG_COST", "0") or 0))
    ap.add_argument("--max-start-holding-ts", type=float, default=float(os.environ.get("MAX_START_HOLDING_TS", "0") or 0))
    ap.add_argument(
        "--watchlist",
        default="",
        help="default: arc-wallets/wallets.jsonl or rh-wallets/wallets.jsonl",
    )
    args = ap.parse_args()
    chain = args.chain.strip().lower()
    if chain in ("robinhood", "rh"):
        chain = "robinhood"
        default_wl = ROOT / "rh-wallets" / "wallets.jsonl"
        chain_id = None
    else:
        chain = "arc"
        default_wl = ROOT / "arc-wallets" / "wallets.jsonl"
        chain_id = 5042

    wl_path = Path(args.watchlist) if args.watchlist else default_wl
    if not wl_path.is_absolute():
        wl_path = ROOT / wl_path

    token = args.token.strip().lower()
    print(f"harvest chain={chain} token={token} limit={args.limit} min_profit={args.min_profit}")

    traders = gmgn_raw(
        [
            "token",
            "traders",
            "--chain",
            "arc" if chain == "arc" else "robinhood",
            "--address",
            token,
            "--limit",
            str(min(100, max(1, args.limit))),
            "--order-by",
            "profit",
            "--direction",
            "desc",
        ]
    )
    items = extract_list(traders)
    print(f"traders_raw={len(items)}")
    if not items:
        print("no traders; abort without touching watchlist", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).isoformat()
    profitable: list[dict] = []
    for row in items:
        addr = wallet_addr(row)
        if not addr:
            continue
        pr = profit_of(row)
        if pr < args.min_profit:
            continue
        avg_cost = num(row.get("avg_cost"))
        if args.max_avg_cost and args.max_avg_cost > 0:
            if avg_cost is None or avg_cost <= 0 or avg_cost > args.max_avg_cost:
                continue
        start_h = num(row.get("start_holding_at"))
        if args.max_start_holding_ts and args.max_start_holding_ts > 0:
            if start_h is None or start_h <= 0 or start_h > args.max_start_holding_ts:
                continue
        tags = row.get("tags") or row.get("tag") or []
        if isinstance(tags, str):
            tags = [tags]
        profitable.append(
            {
                "address": addr,
                "realized_profit": pr,
                "unrealized_profit": num(row.get("unrealized_profit") or row.get("unrealized_pnl")) or 0.0,
                "buy_volume_cur": num(row.get("buy_volume_cur") or row.get("buy_volume")),
                "avg_cost": avg_cost,
                "start_holding_at": start_h,
                "sell_volume_cur": num(row.get("sell_volume_cur") or row.get("sell_volume")),
                "tags": tags,
                "raw": {k: row.get(k) for k in list(row.keys())[:40]},
            }
        )
    profitable.sort(key=lambda r: -r["realized_profit"])
    print(f"profitable>={args.min_profit}: {len(profitable)}")
    for r in profitable[:15]:
        print(f"  {r['address']} profit=${r['realized_profit']:.2f}")

    out_raw = ROOT / ("arc-wallets" if chain == "arc" else "rh-wallets") / "raw"
    out_raw.mkdir(parents=True, exist_ok=True)
    sym = args.symbol or "token"
    (out_raw / f"{sym}_traders.json").write_text(json.dumps(traders, indent=2), encoding="utf-8")
    (out_raw / f"{sym}_profit_wallets.json").write_text(
        json.dumps(
            {"token": token, "chain": chain, "symbol": sym, "fetched_at": now, "profitable": profitable},
            indent=2,
        ),
        encoding="utf-8",
    )

    existing = load_jsonl(wl_path)
    before = len(existing)
    added = 0
    updated = 0
    for r in profitable:
        addr = r["address"]
        short = f"{addr[:4]}...{addr[-4:]}"
        pr = float(r["realized_profit"])
        prev = existing.get(addr)
        if prev is None:
            row = {
                "address": addr,
                "sources": [f"gmgn:token_traders:{sym}"],
                "tags": list({*(r.get("tags") or []), "token_profit", f"{sym}_profit", short}),
                "labels": [short],
                "pnl_hints": {"realized_pnl_usd": pr, "source_token": token, "source_symbol": sym},
                "onchain_stats": {},
                "n_tokens_seen": 1,
                "symbols_seen": [sym] if sym else [],
                "address_label": short,
                "chain": "arc" if chain == "arc" else "robinhood",
                "network": "mainnet",
                "source": "gmgn",
                "source_endpoints": ["gmgn", "gmgn:token_traders"],
                "profit_tagged": True,
                "list_tier": "quality",
                "quality_reason": f"token_profit:{sym}",
                "pass_pnl": True,
                "realized_pnl_usd": pr,
                "exported_at": now,
                "source_token": token,
                "source_symbol": sym,
            }
            if chain_id:
                row["chain_id"] = chain_id
            existing[addr] = row
            added += 1
        else:
            prev["pass_pnl"] = True
            prev["profit_tagged"] = True
            prev["realized_pnl_usd"] = max(float(prev.get("realized_pnl_usd") or 0), pr)
            srcs = list(prev.get("sources") or [])
            tag = f"gmgn:token_traders:{sym}"
            if tag not in srcs:
                srcs.append(tag)
            prev["sources"] = srcs
            tags = list(prev.get("tags") or [])
            for t in ("token_profit", f"{sym}_profit"):
                if t not in tags:
                    tags.append(t)
            prev["tags"] = tags
            prev["source_token"] = token
            prev["source_symbol"] = sym
            prev["exported_at"] = now
            existing[addr] = prev
            updated += 1

    write_jsonl(wl_path, existing)
    # keep quality file in sync for arc
    if chain == "arc":
        qpath = ROOT / "arc-wallets" / "wallets_quality.jsonl"
        write_jsonl(qpath, existing)

    print(f"watchlist {wl_path} before={before} after={len(existing)} added={added} updated={updated}")
    return 0 if (added + updated) > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())

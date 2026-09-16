#!/usr/bin/env python3
"""Harvest early buyers (before our live wallet) from Arc LIVE opens into arc-wallets.

Careful 精査: excludes our wallet, Uniswap/infra routers, bundler/sniper/team tags,
late buyers, and dust. Merges survivors into wallets.jsonl (+ wallets_quality.jsonl)
with tags like early_live:SYMBOL. Raw dumps go under arc-wallets/raw/.

Usage examples:
  python3 scripts/harvest_live_early_wallets.py
  python3 scripts/harvest_live_early_wallets.py --live-state /path/to/live_state_arc.json
  python3 scripts/harvest_live_early_wallets.py --token 0x.. --symbol DIG --buy-tx 0x.. --entry-price 1e-5
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARC_RPC = os.environ.get("ARC_RPC", "https://rpc.mainnet.arc.io")
OUR_WALLET = os.environ.get(
    "LIVE_WALLET_ADDRESS", "0x822AFdCc7f1Ec829f4456A3921ff54B4a6dBCfAe"
).lower()

# Known Arc Uniswap V4 / infra — never watchlist these
INFRA = {
    "0x8366a39cc670b4001a1121b8f6a443a643e40951",  # PoolManager
    "0x4fca4a51ab4f23a7447b3284fbd7d73289a89fb1",  # UniversalRouter
    "0x000000000022d473030f116ddee9f6b43ac78ba3",  # Permit2
    "0x8dc178efb8111bb0973dd9d722ebeff267c98f94",  # Quoter
    "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b",  # StateView
    "0x20eead6db6b3d0a4491e9073119dd0ebff166acc",  # hook
    "0xb6a65950534f061618b4ae102fbcbb8541a8e0cc",  # hook
    "0x3600000000000000000000000000000000000000",  # USDC native
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "0x0000000000000000000000000000000000000000",
}

BAD_TAG_SUBSTR = (
    "bundler",
    "sniper",
    "rat_trader",
    "dex_bot",
    "bot",
    "copy",
    "team",
    "dev_team",
    "creator",
    "scammer",
    "sandwich",
    "mev",
    "phish",
    "exchanger",
    "exchange",
    "pool",
    "router",
    "contract",
    "transfer_in",
)


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


def gmgn_raw(args: list[str]):
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
        if isinstance(v, str) and v.startswith("0x") and len(v) >= 42:
            return v.lower()
        if isinstance(v, dict):
            a = v.get("address") or v.get("wallet_address")
            if isinstance(a, str) and a.startswith("0x"):
                return a.lower()
    return None


def profit_of(row: dict) -> float:
    for k in (
        "realized_profit",
        "realized_pnl",
        "realized_profit_usd",
        "profit",
        "pnl",
        "total_profit",
        "profit_usd",
    ):
        v = num(row.get(k))
        if v is not None:
            return v
    return 0.0


def rpc(method: str, params: list):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    out = subprocess.check_output(
        [
            "curl",
            "-sS",
            "-A",
            "Mozilla/5.0",
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            ARC_RPC,
        ],
        text=True,
        timeout=60,
    )
    j = json.loads(out)
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def buy_ts_from_tx(tx_hash: str) -> tuple[int, int]:
    rec = rpc("eth_getTransactionReceipt", [tx_hash])
    if not rec:
        raise RuntimeError(f"no receipt for {tx_hash}")
    block = int(rec["blockNumber"], 16)
    blk = rpc("eth_getBlockByNumber", [hex(block), False])
    ts = int(blk["timestamp"], 16)
    return block, ts


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


def tag_list(row: dict) -> list[str]:
    tags = []
    for k in ("tags", "tag", "maker_token_tags", "wallet_tag_v2"):
        v = row.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            if v:
                tags.append(v)
        elif isinstance(v, list):
            tags.extend(str(x) for x in v if x)
    return tags


def has_bad_tag(row: dict) -> str | None:
    for t in tag_list(row):
        tl = str(t).lower()
        for bad in BAD_TAG_SUBSTR:
            if bad in tl:
                # allow benign: "smart_degen", "fresh_wallet", "bluechip_owner", "renowned", "kol"
                if tl in ("smart_degen", "fresh_wallet", "bluechip_owner", "renowned", "kol", "gmgn", "fomo", "top_holder"):
                    continue
                if bad == "bot" and tl in ("smart_degen",):
                    continue
                # 'bot' alone is too broad for substr in smart_degen? smart_degen has no bot
                if bad == "bot" and "bot" not in tl:
                    continue
                return f"tag:{t}"
    ex = row.get("exchange")
    if isinstance(ex, str) and ex.strip():
        return f"exchange:{ex}"
    name = (row.get("name") or "")
    if isinstance(name, str) and name:
        nl = name.lower()
        for bad in ("pool", "router", "manager", "permit", "quoter", "hook"):
            if bad in nl:
                return f"name:{name}"
    if row.get("is_suspicious") is True:
        return "is_suspicious"
    return None


def dust_fail(row: dict, min_buy: float, min_profit: float) -> bool:
    buy = num(row.get("buy_volume_cur") or row.get("buy_volume") or row.get("history_bought_cost") or row.get("total_cost")) or 0.0
    pr = profit_of(row)
    # keep if buy>=min_buy OR profit>=min_profit
    return not (buy >= min_buy or pr >= min_profit)


def load_jobs_from_live_state(path: Path) -> list[dict]:
    st = json.loads(path.read_text(encoding="utf-8"))
    positions = st.get("live_positions") or []
    jobs = []
    for p in positions:
        ca = (p.get("ca") or "").lower()
        sym = p.get("symbol") or ca[:10]
        swap = p.get("swap_open") or {}
        tx = swap.get("tx_hash") or swap.get("hash")
        ep = num(p.get("entry_price"))
        if not ca or not tx:
            continue
        jobs.append(
            {
                "symbol": sym,
                "token": ca,
                "buy_tx": tx,
                "entry_price": ep,
            }
        )
    return jobs


def default_jobs() -> list[dict]:
    """Hardcoded current live opens if no live_state path."""
    return [
        {
            "symbol": "DIG",
            "token": "0x2a3fafcd38e866e6a6fbbd19883cfdde2117a501",
            "buy_tx": "0xc6ecc627abded4a816993e0ce9d21d4a45f3ecdb73bf539a8443ff354c1d2894",
            "entry_price": 8.4473658e-05,
        },
        {
            "symbol": "PayLink",
            "token": "0xa66f1d051959c4b01c8855078a12eeba53601734",
            "buy_tx": "0x09a1cfd8dfe4a9e669cf7833ffe4923738766c848b59b841bbdd6e19a5e9bcd2",
            "entry_price": 2.5932963e-05,
        },
        {
            "symbol": "MsMinara",
            "token": "0x7bf48c6a73f60e25ea53e7fa66573bdc9de2e3bf",
            "buy_tx": "0x472e5d5166c9cb505304ec384120feec3faad0385a2ca448c24a0e05af8131fc",
            "entry_price": 1.791515e-05,
        },
        {
            "symbol": "MICA",
            "token": "0x297f1a5d13ecc5ba2dc2fe2ad5d016603001f682",
            "buy_tx": "0x876a47fe4f1d370324320a52800ed9082704755660b24073951bc38bb82131e1",
            "entry_price": 0.00031336303,
        },
        {
            "symbol": "HTTP",
            "token": "0xcb2df9b431fe7c19ee7f0d62d487b0121abdd075",
            "buy_tx": "0x337768c356bf3b83330bca17d3d3c0121566ed653fd71be67753f8dd017eed97",
            "entry_price": 1.2121983e-05,
        },
    ]


def vet_traders(
    items: list,
    *,
    buy_ts: float,
    entry_price: float | None,
    min_profit: float,
    min_buy: float,
    avg_mult: float,
    require_avg_cost: bool,
) -> tuple[list[dict], dict]:
    stats = {
        "raw": len(items),
        "no_addr": 0,
        "self": 0,
        "infra": 0,
        "bad_tag": 0,
        "late": 0,
        "dust": 0,
        "low_profit": 0,
        "avg_cost_high": 0,
        "kept": 0,
    }
    max_avg = (entry_price * avg_mult) if entry_price and entry_price > 0 else None
    kept: list[dict] = []
    for row in items:
        addr = wallet_addr(row)
        if not addr:
            stats["no_addr"] += 1
            continue
        if addr == OUR_WALLET:
            stats["self"] += 1
            continue
        if addr in INFRA:
            stats["infra"] += 1
            continue
        why = has_bad_tag(row)
        if why:
            stats["bad_tag"] += 1
            continue
        # Must be an actual buyer, not transfer-in / zero-buy
        if row.get("transfer_in") is True:
            stats["bad_tag"] += 1
            continue
        buy_tx_n = num(row.get("buy_tx_count_cur") or row.get("buy_tx_count")) or 0
        buy_vol = num(row.get("buy_volume_cur") or row.get("buy_volume") or row.get("history_bought_cost")) or 0.0
        if buy_tx_n <= 0 and buy_vol < min_buy:
            stats["dust"] += 1
            continue
        start_h = num(row.get("start_holding_at"))
        if start_h is None or start_h <= 0 or start_h > buy_ts:
            stats["late"] += 1
            continue
        pr = profit_of(row)
        if pr < min_profit:
            stats["low_profit"] += 1
            continue
        if dust_fail(row, min_buy, min_profit):
            stats["dust"] += 1
            continue
        avg_cost = num(row.get("avg_cost"))
        if max_avg is not None:
            if avg_cost is None or avg_cost <= 0:
                if require_avg_cost:
                    stats["avg_cost_high"] += 1
                    continue
                # allow missing avg_cost if early by time + size/profit floors
            elif avg_cost > max_avg:
                stats["avg_cost_high"] += 1
                continue
        buy_vol = num(row.get("buy_volume_cur") or row.get("buy_volume") or row.get("history_bought_cost"))
        kept.append(
            {
                "address": addr,
                "realized_profit": pr,
                "unrealized_profit": num(row.get("unrealized_profit") or row.get("unrealized_pnl")) or 0.0,
                "buy_volume_cur": buy_vol,
                "avg_cost": avg_cost,
                "start_holding_at": start_h,
                "sell_volume_cur": num(row.get("sell_volume_cur") or row.get("sell_volume")),
                "tags": tag_list(row),
                "early_time": True,
                "early_cost": bool(avg_cost is not None and max_avg is not None and avg_cost <= max_avg),
            }
        )
    kept.sort(key=lambda r: (-(r["realized_profit"] or 0), r["start_holding_at"] or 0))
    stats["kept"] = len(kept)
    return kept, stats


def merge_into_watchlist(
    existing: dict[str, dict],
    survivors: list[dict],
    *,
    symbol: str,
    token: str,
    now: str,
) -> tuple[int, int]:
    added = 0
    updated = 0
    tag_live = f"early_live:{symbol}"
    src = f"early_live:{symbol}"
    for r in survivors:
        addr = r["address"]
        short = f"{addr[:4]}...{addr[-4:]}"
        pr = float(r["realized_profit"] or 0)
        prev = existing.get(addr)
        if prev is None:
            row = {
                "address": addr,
                "sources": [f"gmgn:token_traders:{symbol}", src],
                "tags": list(
                    {
                        *(r.get("tags") or []),
                        "token_profit",
                        f"{symbol}_profit",
                        tag_live,
                        short,
                    }
                ),
                "labels": [short],
                "pnl_hints": {
                    "realized_pnl_usd": pr,
                    "source_token": token,
                    "source_symbol": symbol,
                },
                "onchain_stats": {},
                "n_tokens_seen": 1,
                "symbols_seen": [symbol],
                "address_label": short,
                "chain": "arc",
                "network": "mainnet",
                "source": "gmgn",
                "source_endpoints": ["gmgn", "gmgn:token_traders", src],
                "profit_tagged": True,
                "list_tier": "quality",
                "quality_reason": f"early_live:{symbol}",
                "pass_pnl": True,
                "realized_pnl_usd": pr,
                "exported_at": now,
                "source_token": token,
                "source_symbol": symbol,
                "chain_id": 5042,
                "early_live_tokens": [symbol],
                "last_early_harvest_at": now,
            }
            existing[addr] = row
            added += 1
        else:
            prev["pass_pnl"] = True
            prev["profit_tagged"] = True
            prev["realized_pnl_usd"] = max(float(prev.get("realized_pnl_usd") or 0), pr)
            srcs = list(prev.get("sources") or [])
            for s in (f"gmgn:token_traders:{symbol}", src):
                if s not in srcs:
                    srcs.append(s)
            prev["sources"] = srcs
            tags = list(prev.get("tags") or [])
            for t in ("token_profit", f"{symbol}_profit", tag_live):
                if t not in tags:
                    tags.append(t)
            prev["tags"] = tags
            el = list(prev.get("early_live_tokens") or [])
            if symbol not in el:
                el.append(symbol)
            prev["early_live_tokens"] = el
            # also keep early_2x_tokens field untouched
            syms = list(prev.get("symbols_seen") or [])
            if symbol not in syms:
                syms.append(symbol)
            prev["symbols_seen"] = syms
            prev["last_early_harvest_at"] = now
            prev["exported_at"] = now
            existing[addr] = prev
            updated += 1
    return added, updated


def harvest_one(job: dict, args) -> dict:
    sym = job["symbol"]
    token = job["token"].lower()
    buy_tx = job["buy_tx"]
    entry = num(job.get("entry_price"))
    print(f"\n=== {sym} {token} ===")
    block, buy_ts = buy_ts_from_tx(buy_tx)
    print(f"buy_tx={buy_tx} block={block} buy_ts={buy_ts} entry={entry} max_avg={(entry * args.avg_mult) if entry else None}")

    traders = gmgn_raw(
        [
            "token",
            "traders",
            "--chain",
            "arc",
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

    out_raw = ROOT / "arc-wallets" / "raw"
    out_raw.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    (out_raw / f"{sym}_traders.json").write_text(
        json.dumps(traders if traders is not None else {"error": "gmgn_fail"}, indent=2),
        encoding="utf-8",
    )

    if not items:
        print("no traders from GMGN; skip merge for this token", file=sys.stderr)
        (out_raw / f"{sym}_early_live_wallets.json").write_text(
            json.dumps(
                {
                    "token": token,
                    "symbol": sym,
                    "buy_ts": buy_ts,
                    "block": block,
                    "fetched_at": now,
                    "survivors": [],
                    "stats": {"raw": 0, "gmgn_fail": True},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "symbol": sym,
            "token": token,
            "buy_ts": buy_ts,
            "candidates": 0,
            "after_vet": 0,
            "newly_added": 0,
            "updated": 0,
            "samples": [],
            "stats": {"raw": 0, "gmgn_fail": True},
        }

    survivors, stats = vet_traders(
        items,
        buy_ts=buy_ts,
        entry_price=entry,
        min_profit=args.min_profit,
        min_buy=args.min_buy,
        avg_mult=args.avg_mult,
        require_avg_cost=args.require_avg_cost,
    )
    print(f"vet stats: {stats}")
    for r in survivors[:10]:
        print(
            f"  {r['address']} profit=${r['realized_profit']:.2f} "
            f"avg_cost={r['avg_cost']} start={r['start_holding_at']} buy_vol={r['buy_volume_cur']}"
        )

    for r in survivors:
        r["symbol"] = sym
        r["token"] = token
        r["our_buy_ts"] = buy_ts
        r["our_entry_price"] = entry

    (out_raw / f"{sym}_early_live_wallets.json").write_text(
        json.dumps(
            {
                "token": token,
                "symbol": sym,
                "buy_tx": buy_tx,
                "buy_ts": buy_ts,
                "block": block,
                "entry_price": entry,
                "max_avg_cost": (entry * args.avg_mult) if entry else None,
                "fetched_at": now,
                "stats": stats,
                "survivors": survivors,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    wl_path = ROOT / "arc-wallets" / "wallets.jsonl"
    existing = load_jsonl(wl_path)
    before = len(existing)
    added, updated = merge_into_watchlist(
        existing, survivors, symbol=sym, token=token, now=now
    )
    write_jsonl(wl_path, existing)
    qpath = ROOT / "arc-wallets" / "wallets_quality.jsonl"
    write_jsonl(qpath, existing)
    print(
        f"watchlist before={before} after={len(existing)} newly_added={added} "
        f"updated_existing={updated} after_vet={len(survivors)}"
    )
    return {
        "symbol": sym,
        "token": token,
        "buy_ts": buy_ts,
        "block": block,
        "candidates": stats["raw"],
        "after_vet": len(survivors),
        "newly_added": added,
        "updated": updated,
        "samples": [r["address"] for r in survivors[:5]],
        "stats": stats,
        "watchlist_size": len(existing),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest early live buyers into Arc watchlist")
    ap.add_argument(
        "--live-state",
        default=os.environ.get("LIVE_STATE_PATH", ""),
        help="live_state_arc.json (uses live_positions). Empty = built-in 5 live tokens.",
    )
    ap.add_argument("--token", default="")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--buy-tx", default="")
    ap.add_argument("--entry-price", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--min-profit", type=float, default=1.0)
    ap.add_argument("--min-buy", type=float, default=50.0, help="min buy USD floor (OR with min_profit)")
    ap.add_argument("--avg-mult", type=float, default=1.5, help="max avg_cost = entry * avg_mult")
    ap.add_argument(
        "--require-avg-cost",
        action="store_true",
        help="drop rows missing avg_cost when entry_price known",
    )
    args = ap.parse_args()

    if args.token and args.buy_tx:
        jobs = [
            {
                "symbol": args.symbol or "token",
                "token": args.token,
                "buy_tx": args.buy_tx,
                "entry_price": args.entry_price or None,
            }
        ]
    elif args.live_state:
        p = Path(args.live_state)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            # try sibling live-arc
            alt = Path("/workspace/meme-foundation/live-arc/live_state_arc.json")
            p = alt if alt.exists() else p
        jobs = load_jobs_from_live_state(p)
        print(f"loaded {len(jobs)} jobs from {p}")
    else:
        # prefer live-arc state if present
        alt = Path("/workspace/meme-foundation/live-arc/live_state_arc.json")
        if alt.exists():
            jobs = load_jobs_from_live_state(alt)
            print(f"loaded {len(jobs)} jobs from {alt}")
        else:
            jobs = default_jobs()
            print(f"using default {len(jobs)} live token jobs")

    reports = []
    import time
    for i, job in enumerate(jobs):
        if i > 0:
            time.sleep(float(os.environ.get("HARVEST_SLEEP_SEC", "8")))
        try:
            reports.append(harvest_one(job, args))
        except Exception as e:
            print(f"ERROR {job.get('symbol')}: {e}", file=sys.stderr)
            reports.append(
                {
                    "symbol": job.get("symbol"),
                    "token": job.get("token"),
                    "error": str(e),
                    "candidates": 0,
                    "after_vet": 0,
                    "newly_added": 0,
                }
            )

    summary = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "our_wallet": OUR_WALLET,
        "reports": reports,
        "total_newly_added": sum(r.get("newly_added") or 0 for r in reports),
        "total_after_vet": sum(r.get("after_vet") or 0 for r in reports),
        "final_watchlist_size": reports[-1].get("watchlist_size") if reports else None,
    }
    out = ROOT / "arc-wallets" / "raw" / "live_early_harvest_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n==== SUMMARY ====")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

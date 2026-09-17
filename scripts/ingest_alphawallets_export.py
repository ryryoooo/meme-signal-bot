#!/usr/bin/env python3
"""Ingest AlphaWallets export (JSON/JSONL/CSV) into fomo + RH watch.

When public API is known, harvest_alphawallets.py will call it.
Until then: drop export under fomo-wallets/alphawallets_raw/ or pass --file.
"""
from __future__ import annotations
import argparse, csv, json, os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOMO = ROOT / "fomo-wallets"
WATCH = Path(os.environ.get("WATCHLIST_PATH") or (ROOT / "rh-wallets" / "wallets.jsonl"))
RAW = FOMO / "alphawallets_raw"
MIN_PNL = float(os.environ.get("ALPHAW_MIN_PNL") or "500")
MERGE = os.environ.get("ALPHAW_MERGE_WATCH", "1") != "0"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def addr_key(a):
    return (a or "").strip().lower()

def load_jsonl(path: Path):
    if not path.exists():
        return []
    out=[]
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line=line.strip()
        if not line: continue
        try: out.append(json.loads(line))
        except Exception: continue
    return out

def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False)+"\n")

def parse_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows=[]
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        for line in text.splitlines():
            line=line.strip()
            if not line: continue
            try: rows.append(json.loads(line))
            except Exception: continue
        return rows
    if path.suffix.lower() == ".json":
        data=json.loads(text)
        if isinstance(data, list): return data
        for k in ("wallets","traders","results","data","rows"):
            if isinstance(data.get(k), list): return data[k]
        return [data]
    if path.suffix.lower() == ".csv":
        rdr=csv.DictReader(text.splitlines())
        return list(rdr)
    # try jsonl then json
    try:
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    except Exception:
        data=json.loads(text)
        return data if isinstance(data, list) else [data]

def normalize(o: dict, token_ca: str | None = None) -> dict | None:
    addr = addr_key(o.get("address") or o.get("wallet") or o.get("trader") or o.get("owner"))
    if not (addr.startswith("0x") and len(addr)==42) and not (len(addr)>=32 and not addr.startswith("0x")):
        # sol-only ok for archive but RH watch wants evm
        if not addr: return None
    pnl = o.get("realizedPnl") or o.get("realized_pnl") or o.get("pnl") or o.get("pnlUsd") or o.get("profit")
    try: pnl = float(pnl) if pnl is not None else None
    except Exception: pnl=None
    return {
        "address": addr,
        "pnlUsd": pnl,
        "buyUsd": o.get("buyUsd") or o.get("bought") or o.get("volumeBuy"),
        "sellUsd": o.get("sellUsd") or o.get("sold"),
        "avgMultiple": o.get("avgMultiple") or o.get("multiple") or o.get("x"),
        "token": token_ca or o.get("token") or o.get("ca"),
        "chain": o.get("chain") or o.get("network") or "robinhood",
        "source": "alphawallets",
        "tags": ["alphawallets", "realized_pnl", "themaran_adjacent"],
        "harvested_at": now_iso(),
        "raw": {k:o[k] for k in list(o)[:20]},
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--file", help="export file")
    ap.add_argument("--token", help="token CA context")
    args=ap.parse_args()
    files=[]
    if args.file:
        files=[Path(args.file)]
    else:
        RAW.mkdir(parents=True, exist_ok=True)
        files=sorted(RAW.glob("*"))
    if not files:
        print(json.dumps({"ok": False, "error": "no export files in fomo-wallets/alphawallets_raw/"}))
        return 1
    parsed=[]
    for f in files:
        if f.is_dir(): continue
        for o in parse_file(f):
            n=normalize(o, args.token)
            if n: parsed.append(n)
    write_jsonl(FOMO / "alphawallets_wallets.jsonl", parsed)
    # merge fomo evm
    evm_existing=load_jsonl(FOMO/"wallets_evm.jsonl")
    by={addr_key(x.get("address")):x for x in evm_existing if addr_key(x.get("address")).startswith("0x")}
    for n in parsed:
        a=addr_key(n.get("address"))
        if not a.startswith("0x"): continue
        cur=by.get(a) or {"address":a,"source":"alphawallets","tags":[]}
        tags=list(cur.get("tags") or [])
        for t in n.get("tags") or []:
            if t not in tags: tags.append(t)
        cur["tags"]=tags
        if n.get("pnlUsd") is not None:
            try: cur["pnlUsd"]=max(float(cur.get("pnlUsd") or 0), float(n["pnlUsd"]))
            except Exception: cur["pnlUsd"]=n.get("pnlUsd")
        cur["source"]=(str(cur.get("source") or "")+",alphawallets").strip(",")
        cur["harvested_at"]=now_iso()
        by[a]=cur
    write_jsonl(FOMO/"wallets_evm.jsonl", list(by.values()))
    merged=updated=0
    if MERGE and WATCH.exists():
        watch=load_jsonl(WATCH)
        wby={addr_key(o.get("address") or o.get("wallet")):dict(o) for o in watch if addr_key(o.get("address") or o.get("wallet")).startswith("0x")}
        for n in parsed:
            a=addr_key(n.get("address"))
            if not a.startswith("0x"): continue
            if n.get("pnlUsd") is not None and float(n.get("pnlUsd") or 0) < MIN_PNL:
                continue
            cur=wby.get(a)
            if not cur:
                wby[a]={"address":a,"tags":["alphawallets","realized_pnl"],"sources":["alphawallets"],"realized_pnl_usd":n.get("pnlUsd"),"notes":"alphawallets export","added_at":now_iso()}
                merged+=1
            else:
                tags=list(cur.get("tags") or [])
                if "alphawallets" not in tags: tags.append("alphawallets")
                cur["tags"]=tags
                srcs=list(cur.get("sources") or [])
                if "alphawallets" not in srcs: srcs.append("alphawallets")
                cur["sources"]=srcs
                if n.get("pnlUsd") is not None:
                    try: cur["realized_pnl_usd"]=max(float(cur.get("realized_pnl_usd") or 0), float(n["pnlUsd"]))
                    except Exception: pass
                updated+=1
                wby[a]=cur
        write_jsonl(WATCH, list(wby.values()))
    print(json.dumps({"ok":True,"ingested":len(parsed),"fomo_evm":len(by),"watch_merged":merged,"watch_updated":updated}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())

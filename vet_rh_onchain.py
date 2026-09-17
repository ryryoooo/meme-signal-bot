#!/usr/bin/env python3
"""Vet top RH on-chain actives via GMGN (low call-cap) and optionally merge winners.

Credit-saving defaults:
  RH_VET_MAX_CALLS=8
  RH_ONCHAIN_VET_FLOOR=500
  RH_VET_MIN_WINRATE=0.40
  RH_VET_MIN_TRADES=8
  RH_VET_MERGE=0  (set 1 to write winners into rh-wallets/wallets.jsonl)

Prefer DexScreener elsewhere; this script only scores wallets already collected on-chain.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ONCHAIN = ROOT / "rh-wallets" / "wallets_onchain.jsonl"
WATCH = ROOT / "rh-wallets" / "wallets.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def gmgn_wallet_stat(addr: str) -> dict | None:
    cli = shutil.which("gmgn-cli")
    if not cli:
        print("gmgn-cli missing", file=sys.stderr)
        return None
    try:
        proc = subprocess.run(
            [cli, "wallet", "stat", "--chain", "robinhood", "--address", addr, "--raw"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )
    except Exception as e:
        print(f"gmgn fail {type(e).__name__}", file=sys.stderr)
        return None
    blob = (proc.stdout or "") + (proc.stderr or "")
    if "RATE_LIMIT" in blob.upper() or "429" in blob:
        print("rate-limit; stop", file=sys.stderr)
        return {"_rate": True}
    out = (proc.stdout or "").strip()
    i = min([x for x in (out.find("{"), out.find("[")) if x >= 0], default=-1)
    if i < 0:
        return None
    try:
        data = json.loads(out[i:])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else None


def main() -> int:
    max_calls = int(os.environ.get("RH_VET_MAX_CALLS", "8"))
    floor = float(os.environ.get("RH_ONCHAIN_VET_FLOOR", os.environ.get("WATCH_MIN_REALIZED_HARD", "500")))
    min_wr = float(os.environ.get("RH_VET_MIN_WINRATE", "0.40"))
    min_tr = int(os.environ.get("RH_VET_MIN_TRADES", "8"))
    do_merge = (os.environ.get("RH_VET_MERGE") or "0").strip().lower() in ("1", "true", "yes")

    rows = load_jsonl(ONCHAIN)
    rows = sorted(rows, key=lambda r: (int(r.get("n_token_xf") or 0), int(r.get("n_tx") or 0)), reverse=True)
    # Skip already-vetted winners
    candidates = [r for r in rows if not (r.get("pass_pnl") is True and float(r.get("realized_pnl_usd") or 0) >= floor)]
    candidates = candidates[: max(max_calls * 3, max_calls)]

    print(f"vet_rh_onchain candidates={len(candidates)} max_calls={max_calls} floor={floor}")
    winners: list[dict] = []
    calls = 0
    for r in candidates:
        if calls >= max_calls:
            break
        addr = (r.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        stat = gmgn_wallet_stat(addr)
        calls += 1
        if stat and stat.get("_rate"):
            break
        time.sleep(1.0)
        if not stat:
            r["vet_status"] = "gmgn_na"
            continue
        pnl = None
        for k in ("realized_profit", "realized_pnl", "total_profit", "pnl"):
            if stat.get(k) is not None:
                try:
                    pnl = float(stat[k])
                    break
                except (TypeError, ValueError):
                    pass
        wr = None
        for k in ("winrate", "win_rate"):
            if stat.get(k) is not None:
                try:
                    wr = float(stat[k])
                    if wr > 1:
                        wr = wr / 100.0
                    break
                except (TypeError, ValueError):
                    pass
        ntr = None
        for k in ("buy", "txs", "tx_count", "total_trades", "trade_count"):
            if stat.get(k) is not None:
                try:
                    ntr = int(float(stat[k]))
                    break
                except (TypeError, ValueError):
                    pass
        r["gmgn_pnl_usd"] = pnl
        r["gmgn_winrate"] = wr
        r["n_trades"] = ntr
        r["vetted_at"] = datetime.now(timezone.utc).isoformat()
        ok = pnl is not None and pnl >= floor and (wr is None or wr >= min_wr) and (ntr is None or ntr >= min_tr)
        r["pass_pnl"] = bool(ok)
        r["realized_pnl_usd"] = pnl if pnl is not None else r.get("realized_pnl_usd")
        r["vet_status"] = "pass" if ok else "fail"
        if ok:
            winners.append(dict(r))
            print(f"PASS {addr[:10]}… pnl={pnl} wr={wr} n={ntr}")
        else:
            print(f"fail {addr[:10]}… pnl={pnl} wr={wr} n={ntr}")

    # write back onchain with vet fields
    by = {(x.get("address") or "").lower(): x for x in rows}
    for r in candidates:
        a = (r.get("address") or "").lower()
        if a in by:
            by[a] = r
    write_jsonl(ONCHAIN, list(by.values()))

    merged_n = 0
    if do_merge and winners:
        watch = {(w.get("address") or "").lower(): w for w in load_jsonl(WATCH)}
        now = datetime.now(timezone.utc).isoformat()
        for w in winners:
            a = (w.get("address") or "").lower()
            eps = list(w.get("source_endpoints") or [])
            if "onchain_vetted" not in eps:
                eps.append("onchain_vetted")
            row = dict(watch.get(a) or {})
            row.update(
                {
                    "address": a,
                    "address_label": w.get("address_label") or row.get("address_label") or (a[:4] + "..." + a[-4:]),
                    "realized_pnl_usd": w.get("realized_pnl_usd"),
                    "pass_pnl": True,
                    "gmgn_pnl_usd": w.get("gmgn_pnl_usd"),
                    "gmgn_winrate": w.get("gmgn_winrate"),
                    "n_trades": w.get("n_trades"),
                    "source_endpoints": list(dict.fromkeys(list(row.get("source_endpoints") or []) + eps)),
                    "tags": list(dict.fromkeys(list(row.get("tags") or []) + ["onchain_vetted"])),
                    "collected_at": row.get("collected_at") or now,
                    "vetted_at": now,
                    "chain": "robinhood",
                }
            )
            watch[a] = row
            merged_n += 1
        write_jsonl(WATCH, list(watch.values()))

    print(f"vet done calls={calls} winners={len(winners)} merged={merged_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Merge RH signal state.json files for box↔GHA dedupe.

Keeps notify dedupe keys in sync:
  - ca_last_posted: max timestamp per CA
  - seen_signal_keys: union (capped)
  - open_alerts: by CA, keep newer posted_at

Does NOT overwrite box FOMO poll timers from GHA (avoids starving the fast tick).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def merge(a: dict, b: dict, *, keep_fomo_from: str = "a") -> dict:
    out = dict(a) if a else {}
    if not b:
        return out

    # ca_last_posted: max ts
    ca: dict = dict(out.get("ca_last_posted") or {})
    for k, v in (b.get("ca_last_posted") or {}).items():
        try:
            bv = float(v)
        except (TypeError, ValueError):
            continue
        try:
            av = float(ca.get(k)) if k in ca else None
        except (TypeError, ValueError):
            av = None
        if av is None or bv > av:
            ca[k] = bv
    # prune old
    items = sorted(ca.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:400]
    out["ca_last_posted"] = dict(items)

    # seen_signal_keys: union, preserve order (b then a), cap 500
    seen = []
    for src in (b.get("seen_signal_keys") or [], out.get("seen_signal_keys") or []):
        for k in src:
            if k not in seen:
                seen.append(k)
    out["seen_signal_keys"] = seen[-500:]

    # open_alerts: newer posted_at wins per ca
    by_ca: dict[str, dict] = {}
    for src in (out.get("open_alerts") or [], b.get("open_alerts") or []):
        for al in src:
            if not isinstance(al, dict):
                continue
            ca_k = (al.get("ca") or "").lower()
            if not ca_k:
                continue
            prev = by_ca.get(ca_k)
            try:
                pt = float(al.get("posted_at") or 0)
            except (TypeError, ValueError):
                pt = 0.0
            if prev is None:
                by_ca[ca_k] = al
                continue
            try:
                ppt = float(prev.get("posted_at") or 0)
            except (TypeError, ValueError):
                ppt = 0.0
            if pt >= ppt:
                by_ca[ca_k] = al
    out["open_alerts"] = list(by_ca.values())[-200:]

    # FOMO / holders timers: prefer local (a) so GHA's 25m interval doesn't block box ~20s tick
    if keep_fomo_from == "a":
        for k in ("last_fomo_poll_ts", "last_fomo_err", "last_fomo_holders_ts"):
            if k in (a or {}):
                out[k] = a[k]
            elif k in out and k not in (a or {}):
                # drop GHA-only timer so box can poll immediately after first merge
                if k in (b or {}) and k not in (a or {}):
                    out.pop(k, None)
    else:
        for k in ("last_fomo_poll_ts", "last_fomo_holders_ts"):
            av = (a or {}).get(k)
            bv = (b or {}).get(k)
            try:
                af = float(av) if av is not None else 0.0
            except (TypeError, ValueError):
                af = 0.0
            try:
                bf = float(bv) if bv is not None else 0.0
            except (TypeError, ValueError):
                bf = 0.0
            out[k] = max(af, bf) if (af or bf) else (av or bv)

    # carry useful meta from either side
    for k in ("watch_size", "trade_source", "gmgn_err", "gmgn_cooldown_until", "gmgn_cooldown_err"):
        if k not in out and k in b:
            out[k] = b[k]
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="+", help="state.json files (first = local/primary)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--keep-fomo-from",
        choices=("a", "max"),
        default="a",
        help="a=preserve first file FOMO timers (box tick); max=take later poll ts",
    )
    args = p.parse_args()
    paths = [Path(x) for x in args.paths]
    base = load(paths[0]) if paths else {}
    for extra in paths[1:]:
        base = merge(base, load(extra), keep_fomo_from=args.keep_fomo_from)
    out = Path(args.output)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    ca_n = len((base.get("ca_last_posted") or {}))
    seen_n = len(base.get("seen_signal_keys") or [])
    print(f"merge_signal_state ok ca_last={ca_n} seen={seen_n} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

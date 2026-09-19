#!/usr/bin/env python3
"""Rank RH wallets with LOW profit over a GMGN portfolio period (default 7d).

Reads period-suffixed fields written by vet_gmgn_throttled (period=7d):
  win_rate_7d, realized_pnl_7d, n_trades_7d

Writes (worst-first by realized_pnl_{period} then win_rate_{period}):
  rh-wallets/low_profit_7d.md
  rh-wallets/low_profit_7d.jsonl

Tags selected rows with low_profit_7d (also patches watchlist tags when RANK_TAG_WATCH=1).

Env:
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  GMGN_PORTFOLIO_PERIOD=7d
  LOW_PNL_MIN_TRADES=5
  LOW_PNL_TOP_N=100          # max rows in report (0 = all qualifying)
  RANK_TAG_WATCH=1           # add low_profit_{period} tag onto wallets.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def period_key() -> str:
    p = (os.environ.get("GMGN_PORTFOLIO_PERIOD") or "7d").strip().lower()
    if p in ("7", "7d", "7day", "7days"):
        return "7d"
    if p in ("30", "30d", "30day", "30days"):
        return "30d"
    return p or "7d"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def field_names(period: str) -> tuple[str, str, str]:
    """Return (wr_key, pnl_key, n_key) for period."""
    if period == "7d":
        return "win_rate_7d", "realized_pnl_7d", "n_trades_7d"
    if period == "30d":
        # prefer explicit 30d if present, else legacy un-suffixed
        return "win_rate", "realized_pnl_usd", "n_trades"
    return f"win_rate_{period}", f"realized_pnl_{period}", f"n_trades_{period}"


def main() -> int:
    period = period_key()
    wr_k, pnl_k, n_k = field_names(period)
    tag = f"low_profit_{period}"
    min_n = max(0, int(os.environ.get("LOW_PNL_MIN_TRADES", "5")))
    top_n = int(os.environ.get("LOW_PNL_TOP_N", "100"))
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets" / "wallets.jsonl")))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path

    out_stem = ROOT / "rh-wallets" / f"low_profit_{period}"
    out_md = Path(str(out_stem) + ".md")
    out_jl = Path(str(out_stem) + ".jsonl")

    rows = load_jsonl(watch_path)
    covered = 0
    qual: list[dict] = []
    for o in rows:
        a = (o.get("address") or "").lower()
        if not a.startswith("0x"):
            continue
        wr = _f(o.get(wr_k))
        pnl = _f(o.get(pnl_k))
        n = _i(o.get(n_k), 0)
        if wr is not None or pnl is not None:
            covered += 1
        if n < min_n:
            continue
        if pnl is None and wr is None:
            continue
        # treat missing pnl as +inf so WR-only sort last among equals? better skip if no pnl
        if pnl is None:
            continue
        qual.append(
            {
                "address": a,
                "address_label": o.get("address_label") or o.get("fomo_handle") or a[:10],
                wr_k: wr,
                pnl_k: pnl,
                n_k: n,
                "win_rate": o.get("win_rate"),
                "realized_pnl_usd": o.get("realized_pnl_usd"),
                "n_trades": o.get("n_trades"),
                "tags": list(o.get("tags") or []),
                "scout_tier": o.get("scout_tier"),
                "scout_rank_score": o.get("scout_rank_score"),
            }
        )

    # worst-first: lowest realized pnl, then lowest win rate
    qual.sort(
        key=lambda r: (
            float(r.get(pnl_k) if r.get(pnl_k) is not None else 1e18),
            float(r.get(wr_k) if r.get(wr_k) is not None else 1e18),
            -int(r.get(n_k) or 0),
        )
    )
    if top_n > 0:
        selected = qual[:top_n]
    else:
        selected = list(qual)

    # stamp tag on selected output rows
    selected_addrs = set()
    for r in selected:
        tags = [str(t) for t in (r.get("tags") or [])]
        if tag not in tags and tag.lower() not in [t.lower() for t in tags]:
            tags.append(tag)
        r["tags"] = tags
        r["low_profit_period"] = period
        r["ranked_low_pnl_at"] = now_iso()
        selected_addrs.add(r["address"])

    write_jsonl(out_jl, selected)

    # optional: patch watchlist tags
    tagged = 0
    if env_bool("RANK_TAG_WATCH", True) and selected_addrs:
        by = {}
        for o in rows:
            a = (o.get("address") or "").lower()
            if a.startswith("0x"):
                by[a] = dict(o)
        for a in selected_addrs:
            row = by.get(a)
            if not row:
                continue
            tags = [str(t) for t in (row.get("tags") or [])]
            if tag.lower() not in [t.lower() for t in tags]:
                tags.append(tag)
                row["tags"] = tags
                by[a] = row
                tagged += 1
        # clear tag from wallets no longer in selected set (optional keep stale — we keep)
        write_jsonl(watch_path, list(by.values()))

    lines = [
        f"# Low profit wallets ({period})",
        "",
        f"- Updated: **{now_iso()}**",
        f"- Source: `{watch_path.relative_to(ROOT) if watch_path.is_relative_to(ROOT) else watch_path}`",
        f"- Period fields: `{wr_k}`, `{pnl_k}`, `{n_k}`",
        f"- Coverage (any {period} WR/PnL): **{covered}** / {len(rows)}",
        f"- Qualifying (n_trades>={min_n} + pnl present): **{len(qual)}**",
        f"- Reported (top {top_n if top_n > 0 else 'all'}, worst-first): **{len(selected)}**",
        f"- Tag: `{tag}` (watch patched: {tagged})",
        "",
        "## Worst-first list",
        "",
        f"| # | address | label | {pnl_k} | {wr_k} | {n_k} | 30d_rp | 30d_wr |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(selected, 1):
        pnl = r.get(pnl_k)
        wr = r.get(wr_k)
        n = r.get(n_k)
        rp30 = r.get("realized_pnl_usd")
        wr30 = r.get("win_rate")
        pnl_s = f"{pnl:,.2f}" if isinstance(pnl, (int, float)) else "—"
        wr_s = f"{wr:.3f}" if isinstance(wr, (int, float)) else "—"
        rp30_s = f"{rp30:,.2f}" if isinstance(rp30, (int, float)) else "—"
        wr30_s = f"{wr30:.3f}" if isinstance(wr30, (int, float)) else "—"
        label = str(r.get("address_label") or "")[:28].replace("|", "/")
        lines.append(
            f"| {i} | `{r['address'][:10]}…` | {label} | {pnl_s} | {wr_s} | {n} | {rp30_s} | {wr30_s} |"
        )

    if not selected:
        lines.append("")
        lines.append("_No qualifying wallets yet — run GHA `gmgn-vet-throttle` with `period=7d`._")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "period": period,
                "coverage": covered,
                "total": len(rows),
                "qualifying": len(qual),
                "reported": len(selected),
                "tagged": tagged,
                "out_md": str(out_md.relative_to(ROOT)),
                "out_jsonl": str(out_jl.relative_to(ROOT)),
                "top": [
                    {
                        "address": r["address"],
                        pnl_k: r.get(pnl_k),
                        wr_k: r.get(wr_k),
                        n_k: r.get(n_k),
                    }
                    for r in selected[:15]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

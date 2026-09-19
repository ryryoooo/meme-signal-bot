#!/usr/bin/env python3
"""Rank scout_tg-resolved wallets with GMGN portfolio + FOMO overlap + TG frequency.

Env:
  SCOUT_TG_VET_CAP=40       max portfolio stats calls
  SCOUT_RANK_MERGE=1        merge into main watchlist
  SCOUT_MERGE_ALL_RESOLVED=1  merge EVERY uniquely-resolved scout addr (default on)
  GMGN_DISABLED=0
  CHAIN=robinhood
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  NANSEN_API_KEY            optional, cheap skip if unset

Writes:
  rh-wallets/wallets_scout_ranked.jsonl
  rh-wallets/summary_scout.md
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util

def _load_mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_kol = _load_mod("harvest_kol_vetted", ROOT / "scripts" / "harvest_kol_vetted.py")
batch_vet = _kol.batch_vet
kol_write_jsonl = _kol.write_jsonl

import bot as _bot  # noqa: E402
wallet_quality_score = _bot.wallet_quality_score
wallet_is_consistent = _bot.wallet_is_consistent
wallet_passes_filter = _bot.wallet_passes_filter

RAW_DIR = ROOT / "rh-wallets" / "raw"
RANKED_PATH = ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl"
SUMMARY_PATH = ROOT / "rh-wallets" / "summary_scout.md"
TRUNC_PATH = RAW_DIR / "scout_tg_trunc.jsonl"
RESOLVE_CACHE = RAW_DIR / "scout_tg_resolve_cache.jsonl"
FOMO_EVM = ROOT / "fomo-wallets" / "wallets_evm.jsonl"
FOMO_LB = ROOT / "fomo-wallets" / "leaderboard.jsonl"


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def load_map(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for o in load_jsonl(path):
        a = (o.get("address") or "").lower()
        if a.startswith("0x"):
            out[a] = o
    return out


def fomo_index() -> dict[str, dict]:
    by: dict[str, dict] = {}
    for path in (FOMO_EVM, FOMO_LB):
        for o in load_jsonl(path):
            a = (o.get("address") or o.get("evm") or "").lower()
            if not a.startswith("0x"):
                continue
            prev = by.get(a) or {}
            pnl = o.get("pnlUsd") or o.get("pnl_usd") or o.get("realized_pnl_usd")
            handle = o.get("handle") or o.get("fomo_handle") or o.get("displayName")
            by[a] = {
                **prev,
                "address": a,
                "fomo_handle": handle or prev.get("fomo_handle"),
                "fomo_pnl_usd": pnl if pnl is not None else prev.get("fomo_pnl_usd"),
            }
    return by


def rebuild_freq_from_trunc_and_cache() -> dict[str, dict]:
    """Aggregate frequency stats keyed by full address (from resolve cache) or trunc_key."""
    cache_rows = load_jsonl(RESOLVE_CACHE)
    # trunc_key -> address
    key_to_addr: dict[str, str] = {}
    for o in cache_rows:
        k = o.get("trunc_key") or o.get("cache_key")
        a = (o.get("address") or "").lower()
        if k and a.startswith("0x"):
            key_to_addr[str(k)] = a

    by_addr: dict[str, dict] = {}
    for o in cache_rows:
        a = (o.get("address") or "").lower()
        if not a.startswith("0x"):
            continue
        agg = by_addr.setdefault(
            a,
            {
                "address": a,
                "scout_hit_count": 0,
                "scout_elite_count": 0,
                "scout_good_count": 0,
                "scout_sum_buy_usd": 0.0,
                "scout_msg_ids": [],
                "scout_token_cas": [],
                "scout_tickers": [],
                "scout_tier": "good",
                "address_label": o.get("address_label") or "",
            },
        )
        agg["scout_hit_count"] += 1
        if o.get("tier") == "elite":
            agg["scout_elite_count"] += 1
            agg["scout_tier"] = "elite"
        else:
            agg["scout_good_count"] += 1
        try:
            agg["scout_sum_buy_usd"] += float(o.get("usd") or 0)
        except (TypeError, ValueError):
            pass
        for fld, src in (
            ("scout_msg_ids", "msg_id"),
            ("scout_token_cas", "token_ca"),
            ("scout_tickers", "ticker"),
        ):
            v = o.get(src)
            if v and v not in agg[fld]:
                agg[fld].append(v)
        if o.get("address_label") and not agg.get("address_label"):
            agg["address_label"] = o["address_label"]

    # Also count trunc rows that matched via key_to_addr (in case cache incomplete)
    for t in load_jsonl(TRUNC_PATH):
        k = t.get("trunc_key")
        a = key_to_addr.get(str(k or ""))
        if not a:
            continue
        if a not in by_addr:
            continue  # already counted from cache typically
    return by_addr


def scout_rank_score(row: dict) -> float:
    """Composite for summary ordering: frequency + elite + PnL + quality."""
    score = 0.0
    score += min(3.0, float(row.get("scout_hit_count") or 0) * 0.5)
    score += float(row.get("scout_elite_count") or 0) * 0.8
    score += min(2.0, float(row.get("scout_sum_buy_usd") or 0) / 500.0)
    try:
        rp = float(row.get("realized_pnl_usd") or 0)
    except (TypeError, ValueError):
        rp = 0.0
    if rp >= 10_000:
        score += 2.0
    elif rp >= 1_000:
        score += 1.2
    elif rp >= 100:
        score += 0.5
    try:
        wr = float(row.get("win_rate") or 0)
        if wr > 1.5:
            wr /= 100.0
    except (TypeError, ValueError):
        wr = 0.0
    nt = int(row.get("n_trades") or 0)
    if nt >= 15 and wr >= 0.45:
        score += 1.5
    elif nt >= 10 and wr >= 0.4:
        score += 0.8
    if row.get("fomo_handle"):
        score += 0.6
    score += float(row.get("quality_score") or 0) * 0.4
    if row.get("scout_tier") == "elite":
        score += 0.5
    return round(score, 3)


def should_merge_to_watch(row: dict) -> bool:
    """Merge uniquely-resolved scout wallets into main watch (notify-ready).

    Default SCOUT_MERGE_ALL_RESOLVED=1: every valid 0x from resolve/rank lands on
    watch with scout_tg / scout_tg_early tags (ALLOW_FOMO_WITHOUT_WR / scout seed
    path in bot.py). Set SCOUT_MERGE_ALL_RESOLVED=0 to restore elite/quality gate.
    """
    addr = (row.get("address") or "").lower()
    if env_bool("SCOUT_MERGE_ALL_RESOLVED", True):
        return addr.startswith("0x") and len(addr) == 42
    try:
        if wallet_passes_filter(row, min_realized=float(os.environ.get("WATCH_MIN_REALIZED_HARD", "500"))):
            return True
    except Exception:
        pass
    elite = (row.get("scout_tier") == "elite") or ("scout_elite" in (row.get("tags") or []))
    hits = int(row.get("scout_hit_count") or 0)
    elite_n = int(row.get("scout_elite_count") or 0)
    wr = row.get("win_rate")
    nt = int(row.get("n_trades") or 0)
    try:
        rp = float(row.get("realized_pnl_usd") or 0)
    except (TypeError, ValueError):
        rp = 0.0
    if elite and hits >= 2:
        return True
    if elite and elite_n >= 2:
        return True
    if elite and wr is not None and nt >= 10:
        try:
            wrf = float(wr)
            if wrf > 1.5:
                wrf /= 100.0
            if wrf >= 0.4 and (rp >= 0 or rp == 0):
                return True
        except (TypeError, ValueError):
            pass
    if hits >= 3 and rp > 0:
        return True
    try:
        if wallet_is_consistent(row) and rp > 0:
            return True
    except Exception:
        pass
    return False


def merge_rank_into_watch(ranked: dict[str, dict], watch: dict[str, dict]) -> int:
    merged = 0
    now = now_iso()
    for addr, r in ranked.items():
        if not should_merge_to_watch(r):
            continue
        if addr not in watch:
            row = dict(r)
            tags = list(row.get("tags") or [])
            for t in ("scout_tg", "scout_tg_early"):
                if t not in tags:
                    tags.append(t)
            if row.get("scout_tier") == "elite" and "scout_elite" not in tags:
                tags.append("scout_elite")
            elif "scout_good" not in tags and "scout_elite" not in tags:
                tags.append("scout_good")
            has_wr = row.get("win_rate") is not None and int(float(row.get("n_trades") or 0)) >= 10
            if not has_wr and "scout_pending_wr" not in [t.lower() for t in tags]:
                tags.append("scout_pending_WR")
            if has_wr:
                tags = [t for t in tags if t.lower() != "scout_pending_wr"]
            row["tags"] = tags
            eps = list(row.get("source_endpoints") or [])
            if "telegram:scoutrobinhood" not in eps:
                eps.append("telegram:scoutrobinhood")
            row["source_endpoints"] = eps
            row["pass_pnl"] = True
            row["list_tier"] = "quality" if wallet_is_consistent(r) else "scout"
            row["quality_reason"] = "scout_tg_ranked_merge"
            row["collected_at"] = row.get("collected_at") or now
            row["ranked_at"] = now
            watch[addr] = row
            merged += 1
            continue
        o = watch[addr]
        changed = False
        for k in (
            "win_rate",
            "n_trades",
            "realized_pnl_usd",
            "unrealized_pnl_usd",
            "total_pnl_usd",
            "scout_hit_count",
            "scout_elite_count",
            "scout_good_count",
            "scout_sum_buy_usd",
            "scout_tier",
            "quality_score",
            "scout_rank_score",
            "fomo_handle",
            "fomo_pnl_usd",
        ):
            if r.get(k) is None:
                continue
            if k in ("realized_pnl_usd", "unrealized_pnl_usd", "total_pnl_usd", "fomo_pnl_usd"):
                try:
                    nv = float(r[k])
                    ov = float(o[k]) if o.get(k) is not None else None
                    if ov is None or abs(nv) > abs(ov):
                        o[k] = nv
                        changed = True
                except (TypeError, ValueError):
                    o[k] = r[k]
                    changed = True
            elif k.startswith("scout_") and k.endswith("_count"):
                try:
                    if int(r[k] or 0) > int(o.get(k) or 0):
                        o[k] = int(r[k])
                        changed = True
                except (TypeError, ValueError):
                    pass
            elif k == "scout_sum_buy_usd":
                try:
                    if float(r[k] or 0) > float(o.get(k) or 0):
                        o[k] = float(r[k])
                        changed = True
                except (TypeError, ValueError):
                    pass
            elif o.get(k) is None:
                o[k] = r[k]
                changed = True
            elif k in ("win_rate", "n_trades", "quality_score", "scout_rank_score"):
                o[k] = r[k]
                changed = True
        for t in ("scout_tg", "scout_tg_early", "scout_elite", "scout_good"):
            tags = list(o.get("tags") or [])
            if t in (r.get("tags") or []) and t not in tags:
                tags.append(t)
                o["tags"] = tags
                changed = True
        eps = list(o.get("source_endpoints") or [])
        if "telegram:scoutrobinhood" not in eps:
            eps.append("telegram:scoutrobinhood")
            o["source_endpoints"] = eps
            changed = True
        if "gmgn:portfolio" not in eps and r.get("win_rate") is not None:
            eps.append("gmgn:portfolio")
            o["source_endpoints"] = eps
            changed = True
        if should_merge_to_watch({**o, **{k: r.get(k) for k in r}}):
            o["pass_pnl"] = True
            tags = list(o.get("tags") or [])
            for t in ("scout_tg", "scout_tg_early"):
                if t not in tags:
                    tags.append(t)
                    changed = True
            if (r.get("scout_tier") == "elite" or o.get("scout_tier") == "elite") and "scout_elite" not in tags:
                tags.append("scout_elite")
                changed = True
            elif "scout_good" not in tags and "scout_elite" not in tags:
                tags.append("scout_good")
                changed = True
            o["tags"] = tags
            if o.get("list_tier") in (None, "scout"):
                o["list_tier"] = "quality" if wallet_is_consistent(o) else o.get("list_tier") or "scout"
            o["quality_reason"] = o.get("quality_reason") or "scout_tg_ranked_merge"
            changed = True
        if changed:
            o["ranked_at"] = now
            merged += 1
        watch[addr] = o
    return merged


def write_summary(ranked: list[dict], stats: dict) -> None:
    lines = [
        "# Scout TG wallet ranking",
        "",
        f"- Updated (UTC): {now_iso()}",
        f"- Ranked wallets: **{len(ranked)}**",
        f"- Portfolio vetted this run: **{stats.get('vetted', 0)}** (cap={stats.get('cap')})",
        f"- GMGN calls: **{stats.get('calls', 0)}** err={stats.get('err')}",
        f"- FOMO overlaps: **{stats.get('fomo_hits', 0)}**",
        f"- Merged to main watch: **{stats.get('merged', 0)}**",
        "",
        "## Top by scout_rank_score",
        "",
        "| # | addr | tier | hits | elite | buy$ | WR | n | realized | FOMO | score |",
        "|---|------|------|------|-------|------|----|---|----------|------|-------|",
    ]
    for i, r in enumerate(ranked[:40], 1):
        a = r.get("address") or ""
        short = f"{a[:6]}…{a[-4:]}" if len(a) >= 10 else a
        wr = r.get("win_rate")
        wr_s = f"{float(wr):.0%}" if isinstance(wr, (int, float)) else "—"
        rp = r.get("realized_pnl_usd")
        rp_s = f"{float(rp):,.0f}" if isinstance(rp, (int, float)) else "—"
        fomo = r.get("fomo_handle") or "—"
        lines.append(
            f"| {i} | `{short}` | {r.get('scout_tier') or '—'} | "
            f"{r.get('scout_hit_count') or 0} | {r.get('scout_elite_count') or 0} | "
            f"{float(r.get('scout_sum_buy_usd') or 0):,.0f} | {wr_s} | {r.get('n_trades') or '—'} | "
            f"{rp_s} | {fomo} | {r.get('scout_rank_score')} |"
        )
    lines += [
        "",
        "## Top elite by frequency",
        "",
    ]
    elites = [r for r in ranked if (r.get("scout_tier") == "elite" or int(r.get("scout_elite_count") or 0) > 0)]
    elites.sort(key=lambda r: (-int(r.get("scout_elite_count") or 0), -int(r.get("scout_hit_count") or 0)))
    for r in elites[:15]:
        a = r.get("address") or ""
        lines.append(
            f"- `{a}` elite={r.get('scout_elite_count')} hits={r.get('scout_hit_count')} "
            f"buy=${float(r.get('scout_sum_buy_usd') or 0):,.0f} "
            f"rp={r.get('realized_pnl_usd')} wr={r.get('win_rate')}"
        )
    lines += ["", "## Top by realized PnL (filled)", ""]
    by_pnl = [r for r in ranked if r.get("realized_pnl_usd") is not None]
    by_pnl.sort(key=lambda r: -float(r.get("realized_pnl_usd") or 0))
    for r in by_pnl[:15]:
        a = r.get("address") or ""
        lines.append(
            f"- `{a}` rp=${float(r.get('realized_pnl_usd') or 0):,.0f} "
            f"wr={r.get('win_rate')} n={r.get('n_trades')} "
            f"elite={r.get('scout_elite_count')} hits={r.get('scout_hit_count')}"
        )
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets" / "wallets.jsonl")))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path
    cap = int(os.environ.get("SCOUT_TG_VET_CAP", "40"))
    chain = os.environ.get("CHAIN", "robinhood")
    do_merge = env_bool("SCOUT_RANK_MERGE", True)

    freq = rebuild_freq_from_trunc_and_cache()
    watch = load_map(watch_path)
    # seed candidates: resolve-cache addrs + watch rows tagged scout_*
    candidates: dict[str, dict] = {}
    for a, f in freq.items():
        base = dict(watch.get(a) or {})
        base.update({k: v for k, v in f.items() if v is not None})
        base["address"] = a
        tags = list(base.get("tags") or [])
        for t in ("scout_tg", "scout_tg_early"):
            if t not in tags:
                tags.append(t)
        if base.get("scout_tier") == "elite" and "scout_elite" not in tags:
            tags.append("scout_elite")
        elif "scout_good" not in tags and "scout_elite" not in tags:
            tags.append("scout_good")
        base["tags"] = tags
        candidates[a] = base
    for a, o in watch.items():
        parts = [
            str(x).lower()
            for x in list(o.get("tags") or [])
            + list(o.get("gmgn_tags") or [])
            + list(o.get("sources") or [])
            + list(o.get("source_endpoints") or [])
        ]
        scout_tg = any(
            x in ("scout_tg", "scout_elite", "scout_good", "scout_tg_early", "telegram:scoutrobinhood")
            or x.startswith("scout_tg")
            for x in parts
        )
        if not scout_tg:
            continue
        if a not in candidates:
            candidates[a] = dict(o)

    # Tag scout wallets still missing WR for throttled GMGN fill priority
    pending_n = 0
    for a, row in candidates.items():
        tags = [str(t) for t in (row.get("tags") or [])]
        tags_l = [t.lower() for t in tags]
        scoutish = any(
            t in tags_l or row.get("scout_tier") == "elite"
            for t in ("scout_tg", "scout_tg_early", "scout_elite", "scout_good", "scout_pending_wr")
        )
        has_wr = row.get("win_rate") is not None and int(float(row.get("n_trades") or 0)) >= 10
        if scoutish and not has_wr:
            if "scout_pending_wr" not in tags_l:
                tags.append("scout_pending_WR")
                row["tags"] = tags
                pending_n += 1
        elif has_wr and "scout_pending_wr" in tags_l:
            row["tags"] = [t for t in tags if t.lower() != "scout_pending_wr"]
    if pending_n:
        print(f"scout_rank tagged scout_pending_WR={pending_n}", flush=True)

    fomo = fomo_index()
    fomo_hits = 0
    for a, row in candidates.items():
        if a in fomo:
            fomo_hits += 1
            row["fomo_handle"] = fomo[a].get("fomo_handle") or row.get("fomo_handle")
            row["fomo_pnl_usd"] = fomo[a].get("fomo_pnl_usd")
            eps = list(row.get("source_endpoints") or [])
            if "fomo_leaderboard" not in eps:
                eps.append("fomo_leaderboard")
            row["source_endpoints"] = eps

    # prioritize elite multi-hit without WR yet
    need_vet = []
    for a, row in candidates.items():
        if row.get("win_rate") is not None and int(row.get("n_trades") or 0) >= 10:
            continue
        need_vet.append(a)
    need_vet.sort(
        key=lambda a: (
            0 if candidates[a].get("scout_tier") == "elite" else 1,
            -int(candidates[a].get("scout_hit_count") or 0),
            -float(candidates[a].get("scout_sum_buy_usd") or 0),
        )
    )

    vetted = {}
    calls = 0
    err = None
    if env_bool("GMGN_DISABLED", False):
        print("scout_rank skip portfolio: GMGN_DISABLED=1", flush=True)
        err = "gmgn_disabled"
    elif need_vet and cap > 0:
        vetted, calls, err = batch_vet(chain, need_vet[:cap], cap)
        print(f"scout_rank portfolio vetted={len(vetted)} calls={calls} err={err}")
    else:
        print("scout_rank portfolio skip: nothing to vet or cap=0")

    for a, st in vetted.items():
        row = candidates.get(a) or {"address": a}
        if st.get("realized_pnl_usd") is not None:
            row["realized_pnl_usd"] = st["realized_pnl_usd"]
        if st.get("win_rate") is not None:
            row["win_rate"] = st["win_rate"]
        if st.get("n_trades") is not None:
            row["n_trades"] = st["n_trades"]
        for k in ("unrealized_pnl_usd", "total_pnl_usd", "gmgn_buy", "gmgn_sell"):
            if st.get(k) is not None:
                row[k] = st[k]
        eps = list(row.get("source_endpoints") or [])
        if "gmgn:portfolio" not in eps:
            eps.append("gmgn:portfolio")
        row["source_endpoints"] = eps
        row["vetted_at"] = now_iso()
        candidates[a] = row

    # Optional Nansen — only if key present and cheap flag
    if os.environ.get("NANSEN_API_KEY") and env_bool("SCOUT_NANSEN", False):
        print("scout_rank Nansen skipped (SCOUT_NANSEN experimental off by default)")

    ranked_list = []
    for a, row in candidates.items():
        row["quality_score"] = wallet_quality_score(row)
        row["scout_rank_score"] = scout_rank_score(row)
        row["consistent"] = bool(wallet_is_consistent(row)) if row.get("win_rate") is not None else False
        row["ranked_at"] = now_iso()
        ranked_list.append(row)
    ranked_list.sort(key=lambda r: -float(r.get("scout_rank_score") or 0))

    ranked_map = {r["address"]: r for r in ranked_list}
    kol_write_jsonl(RANKED_PATH, ranked_map)

    merged = 0
    if do_merge:
        merged = merge_rank_into_watch(ranked_map, watch)
        kol_write_jsonl(watch_path, watch)
        print(f"scout_rank merged_to_watch={merged}")

    stats = {
        "vetted": len(vetted),
        "calls": calls,
        "err": err,
        "fomo_hits": fomo_hits,
        "merged": merged,
        "cap": cap,
    }
    write_summary(ranked_list, stats)
    print(
        json.dumps(
            {
                "ok": True,
                "ranked": len(ranked_list),
                **stats,
                "top": [
                    {
                        "address": r["address"],
                        "score": r.get("scout_rank_score"),
                        "elite": r.get("scout_elite_count"),
                        "hits": r.get("scout_hit_count"),
                        "wr": r.get("win_rate"),
                        "rp": r.get("realized_pnl_usd"),
                    }
                    for r in ranked_list[:5]
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

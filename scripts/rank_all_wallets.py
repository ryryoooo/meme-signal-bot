#!/usr/bin/env python3
"""Rank ALL monitored smart wallets using scout_tg-resolved wallets as seed/benchmark.

Seed = resolved scout_tg wallets (elite weighted higher than good; hit frequency; TG buy USD).

Scores every wallet in rh-wallets/wallets.jsonl (optional arc overlap) relative to seed:
  - Direct: is scout_tg / elite / good / hit count / sum buy USD
  - Graph: co-appear on same tokens as seed wallets (shared token CAs from TG resolve)
  - Existing stats: realized_pnl_usd, win_rate, n_trades, wallet_quality_score, FOMO, Nansen

Writes:
  rh-wallets/wallets_ranked_all.jsonl
  rh-wallets/summary_rank_all.md

Promotes S/A into watch tags rank_s / rank_a (RANK_PROMOTE=1).

Env:
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  RANK_PROMOTE=1
  RANK_INCLUDE_ARC=1
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot as _bot  # noqa: E402

wallet_quality_score = _bot.wallet_quality_score
wallet_is_consistent = _bot.wallet_is_consistent

RAW = ROOT / "rh-wallets" / "raw"
WATCH_DEFAULT = ROOT / "rh-wallets" / "wallets.jsonl"
SCOUT_RANKED = ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl"
OUT_RANKED = ROOT / "rh-wallets" / "wallets_ranked_all.jsonl"
OUT_SUMMARY = ROOT / "rh-wallets" / "summary_rank_all.md"
ARC_WATCH = ROOT / "arc-wallets" / "wallets.jsonl"
FOMO_EVM = ROOT / "fomo-wallets" / "wallets_evm.jsonl"
FOMO_LB = ROOT / "fomo-wallets" / "leaderboard.jsonl"
RESOLVE_CACHE = RAW / "scout_tg_resolve_cache.jsonl"
HITS_PATH = RAW / "scout_tg_hits.jsonl"


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
        a = (o.get("address") or o.get("evm") or "").lower()
        if a.startswith("0x") and len(a) >= 42:
            out[a[:42]] = o
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _f(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def fomo_index() -> dict[str, dict]:
    by: dict[str, dict] = {}
    for path in (FOMO_EVM, FOMO_LB):
        for o in load_jsonl(path):
            a = (o.get("address") or o.get("evm") or "").lower()
            if not a.startswith("0x"):
                continue
            a = a[:42]
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


def build_seed_and_token_graph() -> tuple[dict[str, dict], dict[str, set[str]], dict[str, float]]:
    seed: dict[str, dict] = {}
    token_wallets: dict[str, set[str]] = defaultdict(set)
    hit_mult: dict[str, float] = {}

    for h in load_jsonl(HITS_PATH):
        ca = (h.get("token_ca") or "").lower()
        ticker = (h.get("ticker") or "").upper()
        m = _f(h.get("hit_mult"))
        if ca.startswith("0x") and m > 0:
            hit_mult[ca] = max(hit_mult.get(ca, 0.0), m)
        if ticker and m > 0:
            hit_mult[f"t:{ticker}"] = max(hit_mult.get(f"t:{ticker}", 0.0), m)

    for o in load_jsonl(RESOLVE_CACHE):
        a = (o.get("address") or "").lower()
        if not (a.startswith("0x") and len(a) == 42):
            continue
        agg = seed.setdefault(
            a,
            {
                "address": a,
                "scout_hit_count": 0,
                "scout_elite_count": 0,
                "scout_good_count": 0,
                "scout_sum_buy_usd": 0.0,
                "scout_token_cas": [],
                "scout_tickers": [],
                "scout_msg_ids": [],
                "scout_tier": "good",
                "is_seed": True,
            },
        )
        agg["scout_hit_count"] += 1
        if o.get("tier") == "elite":
            agg["scout_elite_count"] += 1
            agg["scout_tier"] = "elite"
        else:
            agg["scout_good_count"] += 1
        agg["scout_sum_buy_usd"] += _f(o.get("usd"))
        ca = (o.get("token_ca") or "").lower()
        if ca.startswith("0x"):
            if ca not in agg["scout_token_cas"]:
                agg["scout_token_cas"].append(ca)
            token_wallets[ca].add(a)
        tick = o.get("ticker")
        if tick and tick not in agg["scout_tickers"]:
            agg["scout_tickers"].append(tick)
        mid = o.get("msg_id")
        if mid and mid not in agg["scout_msg_ids"]:
            agg["scout_msg_ids"].append(mid)

    return seed, dict(token_wallets), hit_mult


def is_scoutish(row: dict) -> bool:
    parts = [
        str(x).lower()
        for x in list(row.get("tags") or [])
        + list(row.get("gmgn_tags") or [])
        + list(row.get("sources") or [])
        + list(row.get("source_endpoints") or [])
    ]
    return any(
        x in ("scout_tg", "scout_elite", "scout_good", "scout_tg_early", "telegram:scoutrobinhood")
        or x.startswith("scout_tg")
        for x in parts
    ) or bool(row.get("scout_tier") or row.get("scout_hit_count"))


def compute_affinity(
    addr: str,
    row: dict,
    seed: dict[str, dict],
    token_wallets: dict[str, set[str]],
) -> dict:
    my_tokens: set[str] = set()
    for ca in list(row.get("scout_token_cas") or []) + list((seed.get(addr) or {}).get("scout_token_cas") or []):
        c = str(ca).lower()
        if c.startswith("0x"):
            my_tokens.add(c)
    for ca, addrs in token_wallets.items():
        if addr in addrs:
            my_tokens.add(ca)

    elite_seed = {
        a
        for a, s in seed.items()
        if s.get("scout_tier") == "elite" or _i(s.get("scout_elite_count")) > 0
    }
    shared = shared_elite = 0
    co_seeds: set[str] = set()
    for ca in my_tokens:
        peers = token_wallets.get(ca) or set()
        seed_peers = (peers & set(seed.keys())) - {addr}
        if seed_peers:
            shared += 1
            co_seeds |= seed_peers
        if (peers & elite_seed) - {addr}:
            shared_elite += 1
    return {
        "affinity_shared_tokens": shared,
        "affinity_shared_elite_tokens": shared_elite,
        "affinity_co_seed_wallets": len(co_seeds),
        "affinity_my_tokens": len(my_tokens),
    }


def score_wallet(
    row: dict,
    seed: dict[str, dict],
    aff: dict,
    hit_mult: dict[str, float],
) -> tuple[float, dict]:
    addr = (row.get("address") or "").lower()
    s = seed.get(addr) or {}
    elite_n = max(_i(row.get("scout_elite_count")), _i(s.get("scout_elite_count")))
    good_n = max(_i(row.get("scout_good_count")), _i(s.get("scout_good_count")))
    hits = max(_i(row.get("scout_hit_count")), _i(s.get("scout_hit_count")))
    buy_usd = max(_f(row.get("scout_sum_buy_usd")), _f(s.get("scout_sum_buy_usd")))
    tier = row.get("scout_tier") or s.get("scout_tier") or ""

    direct = 0.0
    if addr in seed or is_scoutish(row):
        direct += 1.5
    direct += min(4.0, elite_n * 1.2)
    direct += min(2.0, good_n * 0.35)
    direct += min(3.0, hits * 0.45)
    direct += min(2.5, buy_usd / 400.0)
    if tier == "elite":
        direct += 0.8

    graph = 0.0
    graph += min(3.0, _i(aff.get("affinity_shared_tokens")) * 0.7)
    graph += min(3.5, _i(aff.get("affinity_shared_elite_tokens")) * 1.1)
    graph += min(2.0, _i(aff.get("affinity_co_seed_wallets")) * 0.15)

    hit_bonus = 0.0
    for ca in list(row.get("scout_token_cas") or []) + list(s.get("scout_token_cas") or []):
        m = hit_mult.get(str(ca).lower()) or 0.0
        if m >= 10:
            hit_bonus += 1.2
        elif m >= 5:
            hit_bonus += 0.7
        elif m >= 2:
            hit_bonus += 0.3
    hit_bonus = min(2.5, hit_bonus)

    stats = 0.0
    rp = _f(row.get("realized_pnl_usd"))
    if rp >= 50_000:
        stats += 2.5
    elif rp >= 10_000:
        stats += 2.0
    elif rp >= 1_000:
        stats += 1.2
    elif rp >= 500:
        stats += 0.6
    elif rp > 0:
        stats += 0.2
    if 0 < rp < 1.0:
        stats -= 1.0  # reject $0.01 fake pnl

    wr = _f(row.get("win_rate"))
    if wr > 1.5:
        wr /= 100.0
    nt = _i(row.get("n_trades"))
    if nt >= 15 and wr >= 0.45:
        stats += 1.8
    elif nt >= 10 and wr >= 0.4:
        stats += 1.0
    elif nt >= 5 and wr >= 0.4:
        stats += 0.4

    q = _f(row.get("quality_score") or row.get("wallet_quality_score"))
    if q <= 0:
        try:
            q = float(wallet_quality_score(row))
        except Exception:
            q = 0.0
    stats += min(2.0, q * 0.5)

    if row.get("fomo_handle"):
        stats += 0.5
        if _f(row.get("fomo_pnl_usd")) >= 20_000:
            stats += 0.5

    blob = " ".join(
        str(x)
        for x in list(row.get("tags") or [])
        + list(row.get("sources") or [])
        + list(row.get("source_endpoints") or [])
        + [row.get("source") or ""]
    ).lower()
    if "nansen" in blob or "pnl-leaderboard" in blob:
        stats += 0.6

    try:
        consistent = bool(wallet_is_consistent(row)) if row.get("win_rate") is not None else False
    except Exception:
        consistent = False
    if consistent:
        stats += 0.8

    total = round(direct + graph + hit_bonus + stats, 3)
    return total, {
        "score_direct": round(direct, 3),
        "score_graph": round(graph, 3),
        "score_hit_bonus": round(hit_bonus, 3),
        "score_stats": round(stats, 3),
        "quality_score": q,
        "consistent": consistent,
    }


def assign_tier(score: float, row: dict, seed: dict[str, dict]) -> str:
    """S/A require scout-seed signal or strong elite-token affinity (quality > spam)."""
    addr = (row.get("address") or "").lower()
    elite = max(_i(row.get("scout_elite_count")), _i((seed.get(addr) or {}).get("scout_elite_count")))
    rp = _f(row.get("realized_pnl_usd"))
    is_seed = bool(row.get("is_seed") or addr in seed)
    aff_e = _i(row.get("affinity_shared_elite_tokens"))
    aff = _i(row.get("affinity_shared_tokens"))
    scout_linked = is_seed or aff_e >= 1 or (aff >= 2 and elite >= 1)
    if score >= 10 and scout_linked and (elite >= 1 or rp >= 5000 or row.get("consistent") or aff_e >= 2):
        return "S"
    if score >= 9 and scout_linked and (elite >= 2 or aff_e >= 2):
        return "S"
    if score >= 6.5 and scout_linked:
        return "A"
    # High-quality non-seed track record can reach B/A- cautiously as B only unless exceptional
    if score >= 7.5 and row.get("consistent") and rp >= 10_000:
        return "A"
    if score >= 5.0 and (scout_linked or (row.get("consistent") and rp >= 1000)):
        return "B"
    if score >= 3.0:
        return "B" if scout_linked else "C"
    return "C"


def promote_tags(watch: dict[str, dict], ranked: list[dict]) -> int:
    """Promote only S + A that are seed-linked or exceptional consistent winners."""
    n = 0
    for r in ranked:
        tier = r.get("rank_tier")
        if tier not in ("S", "A"):
            continue
        addr = r["address"]
        if addr not in watch:
            continue
        # Extra gate: do not promote pure-PnL A without scout affinity
        if tier == "A" and not (
            r.get("is_seed")
            or _i(r.get("affinity_shared_elite_tokens")) >= 1
            or _i(r.get("affinity_shared_tokens")) >= 2
            or (_f(r.get("realized_pnl_usd")) >= 50_000 and r.get("consistent"))
        ):
            continue
        o = watch[addr]
        tags = list(o.get("tags") or [])
        want = f"rank_{tier.lower()}"
        other = "rank_a" if tier == "S" else "rank_s"
        changed = False
        if other in tags and tier == "S":
            tags = [t for t in tags if t != other]
            changed = True
        if want not in tags:
            tags.append(want)
            changed = True
        if "rank_promoted" not in tags:
            tags.append("rank_promoted")
            changed = True
        if changed:
            o["tags"] = tags
            o["rank_tier"] = tier
            o["rank_score"] = r.get("rank_score")
            o["ranked_all_at"] = now_iso()
            watch[addr] = o
            n += 1
    return n


def write_summary(ranked: list[dict], stats: dict) -> None:
    tiers = {"S": 0, "A": 0, "B": 0, "C": 0}
    for r in ranked:
        t = r.get("rank_tier") or "C"
        tiers[t] = tiers.get(t, 0) + 1
    lines = [
        "# All-wallet ranking (scout_tg seed benchmark)",
        "",
        f"- Updated (UTC): {now_iso()}",
        f"- Ranked wallets: **{len(ranked)}**",
        f"- Seed (scout_tg resolved): **{stats.get('seed_n', 0)}** (elite={stats.get('seed_elite', 0)})",
        f"- Token graph size: **{stats.get('token_n', 0)}**",
        f"- FOMO overlaps: **{stats.get('fomo_hits', 0)}**",
        f"- Promoted tags (rank_s/rank_a): **{stats.get('promoted', 0)}**",
        f"- Tiers: S=**{tiers['S']}** A=**{tiers['A']}** B=**{tiers['B']}** C=**{tiers['C']}**",
        "",
        "## Top 40 by rank_score",
        "",
        "| # | tier | addr | seed | elite | affE | WR | n | realized | FOMO | score |",
        "|---|------|------|------|-------|------|----|---|----------|------|-------|",
    ]
    for i, r in enumerate(ranked[:40], 1):
        a = r.get("address") or ""
        short = f"{a[:6]}…{a[-4:]}" if len(a) >= 10 else a
        wr = r.get("win_rate")
        if isinstance(wr, (int, float)):
            wr_s = f"{float(wr):.0%}" if float(wr) <= 1.5 else f"{float(wr):.0f}%"
        else:
            wr_s = "—"
        rp = r.get("realized_pnl_usd")
        rp_s = f"{float(rp):,.0f}" if isinstance(rp, (int, float)) else "—"
        lines.append(
            f"| {i} | {r.get('rank_tier')} | `{short}` | "
            f"{'Y' if r.get('is_seed') else '·'} | {_i(r.get('scout_elite_count'))} | "
            f"{_i(r.get('affinity_shared_elite_tokens'))} | {wr_s} | {r.get('n_trades') or '—'} | "
            f"{rp_s} | {r.get('fomo_handle') or '—'} | {r.get('rank_score')} |"
        )
    lines += ["", "## Tier S", ""]
    for r in [x for x in ranked if x.get("rank_tier") == "S"][:25]:
        lines.append(
            f"- `{r['address']}` score={r.get('rank_score')} elite={r.get('scout_elite_count')} "
            f"affE={r.get('affinity_shared_elite_tokens')} rp={r.get('realized_pnl_usd')} wr={r.get('win_rate')}"
        )
    lines += ["", "## Tier A", ""]
    for r in [x for x in ranked if x.get("rank_tier") == "A"][:25]:
        lines.append(
            f"- `{r['address']}` score={r.get('rank_score')} elite={r.get('scout_elite_count')} "
            f"affE={r.get('affinity_shared_elite_tokens')} rp={r.get('realized_pnl_usd')} wr={r.get('win_rate')}"
        )
    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(WATCH_DEFAULT)))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path

    seed, token_wallets, hit_mult = build_seed_and_token_graph()
    watch = load_map(watch_path)
    candidates: dict[str, dict] = {a: dict(o) for a, o in watch.items()}

    for a, s in seed.items():
        if a not in candidates:
            row = dict(s)
            row["tags"] = ["scout_tg", "scout_tg_early"]
            if s.get("scout_tier") == "elite":
                row["tags"].append("scout_elite")
            candidates[a] = row
        else:
            for k in (
                "scout_hit_count",
                "scout_elite_count",
                "scout_good_count",
                "scout_sum_buy_usd",
            ):
                if _f(s.get(k)) > _f(candidates[a].get(k)):
                    candidates[a][k] = s[k]
            if s.get("scout_tier") == "elite":
                candidates[a]["scout_tier"] = "elite"
            cas = list(candidates[a].get("scout_token_cas") or [])
            for c in s.get("scout_token_cas") or []:
                if c not in cas:
                    cas.append(c)
            candidates[a]["scout_token_cas"] = cas[-50:]

    if env_bool("RANK_INCLUDE_ARC", True) and ARC_WATCH.exists():
        for a, o in load_map(ARC_WATCH).items():
            if a not in candidates:
                row = dict(o)
                row["arc_overlap"] = True
                candidates[a] = row
            else:
                candidates[a]["arc_overlap"] = True

    for a, o in load_map(SCOUT_RANKED).items():
        if a not in candidates:
            candidates[a] = dict(o)
            continue
        for k in (
            "win_rate",
            "n_trades",
            "realized_pnl_usd",
            "scout_rank_score",
            "quality_score",
            "unrealized_pnl_usd",
            "total_pnl_usd",
        ):
            if o.get(k) is not None and candidates[a].get(k) is None:
                candidates[a][k] = o[k]

    fomo = fomo_index()
    fomo_hits = 0
    for a, row in candidates.items():
        if a in fomo:
            fomo_hits += 1
            row["fomo_handle"] = fomo[a].get("fomo_handle") or row.get("fomo_handle")
            row["fomo_pnl_usd"] = fomo[a].get("fomo_pnl_usd")

    ranked: list[dict] = []
    for a, row in candidates.items():
        row["address"] = a
        row["is_seed"] = a in seed
        aff = compute_affinity(a, row, seed, token_wallets)
        row.update(aff)
        total, br = score_wallet(row, seed, aff, hit_mult)
        row["rank_score"] = total
        row.update(br)
        row["rank_tier"] = assign_tier(total, row, seed)
        row["ranked_all_at"] = now_iso()
        ranked.append(row)

    ranked.sort(key=lambda r: (-float(r.get("rank_score") or 0), -_f(r.get("realized_pnl_usd"))))
    write_jsonl(OUT_RANKED, ranked)

    promoted = 0
    if env_bool("RANK_PROMOTE", True):
        promoted = promote_tags(watch, ranked)
        write_jsonl(watch_path, [watch[a] for a in sorted(watch)])
        print(f"rank_all promoted_tags={promoted}")

    stats = {
        "seed_n": len(seed),
        "seed_elite": sum(1 for s in seed.values() if s.get("scout_tier") == "elite"),
        "token_n": len(token_wallets),
        "fomo_hits": fomo_hits,
        "promoted": promoted,
    }
    write_summary(ranked, stats)
    tiers: dict[str, int] = {}
    for r in ranked:
        tiers[r.get("rank_tier") or "C"] = tiers.get(r.get("rank_tier") or "C", 0) + 1
    print(
        json.dumps(
            {
                "ok": True,
                "ranked": len(ranked),
                "tiers": tiers,
                **stats,
                "top": [
                    {
                        "address": r["address"],
                        "tier": r.get("rank_tier"),
                        "score": r.get("rank_score"),
                        "elite": r.get("scout_elite_count"),
                        "affE": r.get("affinity_shared_elite_tokens"),
                    }
                    for r in ranked[:8]
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

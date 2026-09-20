#!/usr/bin/env python3
"""Analyze unknown-trend candidates and promote passers into rh-wallets/wallets.jsonl.

Each trend_hunt cycle stages unknowns in:
  - rh-wallets/watch_candidates_unknown.jsonl  (top gate)
  - rh-wallets/unknown_trend_smart.jsonl       (published multi-CA / strong)

This script **does not** dump the full list. It applies quality gates, then
appends/merges only passers (capped) into the main watchlist.

Gates (env, tunable):
  PROMOTE_MIN_SCORE=40
  PROMOTE_MIN_CAS=2
  PROMOTE_MIN_BUYS=2
  PROMOTE_MIN_VOL_USD=10
  PROMOTE_ACTIVE_HOURS=72
  PROMOTE_MAX_PER_CYCLE=40
  PROMOTE_MAX_CAS=10          # hub/bot-like if more distinct CAs in one hunt
  PROMOTE_POOL_EXTRA=30       # also consider top N from unknown_trend_smart
  PROMOTE_REQUIRE_PEAK=0      # 1 = must overlap notify_peak CA
  PROMOTE_RPC_CHECK=0         # 1 = eth_getCode reject contracts (light RPC)
  PROMOTE_DRY_RUN=0
  LIVE_TRADING=0 / GMGN_DISABLED=1 — never call GMGN on box

Outputs:
  rh-wallets/wallets.jsonl                  (merged, backup first)
  rh-wallets/promote_unknown_log.jsonl      (append one record per run)
  rh-wallets/summary_promote_unknown.md
  rh-wallets/raw/wallets_pre_unknown_promote.jsonl  (rolling backup)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
WATCH = Path(os.environ.get("WATCHLIST_PATH") or (ROOT / "rh-wallets" / "wallets.jsonl"))
CAND_PATH = ROOT / "rh-wallets" / "watch_candidates_unknown.jsonl"
UNKNOWN_PATH = ROOT / "rh-wallets" / "unknown_trend_smart.jsonl"
LOG_PATH = ROOT / "rh-wallets" / "promote_unknown_log.jsonl"
SUMMARY_PATH = ROOT / "rh-wallets" / "summary_promote_unknown.md"
BACKUP_PATH = ROOT / "rh-wallets" / "raw" / "wallets_pre_unknown_promote.jsonl"
PEAK_PATH = ROOT / "artifacts" / "notify_peak_2x.json"
RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)

SKIP_ADDRS = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x00000000000000000000000000000000000a4b05",
    "0xffffffffffffffffffffffffffffffffffffffff",
}

# Known RH / EVM infra / CEX-ish / LP hubs to never promote
REJECT_ADDRS = {
    "0x4200000000000000000000000000000000000006",  # WETH
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG
}

STABLE_SYMS = {
    "usdg", "usdc", "usdt", "dai", "usd", "weth", "eth", "wbtc", "btc",
    "usde", "susde", "usds", "eurc",
}

REJECT_LABEL_NEEDLES = (
    "router", "univ", "uniswap", "pancake", "cex", "binance", "coinbase",
    "okx", "bybit", "kraken", "hub", "aggregator", "relay", "bridge",
    "mev", "bot_contract", "lp_manager", "liquidity",
)


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} promote-unknown {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def norm_addr(a: str | None) -> str | None:
    if not isinstance(a, str):
        return None
    al = a.strip().lower()
    if al.startswith("0x") and len(al) == 42:
        return al
    return None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_ts(ts: str | None) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_peak_cas() -> set[str]:
    out: set[str] = set()
    if not PEAK_PATH.exists():
        return out
    try:
        data = json.loads(PEAK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in (
            "tokens_ge2x_peak",
            "tokens_ge2x_now",
            "rows",
            "tokens",
            "items",
            "events",
        ):
            v = data.get(key)
            if isinstance(v, list):
                rows.extend(v)
        peaks = data.get("peaks")
        if isinstance(peaks, dict):
            for k, v in peaks.items():
                if isinstance(k, str) and k.startswith("0x") and len(k) == 42:
                    out.add(k.lower())
                if isinstance(v, dict):
                    ca = norm_addr(v.get("ca") or v.get("address") or v.get("token"))
                    if ca:
                        out.add(ca)
                elif isinstance(v, str):
                    ca = norm_addr(v)
                    if ca:
                        out.add(ca)
        for k, v in data.items():
            if isinstance(k, str) and k.startswith("0x") and len(k) == 42:
                out.add(k.lower())
    for r in rows:
        if not isinstance(r, dict):
            continue
        ca = norm_addr(r.get("ca") or r.get("address") or r.get("token") or r.get("token_ca"))
        if ca:
            out.add(ca)
    return out


def rpc_get_code(addr: str) -> str | None:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [addr, "latest"]}
    ).encode()
    req = urllib.request.Request(
        RPC_URL,
        data=payload,
        headers={"content-type": "application/json", "User-Agent": UA, "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode() or "{}")
        if isinstance(body, dict) and body.get("error"):
            return None
        code = body.get("result") if isinstance(body, dict) else None
        return code if isinstance(code, str) else None
    except Exception as e:
        log(f"rpc getCode fail {addr[:10]}… {e}")
        return None


def is_stable_only(symbols: list | None) -> bool:
    syms = [str(s).strip().lower() for s in (symbols or []) if s]
    if not syms:
        return False
    return all(s in STABLE_SYMS for s in syms)


def looks_rejected_label(row: dict) -> bool:
    blob = " ".join(
        str(x).lower()
        for x in (
            [row.get("address_label"), row.get("note"), row.get("quality_reason")]
            + list(row.get("tags") or [])
            + list(row.get("symbols") or [])
        )
        if x
    )
    return any(n in blob for n in REJECT_LABEL_NEEDLES if n in ("router", "cex", "binance", "coinbase", "aggregator", "mev", "bot_contract", "lp_manager"))


def build_pool(unknown_by: dict[str, dict], peak_cas: set[str]) -> list[dict]:
    """Prefer staged watch candidates, enrich from full unknown, then top extras."""
    pool: dict[str, dict] = {}
    cands = load_jsonl(CAND_PATH)
    for r in cands:
        a = norm_addr(r.get("address"))
        if not a:
            continue
        full = unknown_by.get(a) or {}
        merged = {**full, **{k: v for k, v in r.items() if v is not None}}
        # prefer full hunt fields for activity/vol
        for k in ("n_buys", "sum_vol_usd", "last_seen", "first_seen", "has_peak_ca", "n_trend_cas", "best_early_rank", "cas", "symbols", "score", "n_cas"):
            if full.get(k) is not None and (merged.get(k) is None or k in ("n_buys", "sum_vol_usd", "last_seen", "has_peak_ca")):
                if k in ("n_buys", "sum_vol_usd", "last_seen", "has_peak_ca", "n_trend_cas", "best_early_rank") or merged.get(k) is None:
                    merged[k] = full.get(k)
        if full.get("cas"):
            merged["cas"] = full["cas"]
        if full.get("symbols"):
            merged["symbols"] = full["symbols"]
        if full.get("score") is not None:
            merged["score"] = max(float(merged.get("score") or 0), float(full["score"]))
        if full.get("n_cas") is not None:
            merged["n_cas"] = max(int(merged.get("n_cas") or 0), int(full["n_cas"]))
        merged["_pool_src"] = "watch_candidates"
        pool[a] = merged

    extra_n = max(0, env_int("PROMOTE_POOL_EXTRA", 30))
    ranked = sorted(unknown_by.values(), key=lambda r: float(r.get("score") or 0), reverse=True)
    added = 0
    for r in ranked:
        a = norm_addr(r.get("address"))
        if not a or a in pool:
            continue
        if added >= extra_n:
            break
        row = dict(r)
        row["_pool_src"] = "unknown_top"
        pool[a] = row
        added += 1

    # annotate peak overlap
    for a, r in pool.items():
        cas = [norm_addr(c) for c in (r.get("cas") or [])]
        cas = [c for c in cas if c]
        overlap = [c for c in cas if c in peak_cas]
        r["peak_overlap_n"] = len(overlap)
        if overlap and not r.get("has_peak_ca"):
            r["has_peak_ca"] = True
        r["address"] = a
    return list(pool.values())


def analyze(row: dict, now: datetime, gates: dict) -> tuple[bool, list[str], str]:
    """Return (pass, reject_reasons, quality_reason)."""
    reasons: list[str] = []
    a = norm_addr(row.get("address"))
    if not a:
        return False, ["bad_address"], ""
    if a in SKIP_ADDRS or a in REJECT_ADDRS:
        return False, ["skip_or_infra"], ""
    if looks_rejected_label(row):
        return False, ["reject_label"], ""

    score = float(row.get("score") or 0)
    n_cas = int(row.get("n_cas") or len(row.get("cas") or []) or 0)
    n_buys = int(row.get("n_buys") or 0)
    vol = float(row.get("sum_vol_usd") or 0)
    last = parse_ts(row.get("last_seen"))
    age_h = ((now - last).total_seconds() / 3600.0) if last else None

    if score < gates["min_score"]:
        reasons.append(f"score<{gates['min_score']}")
    if n_cas < gates["min_cas"]:
        reasons.append(f"n_cas<{gates['min_cas']}")
    if n_cas > gates["max_cas"]:
        reasons.append(f"hub_n_cas>{gates['max_cas']}")
    if n_buys < gates["min_buys"] and n_cas < 3:
        reasons.append(f"n_buys<{gates['min_buys']}")
    if vol < gates["min_vol"] and n_cas < 3:
        reasons.append(f"vol<{gates['min_vol']}")
    if is_stable_only(row.get("symbols")):
        reasons.append("stable_only")
    if age_h is None:
        # no last_seen — allow if score strong + multi-CA, else reject
        if not (n_cas >= 3 and score >= gates["min_score"] + 10):
            reasons.append("no_last_seen")
    elif age_h > gates["active_hours"]:
        reasons.append(f"inactive_{age_h:.0f}h")
    if gates["require_peak"] and not row.get("has_peak_ca") and int(row.get("peak_overlap_n") or 0) < 1:
        reasons.append("no_peak_ca")

    if reasons:
        return False, reasons, ""

    parts = [
        f"score={score:.1f}",
        f"n_cas={n_cas}",
        f"buys={n_buys}",
        f"vol=${vol:.0f}",
    ]
    if age_h is not None:
        parts.append(f"last={age_h:.1f}h")
    if row.get("has_peak_ca") or int(row.get("peak_overlap_n") or 0) > 0:
        parts.append(f"peak_overlap={int(row.get('peak_overlap_n') or 0)}")
    parts.append(f"src={row.get('_pool_src') or 'unknown'}")
    return True, [], "active_multi_ca:" + ",".join(parts)


def to_watch_row(row: dict, quality_reason: str, promoted_at: str) -> dict:
    a = row["address"]
    short = f"{a[:6]}…{a[-4:]}"
    cas = [c for c in (norm_addr(x) for x in (row.get("cas") or [])) if c]
    symbols = list(row.get("symbols") or [])[:20]
    tags = ["unknown_trend", "unknown_trend_smart", "source=trend_hunt", "pnl_pending"]
    if row.get("has_peak_ca") or int(row.get("peak_overlap_n") or 0) > 0:
        tags.append("peak_ca_overlap")
    return {
        "address": a,
        "address_label": f"unknown_trend [{short}]",
        "realized_pnl_usd": None,
        "win_rate": None,
        "n_trades": None,
        "tags": tags,
        "gmgn_tags": [],
        "sources": ["trend_hunt"],
        "source_endpoints": list(row.get("sources") or ["trend_hunt"]),
        "chain": "robinhood",
        "list_tier": "activity_only",
        "pass_pnl": False,
        "quality_reason": quality_reason,
        "quality_score": round(min(1.0, float(row.get("score") or 0) / 120.0), 3),
        "trend_score": float(row.get("score") or 0),
        "n_cas": int(row.get("n_cas") or len(cas) or 0),
        "scout_token_cas": cas[:20],
        "symbols_seen": symbols,
        "n_buys": int(row.get("n_buys") or 0),
        "sum_vol_usd": float(row.get("sum_vol_usd") or 0),
        "last_seen": row.get("last_seen"),
        "first_seen": row.get("first_seen"),
        "has_peak_ca": bool(row.get("has_peak_ca")),
        "promoted_at": promoted_at,
        "collected_at": promoted_at,
        "discovered_at": row.get("discovered_at"),
        "promote_pool_src": row.get("_pool_src"),
    }


def promote_once(dry_run: bool | None = None) -> dict:
    t0 = time.time()
    if dry_run is None:
        dry_run = env_bool("PROMOTE_DRY_RUN", False)

    gates = {
        "min_score": env_float("PROMOTE_MIN_SCORE", 40.0),
        "min_cas": env_int("PROMOTE_MIN_CAS", 2),
        "min_buys": env_int("PROMOTE_MIN_BUYS", 2),
        "min_vol": env_float("PROMOTE_MIN_VOL_USD", 10.0),
        "active_hours": env_float("PROMOTE_ACTIVE_HOURS", 72.0),
        "max_per": env_int("PROMOTE_MAX_PER_CYCLE", 40),
        "max_cas": env_int("PROMOTE_MAX_CAS", 10),
        "require_peak": env_bool("PROMOTE_REQUIRE_PEAK", False),
        "rpc_check": env_bool("PROMOTE_RPC_CHECK", False),
    }
    now = datetime.now(timezone.utc)
    promoted_at = now_iso()

    unknown_rows = load_jsonl(UNKNOWN_PATH)
    unknown_by = {}
    for r in unknown_rows:
        a = norm_addr(r.get("address"))
        if a:
            unknown_by[a] = r
    peak_cas = load_peak_cas()
    pool = build_pool(unknown_by, peak_cas)
    log(
        f"pool={len(pool)} unknown={len(unknown_by)} peak_cas={len(peak_cas)} "
        f"gates score>={gates['min_score']} cas>={gates['min_cas']} max={gates['max_per']}"
    )

    watch_rows = load_jsonl(WATCH)
    watch: dict[str, dict] = {}
    for r in watch_rows:
        a = norm_addr(r.get("address"))
        if a:
            watch[a] = r
    before_n = len(watch)

    passed: list[tuple[dict, str]] = []
    rejected: list[dict] = []
    skipped_existing = 0

    for row in pool:
        a = row.get("address")
        if a in watch:
            skipped_existing += 1
            continue
        ok, reasons, qreason = analyze(row, now, gates)
        if not ok:
            rejected.append({"address": a, "reasons": reasons, "score": row.get("score"), "n_cas": row.get("n_cas")})
            continue
        passed.append((row, qreason))

    # rank passers: score, n_cas, peak, vol
    passed.sort(
        key=lambda t: (
            float(t[0].get("score") or 0),
            int(t[0].get("n_cas") or 0),
            int(t[0].get("peak_overlap_n") or 0),
            float(t[0].get("sum_vol_usd") or 0),
        ),
        reverse=True,
    )
    selected = passed[: max(0, gates["max_per"])]

    # optional contract reject
    contract_rejects = 0
    final: list[tuple[dict, str]] = []
    if gates["rpc_check"]:
        for row, qreason in selected:
            code = rpc_get_code(row["address"])
            time.sleep(0.15)
            if code and code not in ("0x", "0x0"):
                contract_rejects += 1
                rejected.append({"address": row["address"], "reasons": ["is_contract"], "score": row.get("score")})
                continue
            final.append((row, qreason))
    else:
        final = selected

    added_rows: list[dict] = []
    for row, qreason in final:
        w = to_watch_row(row, qreason, promoted_at)
        added_rows.append(w)
        watch[w["address"]] = w

    after_n = len(watch)
    added_n = len(added_rows)

    if not dry_run and added_n > 0:
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        if WATCH.exists():
            shutil.copy2(WATCH, BACKUP_PATH)
            # also timestamped spare
            ts_name = BACKUP_PATH.with_name(
                f"wallets_pre_unknown_promote_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.jsonl"
            )
            shutil.copy2(WATCH, ts_name)
        # stable sort by address for deterministic file
        write_jsonl(WATCH, [watch[a] for a in sorted(watch.keys())])
        log(f"wrote wallets.jsonl before={before_n} after={after_n} added={added_n}")
    elif dry_run:
        log(f"DRY_RUN would add={added_n} watch {before_n}→{before_n + added_n}")
    else:
        log(f"no new wallets to add (before={before_n})")

    sample = [
        {
            "address": r["address"],
            "score": r.get("trend_score"),
            "n_cas": r.get("n_cas"),
            "symbols": (r.get("symbols_seen") or [])[:5],
            "quality_reason": r.get("quality_reason"),
        }
        for r in added_rows[:12]
    ]

    run_rec = {
        "promoted_at": promoted_at,
        "dry_run": dry_run,
        "gates": gates,
        "pool": len(pool),
        "unknown_published": len(unknown_by),
        "passed": len(passed),
        "selected": added_n,
        "rejected": len(rejected),
        "skipped_existing": skipped_existing,
        "contract_rejects": contract_rejects,
        "watch_before": before_n,
        "watch_after": after_n if not dry_run else before_n + added_n,
        "added_addresses": [r["address"] for r in added_rows],
        "sample": sample,
        "reject_sample": rejected[:15],
        "elapsed_s": round(time.time() - t0, 2),
    }
    if not dry_run:
        append_jsonl(LOG_PATH, run_rec)

    jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        f"# Promote unknown smart — {jst}",
        "",
        f"- Dry run: **{int(dry_run)}**",
        f"- Pool analyzed: **{len(pool)}** (candidates + top extra)",
        f"- Unknown published: **{len(unknown_by)}**",
        f"- Passed gates: **{len(passed)}** → selected/capped: **{added_n}**",
        f"- Rejected: **{len(rejected)}** | already in watch: **{skipped_existing}** | contracts: **{contract_rejects}**",
        f"- Watchlist: **{before_n}** → **{run_rec['watch_after']}** (+{added_n})",
        f"- Gates: score≥{gates['min_score']} cas≥{gates['min_cas']}…≤{gates['max_cas']} "
        f"buys≥{gates['min_buys']} vol≥${gates['min_vol']} active≤{gates['active_hours']}h "
        f"max/cycle={gates['max_per']}",
        f"- Elapsed: **{run_rec['elapsed_s']}s**",
        "",
        "## Added this run",
        "",
    ]
    if added_rows:
        for i, r in enumerate(added_rows[:25], 1):
            lines.append(
                f"{i}. `{r['address']}` score={r.get('trend_score')} n_cas={r.get('n_cas')} "
                f"syms={','.join((r.get('symbols_seen') or [])[:6])} — {r.get('quality_reason')}"
            )
    else:
        lines.append("_None_")
    lines += ["", "## Reject sample", ""]
    if rejected:
        for r in rejected[:12]:
            lines.append(
                f"- `{r.get('address')}` score={r.get('score')} n_cas={r.get('n_cas')} "
                f"reasons={','.join(r.get('reasons') or [])}"
            )
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Notes",
        "",
        "- No GMGN on box; tagged `pnl_pending` for later GHA throttle vet.",
        "- Backup: `rh-wallets/raw/wallets_pre_unknown_promote.jsonl`",
        "- Log: `rh-wallets/promote_unknown_log.jsonl`",
        "",
    ]
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(
        f"done added={added_n} passed={len(passed)} rejected={len(rejected)} "
        f"watch={before_n}→{run_rec['watch_after']} elapsed={run_rec['elapsed_s']}s"
    )
    return run_rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote analyzed unknown-trend smart wallets into watchlist")
    ap.add_argument("--dry-run", action="store_true", help="analyze only, do not write wallets.jsonl")
    ap.add_argument("--apply", action="store_true", help="force write (overrides PROMOTE_DRY_RUN)")
    args = ap.parse_args()
    dry = True if args.dry_run else (False if args.apply else None)
    rec = promote_once(dry_run=dry)
    print(json.dumps({
        "added": rec["selected"],
        "passed": rec["passed"],
        "rejected": rec["rejected"],
        "watch_before": rec["watch_before"],
        "watch_after": rec["watch_after"],
        "sample": [s["address"] for s in rec["sample"][:8]],
        "dry_run": rec["dry_run"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

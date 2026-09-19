#!/usr/bin/env python3
"""Throttled GMGN portfolio vet — GHA only. Avoids box IP ban and big bursts.

Strategy:
  - Never run with GMGN_DISABLED=1 (caller must enable on GHA)
  - Tiny per-run cap (default 8)
  - Long sleep between calls (default 12s)
  - Stop immediately on rate_limited / 429
  - Skip recently failed / recently vetted addresses
  - Prioritize scout_elite / scout_pending_WR missing win_rate

Env:
  GMGN_DISABLED=0
  CHAIN=robinhood
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  GMGN_VET_CAP=8
  GMGN_VET_SLEEP_SEC=12
  GMGN_VET_COOLDOWN_HOURS=6
  GMGN_VET_PRIORITY=elite   # elite|pending_wr|all
                             # elite = scout_elite + scout_pending_WR (+ themaran/985)
  GMGN_PORTFOLIO_PERIOD=30d  # 30d (default) or 7d — 7d writes *_7d fields only
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_kol = _load("harvest_kol_vetted", ROOT / "scripts" / "harvest_kol_vetted.py")
batch_vet = _kol.batch_vet
portfolio_period = getattr(_kol, "portfolio_period", lambda: (os.environ.get("GMGN_PORTFOLIO_PERIOD") or "30d").strip().lower() or "30d")

STATE_DIR = ROOT / "rh-wallets" / "raw"
STATE_PATH = STATE_DIR / "gmgn_vet_throttle_state.json"
SUMMARY = ROOT / "rh-wallets" / "summary_gmgn_vet.md"


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


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


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"ok": {}, "fail": {}, "last_rate_limit_at": None}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": {}, "fail": {}, "last_rate_limit_at": None}


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    if env_bool("GMGN_DISABLED", False):
        print("vet_gmgn_throttled: GMGN_DISABLED=1 — refuse (use GHA)", flush=True)
        return 0

    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets" / "wallets.jsonl")))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path
    chain = os.environ.get("CHAIN", "robinhood")
    cap = max(0, int(os.environ.get("GMGN_VET_CAP", "8")))
    sleep_sec = max(1.0, float(os.environ.get("GMGN_VET_SLEEP_SEC", "12")))
    cool_h = float(os.environ.get("GMGN_VET_COOLDOWN_HOURS", "6"))
    priority = (os.environ.get("GMGN_VET_PRIORITY") or "elite").strip().lower()
    period = portfolio_period()
    period_7d = period == "7d"

    st = load_state()
    last_rl = parse_ts(st.get("last_rate_limit_at"))
    if last_rl and now() - last_rl < timedelta(hours=float(os.environ.get("GMGN_VET_RATE_LIMIT_COOLDOWN_HOURS", "6"))):
        cd_h=float(os.environ.get("GMGN_VET_RATE_LIMIT_COOLDOWN_HOURS", "6")); print(f"vet_gmgn_throttled: cooling after rate_limit until {last_rl + timedelta(hours=cd_h)}", flush=True)
        Path(SUMMARY).write_text(
            f"# GMGN throttled vet\n\n- skipped: post-429 cooldown (last={st.get('last_rate_limit_at')})\n",
            encoding="utf-8",
        )
        return 0

    rows = load_jsonl(watch_path)
    by = {(o.get("address") or "").lower(): dict(o) for o in rows if (o.get("address") or "").lower().startswith("0x")}

    cool_before = now() - timedelta(hours=cool_h)
    cand = []
    tagged_pending = 0
    for a, o in by.items():
        has_wr = o.get("win_rate") is not None and int(float(o.get("n_trades") or 0)) >= 10
        has_7d = o.get("win_rate_7d") is not None or o.get("realized_pnl_7d") is not None
        tags_list = [str(t) for t in (o.get("tags") or [])]
        tags_l = [t.lower() for t in tags_list]
        tags = " ".join(tags_l)
        elite = "scout_elite" in tags_l or o.get("scout_tier") == "elite"
        scoutish = (
            elite
            or "scout_tg" in tags_l
            or "scout_tg_early" in tags_l
            or "scout_good" in tags_l
            or "scout_pending_wr" in tags_l
        )
        # maintain scout_pending_WR tag for scout wallets still missing WR (30d path only)
        if not period_7d:
            if scoutish and not has_wr:
                if "scout_pending_wr" not in tags_l:
                    tags_list.append("scout_pending_WR")
                    o["tags"] = tags_list
                    by[a] = o
                    tagged_pending += 1
                    tags_l.append("scout_pending_wr")
                    tags = " ".join(tags_l)
            elif has_wr and "scout_pending_wr" in tags_l:
                o["tags"] = [t for t in tags_list if t.lower() != "scout_pending_wr"]
                by[a] = o
        # already filled for this period?
        if period_7d:
            if has_7d:
                continue
        else:
            if has_wr:
                continue
        pending_wr = "scout_pending_wr" in tags_l
        if priority in ("elite", "pending_wr"):
            if not (elite or pending_wr or scoutish):
                # still allow themaran/985 with high pnl missing wr
                if "themaran" not in tags and "985monitor" not in tags:
                    continue
            if priority == "pending_wr" and not (pending_wr or elite or scoutish):
                if "themaran" not in tags and "985monitor" not in tags:
                    continue
        ok_at = parse_ts((st.get("ok") or {}).get(a))
        fail_at = parse_ts((st.get("fail") or {}).get(a))
        if ok_at and ok_at > cool_before:
            continue
        if fail_at and fail_at > cool_before:
            continue
        elite_n = int(float(o.get("scout_elite_count") or o.get("elite_hits") or 0))
        hits = int(float(o.get("scout_hit_count") or o.get("hits") or 0))
        try:
            buy = float(o.get("scout_sum_buy_usd") or o.get("buyUsd") or 0)
        except Exception:
            buy = 0.0
        try:
            pnl = float(o.get("fomo_pnl_usd") or o.get("realized_pnl_usd") or 0)
        except Exception:
            pnl = 0.0
        if period_7d:
            # Prefer wallets that already look weak on 30d / FOMO so 7d fills
            # surface true low-profit names first (worst-first hunt).
            try:
                wr30 = float(o.get("win_rate")) if o.get("win_rate") is not None else None
            except Exception:
                wr30 = None
            # lower pnl / wr → higher priority; still nudge scout a bit
            score = (
                -min(50.0, pnl / 1e4)           # negative or small pnl first
                + (20 if wr30 is not None and wr30 < 0.4 else 0)
                + (10 if elite or scoutish else 0)
                + min(5.0, hits / 10.0)
                + (5 if o.get("realized_pnl_usd") is not None else 0)  # known track > cold
            )
        else:
            # Prefer elite + scout_pending_WR first (steady WR fill)
            score = (
                elite_n * 10
                + hits
                + (80 if elite else 0)
                + (60 if pending_wr or scoutish else 0)
                + min(20.0, buy / 200.0)
                + min(30.0, pnl / 1e5)
            )
        cand.append((score, a))
    if tagged_pending:
        print(f"vet_gmgn_throttled: tagged scout_pending_WR on {tagged_pending} wallets", flush=True)

    cand.sort(key=lambda x: -x[0])
    targets = [a for _, a in cand[:cap]]
    print(f"vet_gmgn_throttled: period={period} need={len(cand)} take={len(targets)} cap={cap} sleep={sleep_sec}s", flush=True)
    if not targets:
        Path(SUMMARY).write_text("# GMGN throttled vet\n\n- nothing to vet\n", encoding="utf-8")
        return 0

    # Monkey-patch sleep inside loop by calling one-by-one with our sleep
    vetted = {}
    calls = 0
    err = None
    ok_map = dict(st.get("ok") or {})
    fail_map = dict(st.get("fail") or {})
    for i, a in enumerate(targets):
        chunk, c, e = batch_vet(chain, [a], 1)
        calls += c
        if e == "rate_limited":
            err = e
            st["last_rate_limit_at"] = now_iso()
            print("STOP rate_limited — backing off", flush=True)
            break
        if a in chunk:
            vetted[a] = chunk[a]
            ok_map[a] = now_iso()
            fail_map.pop(a, None)
        else:
            fail_map[a] = now_iso()
            err = e or err
        if i + 1 < len(targets) and err != "rate_limited":
            time.sleep(sleep_sec)

    merged = 0
    wr_gained = 0
    for a, strow in vetted.items():
        row = by.get(a) or {"address": a}
        if period_7d:
            # write 7d fields only — never wipe 30d / lifetime
            prev_wr7 = row.get("win_rate_7d")
            if strow.get("win_rate") is not None:
                row["win_rate_7d"] = strow["win_rate"]
            if strow.get("realized_pnl_usd") is not None:
                row["realized_pnl_7d"] = strow["realized_pnl_usd"]
            if strow.get("n_trades") is not None:
                row["n_trades_7d"] = strow["n_trades"]
            if strow.get("gmgn_buy") is not None:
                row["gmgn_buy_7d"] = strow["gmgn_buy"]
            if strow.get("gmgn_sell") is not None:
                row["gmgn_sell_7d"] = strow["gmgn_sell"]
            row["vetted_7d_at"] = now_iso()
            if row.get("win_rate_7d") is not None and prev_wr7 is None:
                wr_gained += 1
            ep_tag = "gmgn:portfolio:7d"
        else:
            prev_wr = row.get("win_rate")
            for k in ("realized_pnl_usd", "unrealized_pnl_usd", "total_pnl_usd", "win_rate", "n_trades", "gmgn_buy", "gmgn_sell"):
                if strow.get(k) is not None:
                    row[k] = strow[k]
            if strow.get("win_rate") is not None and prev_wr is None:
                wr_gained += 1
            ep_tag = "gmgn:portfolio"
            row["vetted_at"] = now_iso()
        eps = list(row.get("source_endpoints") or [])
        if ep_tag not in eps:
            eps.append(ep_tag)
        row["source_endpoints"] = eps
        tags = list(row.get("tags") or [])
        if "gmgn_vetted" not in tags:
            tags.append("gmgn_vetted")
        if period_7d:
            if "gmgn_vetted_7d" not in [str(t).lower() for t in tags]:
                tags.append("gmgn_vetted_7d")
        else:
            # clear pending WR once filled (30d)
            if row.get("win_rate") is not None:
                tags = [t for t in tags if str(t).lower() != "scout_pending_wr"]
        row["tags"] = tags
        by[a] = row
        merged += 1

    write_jsonl(watch_path, list(by.values()))
    # also patch scout ranked if present
    ranked_path = ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl"
    if ranked_path.exists() and vetted:
        ranked = {(o.get("address") or "").lower(): dict(o) for o in load_jsonl(ranked_path) if (o.get("address") or "").lower().startswith("0x")}
        for a, strow in vetted.items():
            row = ranked.get(a) or {"address": a}
            if period_7d:
                if strow.get("win_rate") is not None:
                    row["win_rate_7d"] = strow["win_rate"]
                if strow.get("realized_pnl_usd") is not None:
                    row["realized_pnl_7d"] = strow["realized_pnl_usd"]
                if strow.get("n_trades") is not None:
                    row["n_trades_7d"] = strow["n_trades"]
            else:
                for k in ("realized_pnl_usd", "unrealized_pnl_usd", "total_pnl_usd", "win_rate", "n_trades"):
                    if strow.get(k) is not None:
                        row[k] = strow[k]
            ranked[a] = row
        write_jsonl(ranked_path, list(ranked.values()))

    st["ok"] = ok_map
    st["fail"] = fail_map
    st["last_run_at"] = now_iso()
    st["last_period"] = period
    st["last_calls"] = calls
    st["last_vetted"] = merged
    st["last_wr_gained"] = wr_gained
    st["last_err"] = err
    save_state(st)

    lines = [
        "# GMGN throttled vet",
        "",
        f"- Updated: **{now_iso()}**",
        f"- Period: **{period}**",
        f"- Calls: **{calls}** vetted_ok: **{merged}** wr_gained: **{wr_gained}** err: `{err}`",
        f"- Cap/sleep: {cap}/{sleep_sec}s priority={priority}",
        f"- Remaining candidates (approx): {max(0, len(cand)-len(targets))}",
        "",
        "## This run",
    ]
    for a, strow in list(vetted.items())[:20]:
        if period_7d:
            lines.append(
                f"- `{a[:10]}…` wr7={strow.get('win_rate')} n7={strow.get('n_trades')} rp7={strow.get('realized_pnl_usd')}"
            )
        else:
            lines.append(
                f"- `{a[:10]}…` wr={strow.get('win_rate')} n={strow.get('n_trades')} rp={strow.get('realized_pnl_usd')}"
            )
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"period": period, "calls": calls, "vetted": merged, "wr_gained": wr_gained, "err": err, "left": max(0, len(cand) - len(targets))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

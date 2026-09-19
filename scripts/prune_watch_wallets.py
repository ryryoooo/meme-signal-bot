#!/usr/bin/env python3
"""Prune / demote weak wallets from rh-wallets/wallets.jsonl.

Keeps scout elite / high scout_rank_score / themaran / rank_s|a unless clearly bad
(banned label, bot tags). Removes or demotes dust-only, inactive (if field exists),
consecutive-loss, and inconsistent PnL when WR is filled.

Env (PRUNE_*):
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  PRUNE_DRY_RUN=1              default 1 — write report only, no merge
  PRUNE_APPLY=0                set 1 (or PRUNE_DRY_RUN=0) to rewrite wallets.jsonl
  PRUNE_MODE=demote|remove     default demote (list_tier=demoted, pass_pnl=false)
  PRUNE_OUT=rh-wallets/raw/pruned_wallets.jsonl
  PRUNE_SUMMARY=rh-wallets/summary_prune.md
  PRUNE_DUST_MAX_BUY_USD=25    scout_sum_buy_usd / buyUsd ceiling for dust
  PRUNE_DUST_MAX_HITS=1
  PRUNE_MIN_WR=0.35            when WR filled + enough trades
  PRUNE_MIN_TRADES_FOR_WR=10
  PRUNE_MAX_NEG_PNL_USD=0      realized <= this + low WR → weak
  PRUNE_INACTIVE_DAYS=45       if last_active*/last_trade* field exists
  PRUNE_CONSEC_LOSSES=5        if consecutive_losses / loss_streak exists
  PRUNE_KEEP_SCOUT_RANK=4.0    keep if scout_rank_score >= this
  PRUNE_KEEP_ELITE=1
  PRUNE_KEEP_THEMARAN=1
  PRUNE_KEEP_RANK_SA=1
  PRUNE_EXCLUDE_BANNED=1       always act on copy/team/bot labels

Also ensures unique-resolved scout cache addresses are present (promote-all).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot as _bot  # noqa: E402

wallet_label_banned = _bot.wallet_label_banned
wallet_looks_bot = _bot.wallet_looks_bot
_wallet_winrate = _bot._wallet_winrate
_wallet_n_trades = _bot._wallet_n_trades
_wallet_realized = _bot._wallet_realized

WATCH_DEFAULT = ROOT / "rh-wallets" / "wallets.jsonl"
RAW = ROOT / "rh-wallets" / "raw"
RESOLVE_CACHE = RAW / "scout_tg_resolve_cache.jsonl"
OUT_DEFAULT = RAW / "pruned_wallets.jsonl"
SUMMARY_DEFAULT = ROOT / "rh-wallets" / "summary_prune.md"


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
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
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def tags_blob(row: dict) -> str:
    parts = []
    for key in ("tags", "gmgn_tags", "sources", "source_endpoints"):
        for x in row.get(key) or []:
            parts.append(str(x).lower())
    for key in ("address_label", "label", "scout_tier", "list_tier", "quality_reason"):
        v = row.get(key)
        if v:
            parts.append(str(v).lower())
    return " ".join(parts)


def is_protected(row: dict) -> bool:
    blob = tags_blob(row)
    if env_bool("PRUNE_KEEP_ELITE", True):
        if row.get("scout_tier") == "elite" or "scout_elite" in blob:
            return True
    if env_bool("PRUNE_KEEP_THEMARAN", True):
        if "themaran" in blob or "985monitor" in blob or "degentape" in blob:
            return True
    if env_bool("PRUNE_KEEP_RANK_SA", True):
        if "rank_s" in blob or "rank_a" in blob:
            return True
    try:
        keep_rank = float(os.environ.get("PRUNE_KEEP_SCOUT_RANK", "4.0"))
    except (TypeError, ValueError):
        keep_rank = 4.0
    try:
        srs = float(row.get("scout_rank_score") or 0)
    except (TypeError, ValueError):
        srs = 0.0
    if srs >= keep_rank:
        return True
    return False


def _parse_ts(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            # ms vs sec
            ts = float(v)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    s = str(v).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def inactivity_days(row: dict) -> float | None:
    for key in (
        "last_active_at",
        "last_activity_at",
        "last_trade_at",
        "last_seen_at",
        "last_active",
        "last_trade",
        "last_seen",
        "updated_at",
    ):
        dt = _parse_ts(row.get(key))
        if dt is not None:
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    return None


def consecutive_losses(row: dict) -> int | None:
    for key in ("consecutive_losses", "loss_streak", "n_consecutive_losses"):
        if row.get(key) is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                return None
    return None


def dust_buy_usd(row: dict) -> float | None:
    for key in ("scout_sum_buy_usd", "buyUsd", "avg_buy_usd", "avg_trade_usd"):
        if row.get(key) is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def clearly_banned(row: dict) -> bool:
    """Strict ban for prune: copy/team regex OR bot tags — not FOMO-handle substrings."""
    if wallet_label_banned(row):
        return True
    # Tag/source tokens only (avoid 'sniperontheroof' FOMO handle false positive)
    parts = []
    for key in ("tags", "gmgn_tags", "sources", "source_endpoints"):
        for x in row.get(key) or []:
            parts.append(str(x).lower())
    blob = " ".join(parts)
    bot_bits = (
        "bundler", "sniper_bot", "rat_trader", "dex_bot", "sandwich", "mev_bot",
        "phish", "scammer", "copy_bot", "copy-only", "copy_only", "team_wallet",
    )
    if any(s in blob for s in bot_bits):
        return True
    lab = str(row.get("address_label") or row.get("label") or "").lower()
    # explicit copy/team words in label (word-ish)
    if "copy-only" in lab or "copy only" in lab or lab in ("team", "copy"):
        return True
    if "copy trad" in lab or lab.startswith("team ") or " team" in lab:
        return True
    return False


def weak_reasons(row: dict) -> list[str]:
    """Return non-empty reasons if wallet should be pruned/demoted."""
    reasons: list[str] = []
    banned = False
    if env_bool("PRUNE_EXCLUDE_BANNED", True):
        if clearly_banned(row):
            reasons.append("banned_label_or_bot")
            banned = True

    # Protected keep unless clearly banned
    protected = is_protected(row)
    if protected and not banned:
        return []

    dust_max = env_float("PRUNE_DUST_MAX_BUY_USD", 25.0)
    dust_hits = env_int("PRUNE_DUST_MAX_HITS", 1)
    buy = dust_buy_usd(row)
    try:
        hits = int(row.get("scout_hit_count") or row.get("hits") or 0)
    except (TypeError, ValueError):
        hits = 0
    rp = _wallet_realized(row)
    if buy is not None and buy <= dust_max and hits <= dust_hits and abs(rp) < 1:
        # only if not already protected (handled above)
        reasons.append(f"dust_buy={buy:.1f}_hits={hits}")

    inactive = inactivity_days(row)
    max_idle = env_float("PRUNE_INACTIVE_DAYS", 45.0)
    if inactive is not None and inactive >= max_idle:
        reasons.append(f"inactive_days={inactive:.0f}")

    cl = consecutive_losses(row)
    max_cl = env_int("PRUNE_CONSEC_LOSSES", 5)
    if cl is not None and cl >= max_cl:
        reasons.append(f"consec_losses={cl}")

    wr = _wallet_winrate(row)
    nt = _wallet_n_trades(row)
    min_wr = env_float("PRUNE_MIN_WR", 0.35)
    min_nt = env_int("PRUNE_MIN_TRADES_FOR_WR", 10)
    max_neg = env_float("PRUNE_MAX_NEG_PNL_USD", 0.0)
    if wr is not None and nt >= min_nt:
        # inconsistent: low WR and non-positive PnL (high absolute winners stay)
        if wr < min_wr and rp <= max_neg:
            reasons.append(f"inconsistent_wr={wr:.2f}_pnl={rp:.0f}_n={nt}")

    return reasons


def demote_row(row: dict, reasons: list[str]) -> dict:
    out = dict(row)
    tags = list(out.get("tags") or [])
    if "pruned_weak" not in tags:
        tags.append("pruned_weak")
    out["tags"] = tags
    out["list_tier"] = "demoted"
    out["pass_pnl"] = False
    out["pruned_at"] = now_iso()
    out["prune_reasons"] = reasons
    out["quality_reason"] = "pruned_weak:" + ",".join(reasons)[:180]
    return out


def ensure_resolved_promoted(watch: dict[str, dict]) -> int:
    """Merge any uniquely-resolved scout addresses missing from watch."""
    if not RESOLVE_CACHE.exists():
        return 0
    added = 0
    now = now_iso()
    for o in load_jsonl(RESOLVE_CACHE):
        addr = (o.get("address") or "").lower()
        if not (addr.startswith("0x") and len(addr) == 42):
            continue
        if addr in watch:
            continue
        tier = (o.get("tier") or "good").lower()
        tags = ["scout_tg", "scout_tg_early"]
        if tier == "elite":
            tags.append("scout_elite")
        else:
            tags.append("scout_good")
        watch[addr] = {
            "address": addr,
            "address_label": o.get("address_label") or f"scout_{tier} [{addr[:6]}…{addr[-4:]}]",
            "realized_pnl_usd": None,
            "win_rate": None,
            "n_trades": None,
            "tags": tags,
            "gmgn_tags": list(tags),
            "sources": ["scout_tg"],
            "source_endpoints": ["telegram:scoutrobinhood"],
            "scout_tier": tier if tier in ("elite", "good") else "good",
            "scout_hit_count": 1,
            "scout_elite_count": 1 if tier == "elite" else 0,
            "scout_good_count": 0 if tier == "elite" else 1,
            "scout_sum_buy_usd": float(o.get("usd") or 0) or None,
            "scout_msg_ids": [str(o["msg_id"])] if o.get("msg_id") is not None else [],
            "scout_token_cas": [o["token_ca"]] if o.get("token_ca") else [],
            "pass_pnl": True,
            "chain": "robinhood",
            "list_tier": "quality",
            "quality_reason": "scout_tg_resolved_pending_rank",
            "collected_at": now,
            "quality_score": 0.6,
        }
        added += 1
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune weak watchlist wallets")
    ap.add_argument("--dry-run", action="store_true", help="Report only (default unless --apply)")
    ap.add_argument("--apply", action="store_true", help="Rewrite wallets.jsonl")
    ap.add_argument("--mode", choices=("demote", "remove"), default=None)
    args = ap.parse_args()

    # Default dry-run. Apply when --apply or PRUNE_APPLY=1 (and not forced --dry-run).
    dry = True
    if (args.apply or env_bool("PRUNE_APPLY", False)) and not args.dry_run:
        dry = False
    elif env_bool("PRUNE_DRY_RUN", True) is False and not args.dry_run:
        dry = False

    mode = (args.mode or os.environ.get("PRUNE_MODE") or "demote").strip().lower()
    watch_path = Path(os.environ.get("WATCHLIST_PATH") or WATCH_DEFAULT)
    out_path = Path(os.environ.get("PRUNE_OUT") or OUT_DEFAULT)
    summary_path = Path(os.environ.get("PRUNE_SUMMARY") or SUMMARY_DEFAULT)

    rows = load_jsonl(watch_path)
    before = len(rows)
    by_addr: dict[str, dict] = {}
    for r in rows:
        a = (r.get("address") or "").lower()
        if a.startswith("0x"):
            by_addr[a] = r

    promoted = ensure_resolved_promoted(by_addr)

    pruned_meta: list[dict] = []
    kept: list[dict] = []
    demoted_n = 0
    removed_n = 0

    for addr, row in by_addr.items():
        reasons = weak_reasons(row)
        if not reasons:
            kept.append(row)
            continue
        meta = {
            "address": addr,
            "address_label": row.get("address_label"),
            "reasons": reasons,
            "mode": mode,
            "scout_tier": row.get("scout_tier"),
            "scout_rank_score": row.get("scout_rank_score"),
            "tags": row.get("tags"),
            "realized_pnl_usd": row.get("realized_pnl_usd"),
            "win_rate": row.get("win_rate"),
            "n_trades": row.get("n_trades"),
            "pruned_at": now_iso(),
        }
        pruned_meta.append(meta)
        if mode == "remove":
            removed_n += 1
            # drop from kept
        else:
            kept.append(demote_row(row, reasons))
            demoted_n += 1

    after = len(kept)
    write_jsonl(out_path, pruned_meta)

    lines = [
        f"# Watchlist prune ({now_iso()})",
        "",
        f"- Watch: `{watch_path}`",
        f"- Before: **{before}** (+{promoted} promote-missing)",
        f"- After: **{after}**",
        f"- Mode: `{mode}` dry_run={dry}",
        f"- Demoted: **{demoted_n}** · Removed: **{removed_n}** · Flagged: **{len(pruned_meta)}**",
        f"- Pruned list: `{out_path}`",
        "",
        "## Knobs",
        f"- PRUNE_DUST_MAX_BUY_USD={env_float('PRUNE_DUST_MAX_BUY_USD', 25)}",
        f"- PRUNE_MIN_WR={env_float('PRUNE_MIN_WR', 0.35)} / PRUNE_MIN_TRADES_FOR_WR={env_int('PRUNE_MIN_TRADES_FOR_WR', 10)}",
        f"- PRUNE_INACTIVE_DAYS={env_float('PRUNE_INACTIVE_DAYS', 45)} (no-op if field missing)",
        f"- PRUNE_CONSEC_LOSSES={env_int('PRUNE_CONSEC_LOSSES', 5)} (no-op if field missing)",
        f"- PRUNE_KEEP_SCOUT_RANK={env_float('PRUNE_KEEP_SCOUT_RANK', 4.0)}",
        "",
        "## Sample flagged",
    ]
    for m in pruned_meta[:30]:
        lines.append(
            f"- `{m['address'][:10]}…` reasons={m['reasons']} tier={m.get('scout_tier')}"
        )
    if not pruned_meta:
        lines.append("- (none)")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not dry:
        # stable order by address
        kept_sorted = sorted(kept, key=lambda r: (r.get("address") or "").lower())
        write_jsonl(watch_path, kept_sorted)
        print(f"applied mode={mode} before={before} after={after} flagged={len(pruned_meta)}")
    else:
        print(
            f"dry_run mode={mode} before={before} after_would={after} "
            f"flagged={len(pruned_meta)} promoted_missing={promoted}"
        )

    print(f"wrote {out_path} and {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

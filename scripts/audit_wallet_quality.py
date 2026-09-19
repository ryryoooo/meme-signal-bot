#!/usr/bin/env python3
"""Fast RH wallet quality audit — classify ACTIVE / INACTIVE / 優良 / WEAK.

Designed for schedule (scout-wallet-bot / GHA), not a slow one-off analysis.
Uses existing wallets.jsonl fields first; optional free RH RPC recent-block scan
for activity. Never calls GMGN on the box.

Writes:
  rh-wallets/audit_active.jsonl
  rh-wallets/audit_inactive.jsonl
  rh-wallets/audit_quality.jsonl   # 優良
  rh-wallets/audit_weak.jsonl
  rh-wallets/summary_wallet_audit.md

Env (AUDIT_*):
  WATCHLIST_PATH=rh-wallets/wallets.jsonl
  AUDIT_TAG_WATCH=1          patch tags on wallets.jsonl (default 1)
  AUDIT_RPC=0                1 = scan recent blocks for from-addrs (default 0, fast)
  AUDIT_RPC_BLOCKS=3000      ~5min of RH chain @ ~0.1s/block
  AUDIT_RPC_CONCURRENCY=24
  RH_RPC_URL=https://rpc.mainnet.chain.robinhood.com
  AUDIT_ACTIVE_DAYS=14       last_active* window when field present
  AUDIT_MIN_WR=0.50          優良 win-rate floor
  AUDIT_MIN_PNL_USD=1000     優良 realized PnL floor
  AUDIT_MIN_TRADES=20        優良 min trades
  AUDIT_WEAK_WR=0.35         weak if WR below this (with enough trades)
  AUDIT_WEAK_MIN_TRADES=10
  AUDIT_ONESHOT_MAX_TRADES=5 # one-shot luck → weak if PnL high but tiny trades
  AUDIT_PREFER_RECENT=1      quality prefers active; still list inactive quality
  LIVE_TRADING=0             refused if somehow set to trade
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WATCH_DEFAULT = ROOT / "rh-wallets" / "wallets.jsonl"
OUT_ACTIVE = ROOT / "rh-wallets" / "audit_active.jsonl"
OUT_INACTIVE = ROOT / "rh-wallets" / "audit_inactive.jsonl"
OUT_QUALITY = ROOT / "rh-wallets" / "audit_quality.jsonl"
OUT_WEAK = ROOT / "rh-wallets" / "audit_weak.jsonl"
SUMMARY = ROOT / "rh-wallets" / "summary_wallet_audit.md"

UA = os.environ.get(
    "RH_RPC_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)
RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()


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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
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
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=None):
    x = _f(v, None)
    if x is None:
        return default
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def wr_of(o: dict) -> float | None:
    for k in ("win_rate_7d", "win_rate", "gmgn_winrate"):
        v = _f(o.get(k), None)
        if v is not None:
            # normalize percent-style
            if v > 1.5:
                v = v / 100.0
            return v
    return None


def wr_source(o: dict) -> str:
    if _f(o.get("win_rate_7d"), None) is not None:
        return "win_rate_7d"
    if _f(o.get("win_rate"), None) is not None:
        return "win_rate"
    if _f(o.get("gmgn_winrate"), None) is not None:
        return "gmgn_winrate"
    return "none"


def pnl_of(o: dict) -> float | None:
    for k in ("realized_pnl_7d", "realized_pnl_usd", "gmgn_pnl_usd", "fomo_pnl_usd", "total_pnl_usd"):
        v = _f(o.get(k), None)
        if v is not None:
            return v
    return None


def pnl_source(o: dict) -> str:
    for k in ("realized_pnl_7d", "realized_pnl_usd", "gmgn_pnl_usd", "fomo_pnl_usd", "total_pnl_usd"):
        if _f(o.get(k), None) is not None:
            return k
    return "none"


def ntrades_of(o: dict) -> int | None:
    for k in ("n_trades_7d", "n_trades"):
        v = _i(o.get(k), None)
        if v is not None:
            return v
    return None


def ntrades_source(o: dict) -> str:
    if _i(o.get("n_trades_7d"), None) is not None:
        return "n_trades_7d"
    if _i(o.get("n_trades"), None) is not None:
        return "n_trades"
    return "none"


def last_active_ts(o: dict) -> datetime | None:
    for k in (
        "last_active_at",
        "last_active",
        "last_trade_at",
        "last_trade",
        "last_active_timestamp",
        "last_tx_at",
    ):
        raw = o.get(k)
        if raw is None or raw == "":
            continue
        # unix seconds?
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
            try:
                n = float(raw)
                if n > 1e12:
                    n /= 1000.0
                return datetime.fromtimestamp(n, tz=timezone.utc)
            except Exception:
                pass
        t = parse_ts(raw)
        if t:
            return t
    return None


# --- optional RPC recent-block activity -------------------------------------

def rpc_call(url: str, method: str, params: list, timeout: float = 20.0):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "content-type": "application/json",
            "User-Agent": UA,
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode() or "{}")
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def scan_recent_senders(
    url: str,
    n_blocks: int,
    concurrency: int,
) -> tuple[set[str], dict]:
    """Return set of lowercase from-addrs seen in last n_blocks (+ meta)."""
    meta: dict = {"rpc_ok": False, "blocks_scanned": 0, "txs_seen": 0, "error": None}
    try:
        latest_hex = rpc_call(url, "eth_blockNumber", [])
        latest = int(latest_hex, 16)
    except Exception as e:
        meta["error"] = f"blockNumber:{type(e).__name__}:{e}"
        return set(), meta

    start = max(0, latest - max(1, n_blocks) + 1)
    blocks = list(range(start, latest + 1))
    senders: set[str] = set()
    scanned = 0
    txs = 0

    def fetch(bn: int):
        try:
            blk = rpc_call(url, "eth_getBlockByNumber", [hex(bn), True], timeout=25.0)
            return bn, blk, None
        except Exception as e:
            return bn, None, e

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = [ex.submit(fetch, bn) for bn in blocks]
        for fut in as_completed(futs):
            bn, blk, err = fut.result()
            if err or not isinstance(blk, dict):
                continue
            scanned += 1
            for tx in blk.get("transactions") or []:
                if not isinstance(tx, dict):
                    continue
                txs += 1
                frm = (tx.get("from") or "").lower()
                if frm.startswith("0x") and len(frm) == 42:
                    senders.add(frm)

    meta.update(
        {
            "rpc_ok": True,
            "latest_block": latest,
            "start_block": start,
            "blocks_scanned": scanned,
            "txs_seen": txs,
            "unique_senders": len(senders),
        }
    )
    return senders, meta


def classify_activity(
    o: dict,
    *,
    active_days: float,
    rpc_senders: set[str] | None,
    now: datetime,
) -> tuple[bool, str, str]:
    """Return (is_active, activity_metric, detail).

    Priority:
      1) n_trades_7d / realized_pnl_7d
      2) last_active* within window
      3) on-chain recent RPC sender set
      4) lifetime proxy: n_trades>0 or realized_pnl filled → active_lifetime
         else inactive_unvetted
    """
    addr = (o.get("address") or "").lower()
    n7 = _i(o.get("n_trades_7d"), None)
    if n7 is not None:
        if n7 > 0:
            return True, "n_trades_7d", f"n_trades_7d={n7}"
        return False, "n_trades_7d", "n_trades_7d=0"

    rp7 = _f(o.get("realized_pnl_7d"), None)
    if rp7 is not None and n7 is None:
        # period fill without trade count — treat any 7d pnl row as active signal
        return True, "realized_pnl_7d", f"realized_pnl_7d={rp7:.2f}"

    la = last_active_ts(o)
    if la is not None:
        age = (now - la).total_seconds() / 86400.0
        if age <= active_days:
            return True, "last_active", f"age_days={age:.1f}"
        return False, "last_active", f"age_days={age:.1f}"

    if rpc_senders is not None and addr in rpc_senders:
        return True, "rpc_recent_tx", "seen_in_rpc_lookback"

    # lifetime proxy (clearly labeled — 7d coverage missing)
    nt = _i(o.get("n_trades"), None)
    pnl = pnl_of(o)
    if nt is not None and nt > 0:
        return True, "lifetime_proxy", f"n_trades={nt}"
    if pnl is not None and pnl != 0:
        return True, "lifetime_proxy", f"pnl={pnl:.2f}"

    # scout-only / never vetted
    return False, "unvetted_proxy", "no_trades_no_pnl_no_7d"


def classify_quality(
    o: dict,
    *,
    min_wr: float,
    min_pnl: float,
    min_trades: int,
    weak_wr: float,
    weak_min_trades: int,
    oneshot_max: int,
    is_active: bool,
    prefer_recent: bool,
) -> tuple[str | None, str, float]:
    """Return (bucket, reason, score) where bucket in {quality, weak, None}."""
    wr = wr_of(o)
    pnl = pnl_of(o)
    nt = ntrades_of(o)
    scout_rank = _f(o.get("scout_rank_score"), 0.0) or 0.0
    scout_tier = (o.get("scout_tier") or "").lower()

    # one-shot luck
    if nt is not None and nt <= oneshot_max and pnl is not None and pnl >= min_pnl * 5:
        return "weak", f"oneshot_luck n={nt} pnl={pnl:.0f}", -abs(pnl)

    # weak: low WR with enough trades
    if wr is not None and nt is not None and nt >= weak_min_trades and wr < weak_wr:
        return "weak", f"low_wr={wr:.3f} n={nt}", wr * 100 + (pnl or 0) / 1e6

    # weak: negative / tiny pnl with enough evidence
    if pnl is not None and pnl <= 0 and nt is not None and nt >= weak_min_trades:
        return "weak", f"neg_or_zero_pnl={pnl:.0f} n={nt}", pnl

    # 優良
    if wr is not None and pnl is not None and nt is not None:
        if wr >= min_wr and pnl >= min_pnl and nt >= min_trades:
            score = wr * 1000 + min(pnl, 5e6) / 1000.0 + min(nt, 5000) / 10.0 + scout_rank * 10
            if prefer_recent and not is_active:
                score *= 0.85  # still quality, slightly demoted
            reason = f"wr={wr:.3f}({wr_source(o)}) pnl={pnl:.0f}({pnl_source(o)}) n={nt}({ntrades_source(o)})"
            if scout_tier:
                reason += f" scout={scout_tier}"
            return "quality", reason, score

    return None, "insufficient_metrics", 0.0


def slim_row(o: dict, extra: dict) -> dict:
    keys = (
        "address",
        "address_label",
        "win_rate",
        "win_rate_7d",
        "realized_pnl_usd",
        "realized_pnl_7d",
        "n_trades",
        "n_trades_7d",
        "scout_tier",
        "scout_rank_score",
        "scout_hit_count",
        "scout_elite_count",
        "list_tier",
        "quality_score",
        "tags",
        "gmgn_tags",
        "pass_pnl",
        "fomo_handle",
        "fomo_pnl_usd",
        "rank_tier",
        "rank_score",
        "collected_at",
        "ranked_at",
        "vetted_at",
    )
    out = {k: o.get(k) for k in keys if k in o}
    out.update(extra)
    return out


def tag_watchlist(path: Path, by_bucket: dict[str, set[str]]) -> int:
    """Add audit_* tags; remove stale audit_* then re-apply. Returns changed count."""
    rows = load_jsonl(path)
    changed = 0
    audit_tags = {"audit_active", "audit_inactive", "audit_quality", "audit_weak"}
    for o in rows:
        addr = (o.get("address") or "").lower()
        tags = o.get("tags")
        if not isinstance(tags, list):
            tags = list(o.get("gmgn_tags") or []) if isinstance(o.get("gmgn_tags"), list) else []
        before = list(tags)
        tags = [t for t in tags if t not in audit_tags]
        for bucket, addrs in by_bucket.items():
            if addr in addrs:
                tags.append(bucket)
        # dedupe preserve order
        seen = set()
        tags2 = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                tags2.append(t)
        if tags2 != before:
            o["tags"] = tags2
            o["audit_at"] = now_iso()
            changed += 1
        else:
            o["tags"] = tags2
    write_jsonl(path, rows)
    return changed


def fmt_addr(a: str) -> str:
    a = a or ""
    if len(a) < 12:
        return a
    return f"{a[:10]}…{a[-6:]}"


def main() -> int:
    if env_bool("LIVE_TRADING", False):
        print("audit_wallet_quality: LIVE_TRADING must stay off — refuse", flush=True)
        return 2

    t0 = time.time()
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(WATCH_DEFAULT)))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path

    min_wr = env_float("AUDIT_MIN_WR", 0.50)
    min_pnl = env_float("AUDIT_MIN_PNL_USD", 1000.0)
    min_trades = env_int("AUDIT_MIN_TRADES", 20)
    weak_wr = env_float("AUDIT_WEAK_WR", 0.35)
    weak_min_trades = env_int("AUDIT_WEAK_MIN_TRADES", 10)
    oneshot_max = env_int("AUDIT_ONESHOT_MAX_TRADES", 5)
    active_days = env_float("AUDIT_ACTIVE_DAYS", 14.0)
    prefer_recent = env_bool("AUDIT_PREFER_RECENT", True)
    do_tag = env_bool("AUDIT_TAG_WATCH", True)
    do_rpc = env_bool("AUDIT_RPC", False)
    rpc_blocks = env_int("AUDIT_RPC_BLOCKS", 3000)
    rpc_conc = env_int("AUDIT_RPC_CONCURRENCY", 24)

    rows = load_jsonl(watch_path)
    now = now_utc()

    rpc_senders: set[str] | None = None
    rpc_meta: dict = {"enabled": do_rpc}
    if do_rpc:
        print(f"audit: RPC scan blocks={rpc_blocks} conc={rpc_conc} url={RPC_URL}", flush=True)
        rpc_senders, rpc_meta = scan_recent_senders(RPC_URL, rpc_blocks, rpc_conc)
        rpc_meta["enabled"] = True
        print(
            f"audit: RPC done ok={rpc_meta.get('rpc_ok')} scanned={rpc_meta.get('blocks_scanned')} "
            f"senders={rpc_meta.get('unique_senders')} err={rpc_meta.get('error')}",
            flush=True,
        )

    # coverage
    n_total = len(rows)
    n_wr = sum(1 for o in rows if wr_of(o) is not None)
    n_pnl = sum(1 for o in rows if pnl_of(o) is not None)
    n_nt = sum(1 for o in rows if ntrades_of(o) is not None)
    n_wr7 = sum(1 for o in rows if _f(o.get("win_rate_7d"), None) is not None)
    n_nt7 = sum(1 for o in rows if _i(o.get("n_trades_7d"), None) is not None)
    n_la = sum(1 for o in rows if last_active_ts(o) is not None)

    active_rows: list[dict] = []
    inactive_rows: list[dict] = []
    quality_rows: list[dict] = []
    weak_rows: list[dict] = []
    metric_counts: dict[str, int] = {}

    by_bucket: dict[str, set[str]] = {
        "audit_active": set(),
        "audit_inactive": set(),
        "audit_quality": set(),
        "audit_weak": set(),
    }

    for o in rows:
        addr = (o.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        is_act, metric, detail = classify_activity(
            o, active_days=active_days, rpc_senders=rpc_senders, now=now
        )
        metric_counts[metric] = metric_counts.get(metric, 0) + 1
        bucket_q, reason_q, score_q = classify_quality(
            o,
            min_wr=min_wr,
            min_pnl=min_pnl,
            min_trades=min_trades,
            weak_wr=weak_wr,
            weak_min_trades=weak_min_trades,
            oneshot_max=oneshot_max,
            is_active=is_act,
            prefer_recent=prefer_recent,
        )
        extra = {
            "audit_active": is_act,
            "activity_metric": metric,
            "activity_detail": detail,
            "audit_bucket_quality": bucket_q,
            "audit_reason": reason_q if bucket_q else None,
            "audit_score": score_q if bucket_q else None,
            "wr_used": wr_of(o),
            "wr_source": wr_source(o),
            "pnl_used": pnl_of(o),
            "pnl_source": pnl_source(o),
            "n_trades_used": ntrades_of(o),
            "n_trades_source": ntrades_source(o),
            "audited_at": now_iso(),
        }
        slim = slim_row(o, extra)
        if is_act:
            active_rows.append(slim)
            by_bucket["audit_active"].add(addr)
        else:
            inactive_rows.append(slim)
            by_bucket["audit_inactive"].add(addr)
        if bucket_q == "quality":
            quality_rows.append(slim)
            by_bucket["audit_quality"].add(addr)
        elif bucket_q == "weak":
            weak_rows.append(slim)
            by_bucket["audit_weak"].add(addr)

    # sort
    active_rows.sort(
        key=lambda r: (
            -(_f(r.get("scout_rank_score"), 0) or 0),
            -(_f(r.get("pnl_used"), 0) or 0),
            -(_f(r.get("wr_used"), 0) or 0),
        )
    )
    inactive_rows.sort(
        key=lambda r: (-(_f(r.get("scout_rank_score"), 0) or 0), r.get("address") or "")
    )
    quality_rows.sort(key=lambda r: (-(_f(r.get("audit_score"), 0) or 0),))
    weak_rows.sort(key=lambda r: ((_f(r.get("wr_used"), 1) or 1), (_f(r.get("pnl_used"), 0) or 0)))

    write_jsonl(OUT_ACTIVE, active_rows)
    write_jsonl(OUT_INACTIVE, inactive_rows)
    write_jsonl(OUT_QUALITY, quality_rows)
    write_jsonl(OUT_WEAK, weak_rows)

    tagged = 0
    if do_tag:
        tagged = tag_watchlist(watch_path, by_bucket)

    elapsed = time.time() - t0
    cov_wr = 100.0 * n_wr / n_total if n_total else 0
    cov_pnl = 100.0 * n_pnl / n_total if n_total else 0
    cov_7d = 100.0 * n_wr7 / n_total if n_total else 0

    # Japanese-friendly summary
    lines: list[str] = []
    lines.append(f"# RH Wallet Audit（品質分類）")
    lines.append("")
    lines.append(f"- Updated: **{now_iso()}** (UTC) / JST+9")
    lines.append(f"- Watch: `{watch_path}` · total **{n_total}**")
    lines.append(f"- Elapsed: **{elapsed:.1f}s** · tagged_rows={tagged} · RPC={do_rpc}")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append(f"| bucket | count | 意味 |")
    lines.append(f"|---|---:|---|")
    lines.append(f"| ACTIVE | **{len(active_rows)}** | 最近取引 or lifetime proxy |")
    lines.append(f"| INACTIVE | **{len(inactive_rows)}** | 直近証拠なし / 未vet |")
    lines.append(f"| QUALITY 優良 | **{len(quality_rows)}** | WR≥{min_wr} + PnL≥{min_pnl:.0f} + n≥{min_trades} |")
    lines.append(f"| WEAK | **{len(weak_rows)}** | 低WR / 弱いPnL / ワンショット |")
    lines.append("")
    lines.append("## Data coverage（どの指標か明示）")
    lines.append("")
    lines.append(f"- `win_rate` filled: **{n_wr}/{n_total}** ({cov_wr:.1f}%)")
    lines.append(f"- `realized_pnl*` filled: **{n_pnl}/{n_total}** ({cov_pnl:.1f}%)")
    lines.append(f"- `n_trades*` filled: **{n_nt}/{n_total}**")
    lines.append(f"- `*_7d` WR filled: **{n_wr7}/{n_total}** ({cov_7d:.1f}%) ← GMGN 7d 未充足なら lifetime で判定")
    lines.append(f"- `n_trades_7d` filled: **{n_nt7}/{n_total}**")
    lines.append(f"- `last_active*` filled: **{n_la}/{n_total}**")
    lines.append(f"- activity_metric breakdown: `{json.dumps(metric_counts, ensure_ascii=False)}`")
    if do_rpc:
        lines.append(f"- RPC meta: `{json.dumps(rpc_meta, ensure_ascii=False)}`")
    lines.append("")
    lines.append("## Thresholds")
    lines.append("")
    lines.append(
        f"- 優良: WR≥{min_wr}, PnL≥${min_pnl:.0f}, trades≥{min_trades}, prefer_recent={prefer_recent}"
    )
    lines.append(
        f"- weak: WR<{weak_wr} (n≥{weak_min_trades}) / PnL≤0 / oneshot n≤{oneshot_max}"
    )
    lines.append(f"- active_days={active_days} · rpc_blocks={rpc_blocks if do_rpc else 0}")
    lines.append("")
    lines.append("## Top 10 優良 (QUALITY)")
    lines.append("")
    if not quality_rows:
        lines.append("- (none — WR/PnL coverage low; wait for GHA GMGN vet)")
    else:
        for i, r in enumerate(quality_rows[:10], 1):
            lines.append(
                f"{i}. `{r.get('address')}` wr={r.get('wr_used')} "
                f"pnl={_f(r.get('pnl_used'),0):.0f} n={r.get('n_trades_used')} "
                f"scout={r.get('scout_tier')}/{_f(r.get('scout_rank_score'),0):.2f} "
                f"active={r.get('audit_active')} metric={r.get('activity_metric')}"
            )
    lines.append("")
    lines.append("## Top 10 WEAK（prune候補）")
    lines.append("")
    if not weak_rows:
        lines.append("- (none)")
    else:
        for i, r in enumerate(weak_rows[:10], 1):
            lines.append(
                f"{i}. `{r.get('address')}` wr={r.get('wr_used')} "
                f"pnl={_f(r.get('pnl_used'),0):.0f} n={r.get('n_trades_used')} "
                f"reason={r.get('audit_reason')}"
            )
    lines.append("")
    lines.append("## Action")
    lines.append("")
    lines.append("- Keep **QUALITY** on watch / promote notify weight")
    lines.append("- Prune / demote **WEAK** + long **INACTIVE** (dry-run via prune_watch_wallets)")
    lines.append("- Fill WR gaps via GHA `gmgn-vet-throttle` (elite priority, respect 429 cool) — box GMGN off")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(f"- `{OUT_ACTIVE.relative_to(ROOT)}`")
    lines.append(f"- `{OUT_INACTIVE.relative_to(ROOT)}`")
    lines.append(f"- `{OUT_QUALITY.relative_to(ROOT)}`")
    lines.append(f"- `{OUT_WEAK.relative_to(ROOT)}`")
    lines.append("")

    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"audit done total={n_total} active={len(active_rows)} inactive={len(inactive_rows)} "
        f"quality={len(quality_rows)} weak={len(weak_rows)} "
        f"cov_wr={cov_wr:.1f}% cov_7d={cov_7d:.1f}% tagged={tagged} {elapsed:.1f}s",
        flush=True,
    )
    # machine-readable one-liner for bots
    print(
        json.dumps(
            {
                "total": n_total,
                "active": len(active_rows),
                "inactive": len(inactive_rows),
                "quality": len(quality_rows),
                "weak": len(weak_rows),
                "cov_wr_pct": round(cov_wr, 2),
                "cov_7d_pct": round(cov_7d, 2),
                "elapsed_s": round(elapsed, 2),
                "top_quality": [r.get("address") for r in quality_rows[:10]],
                "top_weak": [r.get("address") for r in weak_rows[:10]],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

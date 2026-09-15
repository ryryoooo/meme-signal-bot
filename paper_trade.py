"""Virtual paper trading ($300 bankroll). Never places real orders.

FOUNDATION risk rules:
- 1 open position at a time
- size 20% (30% if n>=3)
- +100% half-take / -40% stop
- max 5 entries / week
- 3 losses in a week → stop for the week
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

PAPER_HALF_TAKE_MULT = 2.0
PAPER_STOP_MULT = 0.60
PAPER_SIZE_PCT_DEFAULT = 20
PAPER_SIZE_PCT_STRONG = 30
PAPER_MAX_ENTRIES_WEEK = 5
PAPER_MAX_LOSSES_WEEK = 3
DEFAULT_BANKROLL_USD = 300.0
# Equity / bankroll ratios for paper-PnL Discord milestones (not signal multipliers)
PAPER_EQUITY_MILESTONES = (0.80, 0.90, 1.10, 1.25, 1.50, 2.00)


def bankroll_usd() -> float:
    try:
        return float(os.environ.get("PAPER_BANKROLL_USD") or DEFAULT_BANKROLL_USD)
    except (TypeError, ValueError):
        return DEFAULT_BANKROLL_USD


def append_paper_book(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("paper", True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _week_key(ts: float | None = None) -> str:
    """ISO week key in UTC (Mon-start)."""
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def ensure_paper_state(state: dict) -> dict:
    paper = state.get("paper") or {}
    if "cash_usd" not in paper:
        paper["cash_usd"] = bankroll_usd()
    if "equity_usd" not in paper:
        paper["equity_usd"] = float(paper.get("cash_usd") or bankroll_usd())
    paper.setdefault("realized_pnl_usd", 0.0)
    paper.setdefault("week_key", _week_key())
    paper.setdefault("week_entries", 0)
    paper.setdefault("week_losses", 0)
    paper.setdefault("week_stopped", False)
    paper.setdefault("equity_curve", [])  # [{ts, equity, cash, open_mv}]
    paper.setdefault("equity_milestones_hit", [])
    state["paper"] = paper
    if "paper_positions" not in state:
        state["paper_positions"] = []
    return paper


def _rollover_week(paper: dict) -> None:
    wk = _week_key()
    if paper.get("week_key") != wk:
        paper["week_key"] = wk
        paper["week_entries"] = 0
        paper["week_losses"] = 0
        paper["week_stopped"] = False


def active_positions(state: dict) -> list[dict]:
    return [
        p
        for p in (state.get("paper_positions") or [])
        if (p.get("status") or "") in ("open", "half_taken")
    ]


def can_open_paper(state: dict) -> tuple[bool, str]:
    paper = ensure_paper_state(state)
    _rollover_week(paper)
    if paper.get("week_stopped"):
        return False, "week_stopped_3_losses"
    if int(paper.get("week_entries") or 0) >= PAPER_MAX_ENTRIES_WEEK:
        return False, "week_max_entries"
    if active_positions(state):
        return False, "already_in_position"
    return True, "ok"


def open_paper_position(
    state: dict,
    book_path: Path,
    *,
    ca: str,
    symbol: str | None,
    entry_price: float,
    n: int,
    chain: str,
    mcap=None,
    liq=None,
    webhook: str | None = None,
    discord_post: Callable | None = None,
) -> dict | None:
    """Open virtual position. Returns None if blocked by risk rules."""
    paper = ensure_paper_state(state)
    _rollover_week(paper)
    ok, reason = can_open_paper(state)
    if not ok:
        append_paper_book(
            book_path,
            {
                "event": "open_blocked",
                "ca": ca,
                "symbol": symbol,
                "reason": reason,
                "week_entries": paper.get("week_entries"),
                "week_losses": paper.get("week_losses"),
            },
        )
        print(f"paper open blocked: {reason}")
        return None
    if not entry_price or entry_price <= 0:
        return None

    size_pct = PAPER_SIZE_PCT_STRONG if n >= 3 else PAPER_SIZE_PCT_DEFAULT
    equity = float(paper.get("equity_usd") or bankroll_usd())
    # size against bankroll baseline (not floating equity) to avoid compounding aggression
    base = bankroll_usd()
    notional = base * (size_pct / 100.0)
    cash = float(paper.get("cash_usd") or base)
    if notional > cash:
        notional = cash
    if notional <= 0:
        append_paper_book(book_path, {"event": "open_blocked", "ca": ca, "reason": "no_cash"})
        return None

    paper["cash_usd"] = cash - notional
    paper["week_entries"] = int(paper.get("week_entries") or 0) + 1

    pos = {
        "id": f"{ca[:10]}-{int(time.time())}",
        "ca": ca,
        "symbol": symbol,
        "entry_price": entry_price,
        "size_pct": size_pct,
        "notional_usd": notional,
        "remaining_usd": notional,
        "remaining_pct": size_pct,
        "opened_at": time.time(),
        "status": "open",
        "half_taken": False,
        "realized_pnl_usd": 0.0,
        "n": n,
        "chain": chain,
        "alert_mcap": mcap,
        "alert_liq": liq,
        "week_key": paper["week_key"],
    }
    positions = list(state.get("paper_positions") or [])
    positions.append(pos)
    state["paper_positions"] = positions
    state["paper"] = paper

    append_paper_book(
        book_path,
        {
            "event": "open",
            "ca": ca,
            "symbol": symbol,
            "entry_price": entry_price,
            "size_pct": size_pct,
            "notional_usd": notional,
            "n": n,
            "chain": chain,
            "cash_usd": paper["cash_usd"],
            "bankroll_usd": base,
            "week_entries": paper["week_entries"],
            "mcap": mcap,
            "liq": liq,
        },
    )
    print(
        f"paper open {ca[:10]}… size={size_pct}% notional=${notional:.2f} "
        f"cash=${paper['cash_usd']:.2f} week_entries={paper['week_entries']}"
    )
    if webhook and discord_post:
        try:
            discord_post(
                webhook,
                embeds=[
                    {
                        "title": "紙トレード・仮想エントリー",
                        "description": (
                            f"📥 ${symbol or '?'} · サイズ {size_pct}% · "
                            f"${notional:.2f} · n={n}\n"
                            f"エントリー ${entry_price:.8g} · "
                            f"週 {paper['week_entries']}/{PAPER_MAX_ENTRIES_WEEK}"
                        )[:1900],
                        "color": 0x3498DB,
                        "footer": {
                            "text": (
                                f"仮想 ${base:.0f} · "
                                f"現金 ${float(paper.get('cash_usd') or 0):.2f} · 実注文なし"
                            )
                        },
                    }
                ],
            )
        except Exception as e:
            print(f"paper entry discord fail: {type(e).__name__}", flush=True)
    return pos


def _mark_equity(state: dict, book_path: Path, now: float) -> None:
    paper = ensure_paper_state(state)
    open_mv = 0.0
    for p in active_positions(state):
        rem = float(p.get("remaining_usd") or 0)
        entry = float(p.get("entry_price") or 0)
        mark = float(p.get("last_mark_price") or entry or 0)
        if entry > 0 and mark > 0:
            open_mv += rem * (mark / entry)
        else:
            open_mv += rem
    cash = float(paper.get("cash_usd") or 0)
    equity = cash + open_mv
    paper["equity_usd"] = equity
    curve = list(paper.get("equity_curve") or [])
    # sample at most every ~15 min in curve (Actions runs */5)
    if not curve or (now - float(curve[-1].get("t") or 0)) >= 900:
        curve.append(
            {
                "t": now,
                "ts": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "equity": round(equity, 4),
                "cash": round(cash, 4),
                "open_mv": round(open_mv, 4),
            }
        )
        paper["equity_curve"] = curve[-500:]
    state["paper"] = paper


def process_paper_positions(
    state: dict,
    book_path: Path,
    chain: str,
    fetch_dex: Callable[[str, str], dict],
    webhook: str | None = None,
    discord_post: Callable | None = None,
) -> dict:
    """Update marks; apply +100% half / -40% stop. Never real orders."""
    paper = ensure_paper_state(state)
    _rollover_week(paper)
    now = time.time()
    stats = {"marked": 0, "half": 0, "stop": 0, "open": 0}
    notices: list[str] = []
    positions = list(state.get("paper_positions") or [])

    for pos in positions:
        status = pos.get("status") or "open"
        if status not in ("open", "half_taken"):
            continue
        ca = pos.get("ca")
        entry = float(pos.get("entry_price") or 0)
        if not ca or entry <= 0:
            continue
        dex = fetch_dex(ca, pos.get("chain") or chain)
        price = None
        try:
            price = float(dex.get("price_usd")) if dex.get("price_usd") is not None else None
        except (TypeError, ValueError):
            price = None
        if not price or price <= 0:
            continue
        mult = price / entry
        pos["last_mark_price"] = price
        pos["last_mark_mult"] = mult
        pos["last_mark_at"] = now
        pos["last_mcap"] = dex.get("mcap_usd") or dex.get("fdv")
        pos["last_liq"] = dex.get("liq_usd")
        stats["marked"] += 1
        rem = float(pos.get("remaining_usd") or 0)
        mark_value = rem * mult
        append_paper_book(
            book_path,
            {
                "event": "mark",
                "ca": ca,
                "symbol": pos.get("symbol"),
                "price": price,
                "mult": mult,
                "status": status,
                "remaining_usd": rem,
                "mark_value_usd": mark_value,
                "mcap": pos.get("last_mcap"),
                "liq": pos.get("last_liq"),
            },
        )

        # -40% stop (full exit of remaining)
        if mult <= PAPER_STOP_MULT:
            exit_value = rem * mult
            pnl = exit_value - rem
            paper["cash_usd"] = float(paper.get("cash_usd") or 0) + exit_value
            paper["realized_pnl_usd"] = float(paper.get("realized_pnl_usd") or 0) + pnl
            pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
            pos["status"] = "stopped"
            pos["closed_at"] = now
            pos["remaining_usd"] = 0
            pos["remaining_pct"] = 0
            paper["week_losses"] = int(paper.get("week_losses") or 0) + 1
            was_week_stopped = bool(paper.get("week_stopped"))
            if paper["week_losses"] >= PAPER_MAX_LOSSES_WEEK:
                paper["week_stopped"] = True
                if not was_week_stopped:
                    notices.append(
                        f"🛑 週停止 · 連敗 {paper['week_losses']}/{PAPER_MAX_LOSSES_WEEK} · "
                        f"今週の新規エントリー停止"
                    )
            stats["stop"] += 1
            notices.append(
                f"⛔ ストップ ${pos.get('symbol') or '?'} · {mult:.2f}倍 · PnL ${pnl:+.2f}"
            )
            append_paper_book(
                book_path,
                {
                    "event": "stop",
                    "ca": ca,
                    "symbol": pos.get("symbol"),
                    "entry_price": entry,
                    "exit_price": price,
                    "mult": mult,
                    "pnl_usd": pnl,
                    "cash_usd": paper["cash_usd"],
                    "week_losses": paper["week_losses"],
                    "week_stopped": paper["week_stopped"],
                },
            )
            print(f"paper stop {ca[:10]}… mult={mult:.2f} pnl={pnl:.2f}")
            continue

        # +100% half take
        if (not pos.get("half_taken")) and mult >= PAPER_HALF_TAKE_MULT and status == "open":
            half = rem / 2.0
            exit_value = half * mult
            # cost basis of half was `half`; pnl = exit - half
            pnl = exit_value - half
            paper["cash_usd"] = float(paper.get("cash_usd") or 0) + exit_value
            paper["realized_pnl_usd"] = float(paper.get("realized_pnl_usd") or 0) + pnl
            pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
            pos["remaining_usd"] = rem - half
            pos["remaining_pct"] = float(pos.get("size_pct") or PAPER_SIZE_PCT_DEFAULT) / 2.0
            pos["half_taken"] = True
            pos["status"] = "half_taken"
            pos["half_taken_at"] = now
            pos["half_taken_price"] = price
            stats["half"] += 1
            notices.append(
                f"💰 半分利確 ${pos.get('symbol') or '?'} · {mult:.2f}倍 · PnL ${pnl:+.2f}"
            )
            append_paper_book(
                book_path,
                {
                    "event": "half_take",
                    "ca": ca,
                    "symbol": pos.get("symbol"),
                    "entry_price": entry,
                    "exit_price": price,
                    "mult": mult,
                    "pnl_usd": pnl,
                    "remaining_usd": pos["remaining_usd"],
                    "cash_usd": paper["cash_usd"],
                },
            )
            print(f"paper half_take {ca[:10]}… mult={mult:.2f} pnl={pnl:.2f}")
            continue

        stats["open"] += 1

    state["paper_positions"] = positions
    state["paper"] = paper
    _mark_equity(state, book_path, now)
    paper = state["paper"]

    # Paper-PnL equity milestones (bankroll ratios) — not signal multiplier followups
    equity_notices = _equity_milestone_notices(paper)
    notices.extend(equity_notices)
    if equity_notices:
        stats["equity_ms"] = len(equity_notices)

    if webhook and discord_post and notices:
        discord_post(
            webhook,
            embeds=[
                {
                    "title": "紙トレード更新",
                    "description": "\n".join(notices)[:1900],
                    "color": 0x1ABC9C,
                    "footer": {
                        "text": (
                            f"仮想 ${bankroll_usd():.0f} · "
                            f"現金 ${float(paper.get('cash_usd') or 0):.2f} · "
                            f"純資産 ${float(paper.get('equity_usd') or 0):.2f} · 実注文なし"
                        )
                    },
                }
            ],
        )
    return stats


def _equity_milestone_notices(paper: dict) -> list[str]:
    """Emit paper equity / bankroll milestone lines once each."""
    br = bankroll_usd()
    if br <= 0:
        return []
    equity = float(paper.get("equity_usd") or 0)
    ratio = equity / br
    hit = list(paper.get("equity_milestones_hit") or [])
    out: list[str] = []
    for ms in PAPER_EQUITY_MILESTONES:
        if ms in hit:
            continue
        # downside: hit when ratio <= ms; upside: when ratio >= ms
        if ms < 1.0:
            if ratio > ms:
                continue
        else:
            if ratio < ms:
                continue
        hit.append(ms)
        pct = (ms - 1.0) * 100.0
        if ms < 1.0:
            out.append(f"📉 純資産マイルストーン {ms:g}x銀行 (${equity:.2f} / ${br:.0f})")
        else:
            out.append(
                f"📈 純資産マイルストーン {ms:g}x銀行 ({pct:+.0f}%) · ${equity:.2f}"
            )
    paper["equity_milestones_hit"] = hit
    return out


def write_paper_summary(
    paper_path: Path,
    state_path: Path,
    book_path: Path,
    out_path: Path,
    load_state: Callable[[Path], dict],
    milestones: tuple = (1.5, 2.0, 3.0, 5.0),
    webhook: str | None = None,
    discord_post: Callable | None = None,
) -> Path:
    posted = skipped = 0
    reasons: dict[str, int] = {}
    ms_counts = {str(float(m)): 0 for m in milestones}
    followups = 0
    if paper_path.exists():
        for line in paper_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "multiplier_followup":
                followups += 1
                ms = row.get("milestone")
                if ms is not None:
                    key = str(float(ms))
                    ms_counts[key] = ms_counts.get(key, 0) + 1
                continue
            if "posted" not in row:
                continue
            if row.get("posted"):
                posted += 1
            else:
                skipped += 1
                r = str(row.get("reason") or "unknown")
                if r.startswith("safety_fail"):
                    key = "safety"
                elif r.startswith("cooldown"):
                    key = "cooldown"
                elif r == "already_seen":
                    key = "already_seen"
                else:
                    key = r[:40]
                reasons[key] = reasons.get(key, 0) + 1

    state = load_state(state_path) if state_path.exists() else {}
    paper = ensure_paper_state(state)
    open_alerts = state.get("open_alerts") or []
    positions = state.get("paper_positions") or []

    book_counts: dict[str, int] = {}
    if book_path.exists():
        for line in book_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = str(row.get("event") or "")
            book_counts[ev] = book_counts.get(ev, 0) + 1

    pos_open = sum(1 for p in positions if (p.get("status") or "") in ("open", "half_taken"))
    pos_half = sum(1 for p in positions if p.get("half_taken"))
    pos_stop = sum(1 for p in positions if p.get("status") == "stopped")

    curve = paper.get("equity_curve") or []
    curve_lines = []
    for pt in curve[-20:]:
        curve_lines.append(
            f"- {pt.get('ts', '')}: equity=${pt.get('equity')} cash=${pt.get('cash')} open_mv=${pt.get('open_mv')}"
        )

    br = bankroll_usd()
    lines = [
        "# 紙トレード／シグナル要約",
        "",
        f"- 生成時刻(UTC): {datetime.now(timezone.utc).isoformat()}",
        f"- 仮想資金(PAPER_BANKROLL_USD): **${br:.2f}**",
        f"- 現金: **${float(paper.get('cash_usd') or 0):.2f}**",
        f"- 純資産: **${float(paper.get('equity_usd') or 0):.2f}**",
        f"- 実現PnL: **${float(paper.get('realized_pnl_usd') or 0):+.2f}**",
        f"- 週キー: `{paper.get('week_key')}` エントリー {paper.get('week_entries')}/{PAPER_MAX_ENTRIES_WEEK} "
        f"・負け {paper.get('week_losses')}/{PAPER_MAX_LOSSES_WEEK} "
        f"・週停止={bool(paper.get('week_stopped'))}",
        f"- 投稿: **{posted}** / 見送り: **{skipped}** / 倍率FU: **{followups}**",
        f"- オープン中アラート: **{len(open_alerts)}**",
        "",
        "## 見送り理由",
    ]
    if reasons:
        for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- （なし）")
    lines += ["", "## 倍率マイルストーン到達回数"]
    for m in milestones:
        lines.append(f"- {m:g}x: {ms_counts.get(str(float(m)), 0)}")
    if open_alerts:
        bits = []
        for m in milestones:
            n_hit = sum(1 for a in open_alerts if m in (a.get("milestones_hit") or []))
            bits.append(f"{m:g}x={n_hit}/{len(open_alerts)}")
        lines.append(f"- 現アラート内ヒット: {', '.join(bits)}")
    lines += [
        "",
        "## 紙ポジション（FOUNDATION）",
        f"- 同時1本 / サイズ {PAPER_SIZE_PCT_DEFAULT}%（n≥3→{PAPER_SIZE_PCT_STRONG}%）",
        f"- +100%半分 / −40%ストップ / 週最大{PAPER_MAX_ENTRIES_WEEK} / 連敗{PAPER_MAX_LOSSES_WEEK}で週終了",
        f"- 帳簿: " + ", ".join(f"{k}={v}" for k, v in sorted(book_counts.items())),
        f"- 状態 open系={pos_open} / 半分利確済={pos_half} / ストップ={pos_stop}",
        "",
        "## エクイティ曲線（直近）",
    ]
    if curve_lines:
        lines.extend(curve_lines)
    else:
        lines.append("- （まだサンプルなし）")
    lines += ["", "## 注意", "- 実注文なし（LIVE_TRADING ブロック）。GitHub Actions 上のみ更新。", ""]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"paper_summary written {out_path} equity={paper.get('equity_usd')}")
    if webhook and discord_post:
        try:
            discord_post(
                webhook,
                embeds=[
                    {
                        "title": "紙トレード週次サマリー",
                        "description": (
                            f"純資産 **${float(paper.get('equity_usd') or 0):.2f}** · "
                            f"現金 **${float(paper.get('cash_usd') or 0):.2f}** · "
                            f"実現PnL **${float(paper.get('realized_pnl_usd') or 0):+.2f}**\n"
                            f"週 `{paper.get('week_key')}` エントリー "
                            f"{paper.get('week_entries')}/{PAPER_MAX_ENTRIES_WEEK} · "
                            f"負け {paper.get('week_losses')}/{PAPER_MAX_LOSSES_WEEK} · "
                            f"週停止={bool(paper.get('week_stopped'))}\n"
                            f"open系={pos_open} / 半分={pos_half} / ストップ={pos_stop} · 実注文なし"
                        )[:1900],
                        "color": 0x9B59B6,
                        "footer": {"text": f"詳細: {out_path.name}"},
                    }
                ],
            )
            print("paper_summary discord ping ok")
        except Exception as e:
            print(f"paper_summary discord fail: {type(e).__name__}", flush=True)
    return out_path

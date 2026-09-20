"""Virtual paper trading ($300 bankroll). Never places real orders.

Risk rules (env-overridable; 0 = unlimited for caps):
- concurrent opens: PAPER_MAX_OPEN (default 5; Sol tick uses 3–4)
- size: PAPER_SIZE_PCT_DEFAULT / PAPER_SIZE_PCT_STRONG (Sol aggressive: 30 / 40)
- Aggressive moonbag exits:
  TP1 PAPER_TP1_MULT / PAPER_TP1_SELL_PCT (default 1.25 / 0.50 of original)
  TP2 PAPER_TP2_MULT → leave PAPER_MOONBAG_PCT (default 1.60 / 0.15)
  main stop PAPER_STOP_MULT before moonbag (default 0.50 = -50%)
  moonbag catastrophic PAPER_MOON_STOP_MULT (default 0.25 = -75%; 0=off)
- legacy half_taken (+100% sold 50%) migrates as TP1-done → TP2 trims to moonbag
- weekly entry / loss caps off by default
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable

# Legacy alias; exits use tp1_mult()/stop_mult() env knobs
PAPER_HALF_TAKE_MULT = 2.0
PAPER_STOP_MULT = 0.50  # aggressive default (-50%)
PAPER_SIZE_PCT_DEFAULT = 30  # aggressive; env override
PAPER_SIZE_PCT_STRONG = 40
DEFAULT_BANKROLL_USD = 300.0
ACTIVE_STATUSES = ("open", "half_taken", "tp1_taken", "moonbag")


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def size_pct_default() -> int:
    return max(1, int(_env_float("PAPER_SIZE_PCT_DEFAULT", PAPER_SIZE_PCT_DEFAULT)))


def size_pct_strong() -> int:
    return max(size_pct_default(), int(_env_float("PAPER_SIZE_PCT_STRONG", PAPER_SIZE_PCT_STRONG)))


def tp1_mult() -> float:
    return max(1.01, _env_float("PAPER_TP1_MULT", 1.25))


def tp1_sell_pct() -> float:
    """Fraction of *original* notional sold at TP1."""
    return min(0.95, max(0.05, _env_float("PAPER_TP1_SELL_PCT", 0.50)))


def tp2_mult() -> float:
    return max(tp1_mult(), _env_float("PAPER_TP2_MULT", 1.60))


def moonbag_pct() -> float:
    return min(0.50, max(0.01, _env_float("PAPER_MOONBAG_PCT", 0.15)))


def moon_stop_mult() -> float:
    """0 = disabled. Default 0.25 ≈ -75% on moonbag only."""
    return max(0.0, _env_float("PAPER_MOON_STOP_MULT", 0.25))


def stop_mult() -> float:
    """Main stop before moonbag. Default 0.50 = -50% (aggressive)."""
    return max(0.01, min(0.99, _env_float("PAPER_STOP_MULT", PAPER_STOP_MULT)))


def max_entries_week() -> int:
    """0 = unlimited."""
    return max(0, _env_int("PAPER_MAX_ENTRIES_WEEK", 0))


def max_losses_week() -> int:
    """0 = unlimited (no week stop)."""
    return max(0, _env_int("PAPER_MAX_LOSSES_WEEK", 0))


def max_open_positions() -> int:
    """Max concurrent open/tp1/moonbag positions. 0 = unlimited."""
    return max(0, _env_int("PAPER_MAX_OPEN", 5))


# Back-compat names used in f-strings / summaries (resolved at call time where possible)
PAPER_MAX_ENTRIES_WEEK = max_entries_week()
PAPER_MAX_LOSSES_WEEK = max_losses_week()
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
        if (p.get("status") or "") in ACTIVE_STATUSES
    ]


def can_open_paper(state: dict, chain: str | None = None) -> tuple[bool, str]:
    paper = ensure_paper_state(state)
    _rollover_week(paper)
    max_loss = max_losses_week()
    if max_loss > 0 and paper.get("week_stopped"):
        return False, "week_stopped_losses"
    max_ent = max_entries_week()
    if max_ent > 0 and int(paper.get("week_entries") or 0) >= max_ent:
        return False, "week_max_entries"
    active = active_positions(state)
    max_open = max_open_positions()
    if max_open > 0 and len(active) >= max_open:
        return False, "max_open_positions"
    # optional legacy: one open per chain
    one_per = str(os.environ.get("PAPER_ONE_PER_CHAIN") or "0").strip().lower() in ("1", "true", "yes")
    if one_per and chain and active:
        ch = (chain or "").lower()
        if any((p.get("chain") or "").lower() == ch for p in active):
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
    ok, reason = can_open_paper(state, chain=chain)
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

    size_pct = size_pct_strong() if n >= 3 else size_pct_default()
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
        "tp1_taken": False,
        "tp2_taken": False,
        "moonbag": False,
        "legacy_half": False,
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
                            f"週 {paper['week_entries']}/{'∞' if max_entries_week()<=0 else max_entries_week()}"
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
    """Update marks; aggressive moonbag TP1/TP2 + stops. Never real orders.

    Legacy half_taken (old +100% / 50% sold) ⇒ TP1 done; TP2 trims to moonbag %.
    """
    paper = ensure_paper_state(state)
    _rollover_week(paper)
    now = time.time()
    stats = {
        "marked": 0,
        "half": 0,
        "tp1": 0,
        "tp2": 0,
        "stop": 0,
        "moon_stop": 0,
        "open": 0,
        "migrated": 0,
    }
    notices: list[str] = []
    positions = list(state.get("paper_positions") or [])

    t1m = tp1_mult()
    t1s = tp1_sell_pct()
    t2m = tp2_mult()
    moon_pct = moonbag_pct()
    main_stop = stop_mult()
    m_stop = moon_stop_mult()

    def _sell(pos: dict, sell_cost: float, price: float, mult: float, *, event: str, label: str) -> float:
        rem = float(pos.get("remaining_usd") or 0)
        sell_cost = min(max(0.0, sell_cost), rem)
        if sell_cost <= 0:
            return 0.0
        exit_value = sell_cost * mult
        pnl = exit_value - sell_cost
        paper["cash_usd"] = float(paper.get("cash_usd") or 0) + exit_value
        paper["realized_pnl_usd"] = float(paper.get("realized_pnl_usd") or 0) + pnl
        pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
        pos["remaining_usd"] = rem - sell_cost
        notion = float(pos.get("notional_usd") or rem or 1.0)
        size_pct = float(pos.get("size_pct") or size_pct_default())
        pos["remaining_pct"] = size_pct * (float(pos["remaining_usd"]) / notion) if notion else 0.0
        append_paper_book(
            book_path,
            {
                "event": event,
                "ca": pos.get("ca"),
                "symbol": pos.get("symbol"),
                "entry_price": pos.get("entry_price"),
                "exit_price": price,
                "mult": mult,
                "pnl_usd": pnl,
                "sold_usd": sell_cost,
                "remaining_usd": pos["remaining_usd"],
                "cash_usd": paper["cash_usd"],
                "status": pos.get("status"),
            },
        )
        notices.append(
            f"{label} ${pos.get('symbol') or '?'} · {mult:.2f}倍 · "
            f"PnL ${pnl:+.2f} · 残 ${pos['remaining_usd']:.2f}"
        )
        print(
            f"paper {event} {str(pos.get('ca') or '')[:10]}… mult={mult:.2f} "
            f"pnl={pnl:.2f} rem={pos['remaining_usd']:.2f}"
        )
        return pnl

    def _full_exit(
        pos: dict,
        price: float,
        mult: float,
        *,
        event: str,
        label: str,
        count_week_loss: bool,
    ) -> None:
        rem = float(pos.get("remaining_usd") or 0)
        _sell(pos, rem, price, mult, event=event, label=label)
        pos["status"] = "stopped"
        pos["closed_at"] = now
        pos["remaining_usd"] = 0.0
        pos["remaining_pct"] = 0.0
        pos["moonbag"] = False
        if count_week_loss:
            paper["week_losses"] = int(paper.get("week_losses") or 0) + 1
            was = bool(paper.get("week_stopped"))
            if max_losses_week() > 0 and paper["week_losses"] >= max_losses_week():
                paper["week_stopped"] = True
                if not was:
                    notices.append(
                        f"🛑 週停止 · 連敗 {paper['week_losses']}/{max_losses_week()} · "
                        f"今週の新規エントリー停止"
                    )

    for pos in positions:
        status = pos.get("status") or "open"
        if status not in ACTIVE_STATUSES:
            continue
        ca = pos.get("ca")
        entry = float(pos.get("entry_price") or 0)
        if not ca or entry <= 0:
            continue

        # Migrate legacy half_taken (old +100% half) → TP1 done
        if status == "half_taken" and not pos.get("tp1_taken") and not pos.get("moonbag"):
            pos["tp1_taken"] = True
            pos["half_taken"] = True
            pos["legacy_half"] = True
            pos.setdefault("tp1_sell_pct", 0.50)
            stats["migrated"] += 1
            append_paper_book(
                book_path,
                {
                    "event": "migrate_legacy_half",
                    "ca": ca,
                    "symbol": pos.get("symbol"),
                    "note": "old +100% half → TP1 done; await TP2 trim to moonbag",
                    "remaining_usd": pos.get("remaining_usd"),
                    "notional_usd": pos.get("notional_usd"),
                },
            )

        dex = fetch_dex(ca, pos.get("chain") or chain)
        price = None
        try:
            if dex.get("price_usd") is not None:
                price = float(dex.get("price_usd"))
        except (TypeError, ValueError):
            price = None
        if not price or price <= 0:
            # Fall back to last mark so TP/stop still apply when Dex flaps
            try:
                price = float(pos.get("last_mark_price") or 0)
            except (TypeError, ValueError):
                price = 0.0
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
        notion = float(pos.get("notional_usd") or rem or 0)
        append_paper_book(
            book_path,
            {
                "event": "mark",
                "ca": ca,
                "symbol": pos.get("symbol"),
                "price": price,
                "mult": mult,
                "status": pos.get("status"),
                "remaining_usd": rem,
                "mark_value_usd": rem * mult,
                "mcap": pos.get("last_mcap"),
                "liq": pos.get("last_liq"),
            },
        )

        is_moon = bool(pos.get("moonbag") or status == "moonbag")

        if is_moon:
            if m_stop > 0 and mult <= m_stop:
                _full_exit(
                    pos,
                    price,
                    mult,
                    event="moon_stop",
                    label="☄️ ムーン袋ストップ",
                    count_week_loss=True,
                )
                stats["moon_stop"] += 1
                stats["stop"] += 1
            else:
                stats["open"] += 1
            continue

        if mult <= main_stop:
            _full_exit(
                pos,
                price,
                mult,
                event="stop",
                label="⛔ ストップ",
                count_week_loss=True,
            )
            stats["stop"] += 1
            continue

        if (
            (not pos.get("tp1_taken"))
            and (not pos.get("half_taken"))
            and mult >= t1m
            and status == "open"
        ):
            sell = (notion * t1s) if notion > 0 else rem * t1s
            _sell(
                pos,
                sell,
                price,
                mult,
                event="tp1",
                label=f"🎯 TP1(+{(t1m - 1) * 100:.0f}%/{t1s * 100:.0f}%)",
            )
            pos["tp1_taken"] = True
            pos["half_taken"] = True
            pos["status"] = "tp1_taken"
            pos["tp1_at"] = now
            pos["tp1_price"] = price
            pos["half_taken_at"] = now
            pos["half_taken_price"] = price
            stats["tp1"] += 1
            stats["half"] += 1
            continue

        tp1_done = bool(
            pos.get("tp1_taken")
            or pos.get("half_taken")
            or status in ("half_taken", "tp1_taken")
        )
        if tp1_done and (not pos.get("tp2_taken")) and (not is_moon) and mult >= t2m:
            target = (notion * moon_pct) if notion > 0 else rem * moon_pct
            sell = max(0.0, rem - target)
            if sell > 1e-9:
                _sell(
                    pos,
                    sell,
                    price,
                    mult,
                    event="tp2",
                    label=f"🚀 TP2(+{(t2m - 1) * 100:.0f}%→ムーン{moon_pct * 100:.0f}%)",
                )
            pos["tp2_taken"] = True
            pos["moonbag"] = True
            pos["status"] = "moonbag"
            pos["tp2_at"] = now
            pos["tp2_price"] = price
            pos["moonbag_at"] = now
            stats["tp2"] += 1
            stats["half"] += 1
            continue

        stats["open"] += 1

    state["paper_positions"] = positions
    state["paper"] = paper
    _mark_equity(state, book_path, now)
    paper = state["paper"]

    equity_notices = _equity_milestone_notices(paper)
    notices.extend(equity_notices)
    if equity_notices:
        stats["equity_ms"] = len(equity_notices)

    heartbeat_on = str((os.environ.get("PAPER_MARK_HEARTBEAT") or "1")).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if webhook and discord_post and heartbeat_on and stats["marked"]:
        lines = []
        for pos in positions:
            st = pos.get("status") or "open"
            if st not in ACTIVE_STATUSES:
                continue
            if not pos.get("last_mark_mult"):
                continue
            mult = float(pos["last_mark_mult"])
            rem = float(pos.get("remaining_usd") or 0)
            u_pnl = rem * mult - rem
            mcap = pos.get("last_mcap")
            mcap_s = f"${mcap:,.0f}" if isinstance(mcap, (int, float)) else "-"
            if st == "moonbag" or pos.get("moonbag"):
                flag = "ムーン袋"
            elif st in ("tp1_taken", "half_taken") or pos.get("tp1_taken"):
                flag = "TP1後"
            else:
                flag = "オープン"
            lines.append(
                f"· `${pos.get('symbol') or '?'}` {mult:.2f}x · uPnL ${u_pnl:+.2f} · "
                f"残 ${rem:.0f} · mcap {mcap_s} · {flag}"
            )
        if lines:
            notices.append("📊 値動きマーク\n" + "\n".join(lines))

    if webhook and discord_post and notices:
        discord_post(
            webhook,
            embeds=[
                {
                    "title": "紙トレード更新（攻撃的ムーンバッグ）",
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

    pos_open = sum(1 for p in positions if (p.get("status") or "") in ACTIVE_STATUSES)
    pos_half = sum(1 for p in positions if p.get("half_taken") or p.get("tp1_taken") or p.get("moonbag"))
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
        f"- 週キー: `{paper.get('week_key')}` エントリー {paper.get('week_entries')}/{'∞' if max_entries_week()<=0 else max_entries_week()} "
        f"・負け {paper.get('week_losses')}/{'∞' if max_losses_week()<=0 else max_losses_week()} "
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
        f"- 同時最大{max_open_positions() or '∞'}本 / サイズ {size_pct_default()}%（n≥3→{size_pct_strong()}%）",
        f"- 攻撃的ムーンバッグ / TP1+25%/50%·TP2+60%→15%·Stop-50% / 週エントリー{'無制限' if max_entries_week()<=0 else max_entries_week()} / 連敗停止{'なし' if max_losses_week()<=0 else max_losses_week()}",
        f"- 帳簿: " + ", ".join(f"{k}={v}" for k, v in sorted(book_counts.items())),
        f"- 状態 open系={pos_open} / TP1orムーン={pos_half} / ストップ={pos_stop}",
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
                            f"{paper.get('week_entries')}/{'∞' if max_entries_week()<=0 else max_entries_week()} · "
                            f"負け {paper.get('week_losses')}/{'∞' if max_losses_week()<=0 else max_losses_week()} · "
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

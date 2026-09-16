"""Arc-only LIVE auto-trading. Same risk rules as paper; real swaps via live_exec (Uniswap V4).

Risk (LIVE_* env; 0 = unlimited for caps):
- concurrent opens: LIVE_MAX_OPEN (default 5)
- size 20% (30% if n>=3) of LIVE_BANKROLL_USD, capped by on-chain USDC
- +100% half-take / -40% stop (processor-side; arc has no condition-orders)
- weekly entry / loss caps off by default
- LIVE_ONE_PER_CHAIN=0 default
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import live_exec

LIVE_HALF_TAKE_MULT = 2.0
LIVE_STOP_MULT = 0.60
LIVE_SIZE_PCT_DEFAULT = 20
LIVE_SIZE_PCT_STRONG = 30
DEFAULT_BANKROLL_USD = 80.0
DEFAULT_STATE = "live_state_arc.json"
DEFAULT_BOOK = "live_book_arc.jsonl"


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def max_entries_week() -> int:
    return max(0, _env_int("LIVE_MAX_ENTRIES_WEEK", 0))


def max_losses_week() -> int:
    return max(0, _env_int("LIVE_MAX_LOSSES_WEEK", 0))


def max_open_positions() -> int:
    return max(0, _env_int("LIVE_MAX_OPEN", 5))


def bankroll_usd() -> float:
    try:
        return float(os.environ.get("LIVE_BANKROLL_USD") or DEFAULT_BANKROLL_USD)
    except (TypeError, ValueError):
        return DEFAULT_BANKROLL_USD


def exit_grace_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("LIVE_EXIT_GRACE_SEC") or "15"))
    except (TypeError, ValueError):
        return 15.0


def stop_confirm_needed() -> int:
    return max(1, _env_int("LIVE_STOP_CONFIRM", 1))


def fill_price_usd_from_swap(swap: dict | None) -> float | None:
    """USD per token from buy swap amounts. Prefer amountInSpent over amountIn."""
    if not isinstance(swap, dict):
        return None
    raw_in = swap.get("amountInSpent")
    if raw_in is None:
        raw_in = swap.get("amountIn")
    raw_out = swap.get("amountOutReceived")
    if raw_in is None or raw_out is None:
        return None
    try:
        ain = float(raw_in)
        aout = float(raw_out)
    except (TypeError, ValueError):
        return None
    if ain <= 0 or aout <= 0:
        return None
    # Native wei-looking amounts are 18-dec; ERC20 USDC is 6-dec
    usd_in = ain / 1e18 if ain >= 1e15 else ain / 1e6
    try:
        dec = int(swap.get("decimals") if swap.get("decimals") is not None else (
            swap.get("tokenDecimals") if swap.get("tokenDecimals") is not None else 18
        ))
    except (TypeError, ValueError):
        dec = 18
    tokens_out = aout / (10 ** dec)
    if tokens_out <= 0:
        return None
    fill = usd_in / tokens_out
    return fill if fill > 0 else None


def state_path(root: Path | None = None) -> Path:
    raw = os.environ.get("LIVE_STATE_PATH") or DEFAULT_STATE
    p = Path(raw)
    if not p.is_absolute() and root is not None:
        return (root / p).resolve()
    return p.resolve()


def book_path(root: Path | None = None) -> Path:
    raw = os.environ.get("LIVE_BOOK_PATH") or DEFAULT_BOOK
    p = Path(raw)
    if not p.is_absolute() and root is not None:
        return (root / p).resolve()
    return p.resolve()


def load_live_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_live_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_live_book(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("live", True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _week_key(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def ensure_live_state(state: dict) -> dict:
    live = state.get("live") or {}
    if "cash_usd" not in live:
        live["cash_usd"] = bankroll_usd()
    if "equity_usd" not in live:
        live["equity_usd"] = float(live.get("cash_usd") or bankroll_usd())
    live.setdefault("realized_pnl_usd", 0.0)
    live.setdefault("week_key", _week_key())
    live.setdefault("week_entries", 0)
    live.setdefault("week_losses", 0)
    live.setdefault("week_stopped", False)
    state["live"] = live
    if "live_positions" not in state:
        state["live_positions"] = []
    return live


def _rollover_week(live: dict) -> None:
    wk = _week_key()
    if live.get("week_key") != wk:
        live["week_key"] = wk
        live["week_entries"] = 0
        live["week_losses"] = 0
        live["week_stopped"] = False


def active_positions(state: dict) -> list[dict]:
    return [
        p
        for p in (state.get("live_positions") or [])
        if (p.get("status") or "") in ("open", "half_taken")
    ]


def can_open_live(state: dict, chain: str | None = None) -> tuple[bool, str]:
    live = ensure_live_state(state)
    _rollover_week(live)
    max_loss = max_losses_week()
    if max_loss > 0 and live.get("week_stopped"):
        return False, "week_stopped_losses"
    max_ent = max_entries_week()
    if max_ent > 0 and int(live.get("week_entries") or 0) >= max_ent:
        return False, "week_max_entries"
    active = active_positions(state)
    max_open = max_open_positions()
    if max_open > 0 and len(active) >= max_open:
        return False, "max_open_positions"
    one_per = str(os.environ.get("LIVE_ONE_PER_CHAIN") or "0").strip().lower() in ("1", "true", "yes")
    if one_per and chain and active:
        ch = (chain or "").lower()
        if any((p.get("chain") or "").lower() == ch for p in active):
            return False, "already_in_position"
    # Arc-only hard gate
    ch = (chain or "").lower()
    if ch and ch not in ("arc",):
        return False, "chain_not_arc"
    return True, "ok"


def _size_notional_usd(n: int) -> tuple[float, int, float | None]:
    """Return (notional, size_pct, onchain_usdc_or_None)."""
    size_pct = LIVE_SIZE_PCT_STRONG if n >= 3 else LIVE_SIZE_PCT_DEFAULT
    base = bankroll_usd()
    notional = base * (size_pct / 100.0)
    onchain, _err = live_exec.fetch_usdc_balance_usd("arc")
    if onchain is not None and onchain >= 0:
        notional = min(notional, onchain)
    return notional, size_pct, onchain


def _fmt_money(x: float | None) -> str:
    """Signed money: +$1.23 / -$1.23."""
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _portfolio_lines(state: dict) -> str:
    live = ensure_live_state(state)
    opens = active_positions(state)
    realized = float(live.get("realized_pnl_usd") or 0)
    open_cost = sum(float(p.get("remaining_usd") or 0) for p in opens)
    parts = [
        f"実現PnL **{_fmt_money(realized)}**",
        f"オープン **{len(opens)}/{max_open_positions() or '∞'}**",
        f"拘束中 ~${open_cost:,.0f}",
    ]
    return " · ".join(parts)


def _notify(
    webhook: str | None,
    discord_post: Callable | None,
    *,
    title: str,
    description: str,
    color: int = 0xE67E22,
    fields: list[dict] | None = None,
    state: dict | None = None,
) -> None:
    if not webhook or not discord_post:
        return
    try:
        desc = description[:1800]
        if state is not None:
            desc = (desc + "\n\n" + _portfolio_lines(state)).strip()[:1900]
        embed: dict = {
            "title": title,
            "description": desc,
            "color": color,
            "footer": {"text": "⚡ Arc 実弾 · 自動売買"},
        }
        if fields:
            embed["fields"] = fields[:8]
        discord_post(webhook, embeds=[embed])
    except Exception as e:
        print(f"live discord fail: {type(e).__name__}", flush=True)


def open_live_position(
    state: dict,
    book: Path,
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
    danger_ok: bool = True,
    danger_reasons: list[str] | None = None,
) -> dict | None:
    """Open live Arc position via USDC→token swap. Soft-fail on swap errors."""
    live = ensure_live_state(state)
    _rollover_week(live)
    chain = (chain or "").strip().lower()

    if not danger_ok:
        reasons = danger_reasons or ["danger"]
        append_live_book(
            book,
            {
                "event": "open_blocked",
                "ca": ca,
                "symbol": symbol,
                "reason": "gmgn_danger",
                "details": reasons[:12],
            },
        )
        print(f"live open blocked: gmgn_danger {reasons[:3]}", flush=True)
        _notify(
            webhook,
            discord_post,
            title="⚠️ 実弾見送り（危険🚫）",
            description=f"${symbol or '?'} · `{ca[:12]}…`\n" + ", ".join(reasons[:6]),
            color=0xE67E22,
        )
        return None

    ok, reason = can_open_live(state, chain=chain)
    if not ok:
        append_live_book(
            book,
            {
                "event": "open_blocked",
                "ca": ca,
                "symbol": symbol,
                "reason": reason,
                "week_entries": live.get("week_entries"),
                "week_losses": live.get("week_losses"),
            },
        )
        print(f"live open blocked: {reason}", flush=True)
        return None
    if not entry_price or entry_price <= 0:
        return None

    notional, size_pct, onchain = _size_notional_usd(n)
    if notional <= 0.5:
        append_live_book(book, {"event": "open_blocked", "ca": ca, "reason": "no_usdc", "onchain": onchain})
        print("live open blocked: no_usdc", flush=True)
        _notify(
            webhook,
            discord_post,
            title="⚠️ 実弾見送り（USDC不足）",
            description=f"${symbol or '?'} · bankroll=${bankroll_usd():.0f} · onchain={onchain}",
            color=0xE67E22,
        )
        return None

    data, err = live_exec.swap_buy_usdc_to_token(chain, ca, notional)
    if err or data is None:
        append_live_book(
            book,
            {
                "event": "open_failed",
                "ca": ca,
                "symbol": symbol,
                "reason": err or "swap_fail",
                "notional_usd": notional,
                "swap": live_exec.summarize_swap_result(data),
            },
        )
        print(f"live open swap fail: {err}", flush=True)
        _notify(
            webhook,
            discord_post,
            title="⚠️ 実弾エラー",
            description=(
                f"エントリー失敗 · ${symbol or '?'} · ${notional:.2f} USDC → token\n"
                f"reason=`{err}` · シグナルジョブは継続"
            ),
            color=0xE67E22,
        )
        return None

    live["week_entries"] = int(live.get("week_entries") or 0) + 1
    # Approximate cash drawdown vs bankroll tracking (not true wallet sync)
    cash = float(live.get("cash_usd") or bankroll_usd())
    live["cash_usd"] = max(0.0, cash - notional)

    swap_sum = live_exec.summarize_swap_result(data)
    alert_price = float(entry_price)
    fill = fill_price_usd_from_swap(swap_sum) or fill_price_usd_from_swap(data)
    # Prefer fill; if missing, keep any fresh price already passed as entry_price (caller refetch).
    use_entry = fill if (fill is not None and fill > 0) else alert_price

    pos = {
        "id": f"live-{ca[:10]}-{int(time.time())}",
        "ca": ca,
        "symbol": symbol,
        "entry_price": use_entry,
        "alert_price_usd": alert_price,
        "alert_entry_price": alert_price,
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
        "week_key": live["week_key"],
        "swap_open": swap_sum,
        "onchain_usdc_at_open": onchain,
        "stop_confirm_count": 0,
        "entry_from_fill": bool(fill and fill > 0),
    }
    positions = list(state.get("live_positions") or [])
    positions.append(pos)
    state["live_positions"] = positions
    state["live"] = live

    append_live_book(
        book,
        {
            "event": "open",
            "ca": ca,
            "symbol": symbol,
            "entry_price": use_entry,
            "alert_price_usd": alert_price,
            "fill_price_usd": fill,
            "size_pct": size_pct,
            "notional_usd": notional,
            "n": n,
            "chain": chain,
            "cash_usd": live["cash_usd"],
            "bankroll_usd": bankroll_usd(),
            "onchain_usdc": onchain,
            "week_entries": live["week_entries"],
            "swap": pos["swap_open"],
            "mcap": mcap,
            "liq": liq,
        },
    )
    print(
        f"live open {ca[:10]}… size={size_pct}% notional=${notional:.2f} "
        f"entry={use_entry:.8g} alert={alert_price:.8g} fill={fill} "
        f"week_entries={live['week_entries']}",
        flush=True,
    )
    gmgn = f"https://gmgn.ai/arc/token/{ca}"
    _notify(
        webhook,
        discord_post,
        title=f"🟢 実弾エントリー · ${symbol or '?'}",
        description=(
            f"**買った** · サイズ {size_pct}% · **${notional:.2f}**\n"
            f"監視財布 n={n} · 約定入口 ${use_entry:.8g}"
            + (f" (alert ${alert_price:.8g})" if abs(use_entry - alert_price) / max(alert_price, 1e-18) > 0.01 else "")
            + f"\n[GMGN]({gmgn}) · `{ca}`"
        ),
        color=0x2ECC71,
        fields=[
            {"name": "ルール", "value": "+100%で半分 / −40%で全損切", "inline": False},
            {
                "name": "USDC",
                "value": f"onchain={onchain if onchain is not None else '—'} · bankroll=${bankroll_usd():.0f}",
                "inline": False,
            },
        ],
        state=state,
    )
    return pos



def _close_already_flat(
    state: dict,
    book: Path,
    live: dict,
    pos: dict,
    *,
    now: float,
    price: float,
    entry: float,
    mult: float,
    rem: float,
    reason: str,
    webhook: str | None,
    discord_post: Callable | None,
) -> None:
    """Token already gone on-chain — book flat, stop retry spam."""
    ca = pos.get("ca")
    exit_value = rem * mult if rem and mult else 0.0
    pnl = exit_value - rem
    live["cash_usd"] = float(live.get("cash_usd") or 0) + exit_value
    live["realized_pnl_usd"] = float(live.get("realized_pnl_usd") or 0) + pnl
    pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
    pos["status"] = "closed_dust" if abs(pnl) < 0.5 else "stopped"
    pos["closed_at"] = now
    pos["remaining_usd"] = 0
    pos["remaining_pct"] = 0
    pos["flat_reason"] = reason
    if pos["status"] == "stopped":
        live["week_losses"] = int(live.get("week_losses") or 0) + 1
        if max_losses_week() > 0 and live["week_losses"] >= max_losses_week():
            live["week_stopped"] = True
    append_live_book(
        book,
        {
            "event": "closed_dust" if pos["status"] == "closed_dust" else "stop",
            "ca": ca,
            "symbol": pos.get("symbol"),
            "entry_price": entry,
            "exit_price": price,
            "mult": mult,
            "pnl_usd": pnl,
            "reason": reason,
            "cash_usd": live["cash_usd"],
        },
    )
    print(
        f"live already_flat {str(ca)[:10]}… status={pos['status']} mult={mult:.2f} pnl={pnl:.2f}",
        flush=True,
    )
    _notify(
        webhook,
        discord_post,
        title=f"⚪ 残高0でクローズ · ${pos.get('symbol') or '?'}",
        description=(
            f"on-chain tokenBal=0 · **{mult:.2f}倍** · PnL **{_fmt_money(pnl)}**\n"
            f"`{ca}`"
        ),
        color=0x95A5A6,
        state=state,
    )


def process_live_positions(
    state: dict,
    book: Path,
    chain: str,
    fetch_dex: Callable[[str, str], dict],
    webhook: str | None = None,
    discord_post: Callable | None = None,
) -> dict:
    """Mark prices; +100% half / -40% stop via real sells. Soft-fail on swap errors."""
    live = ensure_live_state(state)
    _rollover_week(live)
    now = time.time()
    stats = {"marked": 0, "half": 0, "stop": 0, "open": 0, "fail": 0}
    positions = list(state.get("live_positions") or [])
    chain = (chain or "arc").strip().lower()

    for pos in positions:
        status = pos.get("status") or "open"
        if status not in ("open", "half_taken"):
            continue
        ca = pos.get("ca")
        entry = float(pos.get("entry_price") or 0)
        if not ca or entry <= 0:
            continue
        pos_chain = (pos.get("chain") or chain).lower()
        if pos_chain != "arc":
            continue
        dex = fetch_dex(ca, pos_chain)
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
        append_live_book(
            book,
            {
                "event": "mark",
                "ca": ca,
                "symbol": pos.get("symbol"),
                "price": price,
                "mult": mult,
                "status": status,
                "remaining_usd": rem,
                "entry_price": entry,
                "stop_confirm_count": int(pos.get("stop_confirm_count") or 0),
            },
        )

        opened_at = float(pos.get("opened_at") or 0)
        in_grace = opened_at > 0 and (now - opened_at) < exit_grace_sec()
        if in_grace:
            # No stop AND no half-take during grace after open
            if mult > LIVE_STOP_MULT:
                pos["stop_confirm_count"] = 0
            stats["open"] += 1
            continue

        # -40% stop → sell 100% remaining (needs LIVE_STOP_CONFIRM consecutive marks)
        if mult <= LIVE_STOP_MULT:
            need = stop_confirm_needed()
            cnt = int(pos.get("stop_confirm_count") or 0) + 1
            pos["stop_confirm_count"] = cnt
            if cnt < need:
                append_live_book(
                    book,
                    {
                        "event": "stop_pending",
                        "ca": ca,
                        "symbol": pos.get("symbol"),
                        "mult": mult,
                        "confirm": cnt,
                        "need": need,
                        "price": price,
                        "entry_price": entry,
                    },
                )
                print(
                    f"live stop_pending {ca[:10]}… mult={mult:.2f} confirm={cnt}/{need}",
                    flush=True,
                )
                stats["open"] += 1
                continue
            data, err = live_exec.swap_sell_token_to_usdc(pos_chain, ca, 100)
            if err or data is None:
                if err == "already_flat" or "tokenBal=0" in str(err or ""):
                    _close_already_flat(
                        state, book, live, pos,
                        now=now, price=price, entry=entry, mult=mult, rem=rem,
                        reason="already_flat",
                        webhook=webhook, discord_post=discord_post,
                    )
                    stats["stop"] += 1
                    continue
                stats["fail"] += 1
                append_live_book(
                    book,
                    {
                        "event": "stop_failed",
                        "ca": ca,
                        "symbol": pos.get("symbol"),
                        "reason": err or "swap_fail",
                        "mult": mult,
                        "swap": live_exec.summarize_swap_result(data),
                    },
                )
                _notify(
                    webhook,
                    discord_post,
                    title="⚠️ 実弾エラー",
                    description=(
                        f"損切り失敗 · ${pos.get('symbol') or '?'} · {mult:.2f}倍\n"
                        f"reason=`{err}` · 次回再試行"
                    ),
                    color=0xE67E22,
                )
                print(f"live stop swap fail {ca[:10]}… {err}", flush=True)
                continue
            exit_value = rem * mult
            pnl = exit_value - rem
            live["cash_usd"] = float(live.get("cash_usd") or 0) + exit_value
            live["realized_pnl_usd"] = float(live.get("realized_pnl_usd") or 0) + pnl
            pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
            pos["status"] = "stopped"
            pos["closed_at"] = now
            pos["remaining_usd"] = 0
            pos["remaining_pct"] = 0
            pos["swap_stop"] = live_exec.summarize_swap_result(data)
            live["week_losses"] = int(live.get("week_losses") or 0) + 1
            if max_losses_week() > 0 and live["week_losses"] >= max_losses_week():
                live["week_stopped"] = True
            stats["stop"] += 1
            _notify(
                webhook,
                discord_post,
                title=f"🔴 損切り · ${pos.get('symbol') or '?'}",
                description=(
                    f"**{mult:.2f}倍** · このPnL **{_fmt_money(pnl)}**\n"
                    f"口座実現PnL **{_fmt_money(float(live.get('realized_pnl_usd') or 0))}**\n"
                    f"`{ca}`"
                ),
                color=0xE74C3C,
                state=state,
            )
            append_live_book(
                book,
                {
                    "event": "stop",
                    "ca": ca,
                    "symbol": pos.get("symbol"),
                    "entry_price": entry,
                    "exit_price": price,
                    "mult": mult,
                    "pnl_usd": pnl,
                    "cash_usd": live["cash_usd"],
                    "week_losses": live["week_losses"],
                    "swap": pos["swap_stop"],
                },
            )
            print(f"live stop {ca[:10]}… mult={mult:.2f} pnl={pnl:.2f}", flush=True)
            continue

        # Recovered above stop → reset consecutive confirm counter
        pos["stop_confirm_count"] = 0

        # +100% half take → sell 50%
        if (not pos.get("half_taken")) and mult >= LIVE_HALF_TAKE_MULT and status == "open":
            data, err = live_exec.swap_sell_token_to_usdc(pos_chain, ca, 50)
            if err or data is None:
                if err == "already_flat" or "tokenBal=0" in str(err or ""):
                    _close_already_flat(
                        state, book, live, pos,
                        now=now, price=price, entry=entry, mult=mult, rem=rem,
                        reason="already_flat_half",
                        webhook=webhook, discord_post=discord_post,
                    )
                    stats["stop"] += 1
                    continue
                stats["fail"] += 1
                append_live_book(
                    book,
                    {
                        "event": "half_take_failed",
                        "ca": ca,
                        "symbol": pos.get("symbol"),
                        "reason": err or "swap_fail",
                        "mult": mult,
                        "swap": live_exec.summarize_swap_result(data),
                    },
                )
                _notify(
                    webhook,
                    discord_post,
                    title="⚠️ 実弾エラー",
                    description=(
                        f"半分利確失敗 · ${pos.get('symbol') or '?'} · {mult:.2f}倍\n"
                        f"reason=`{err}` · 次回再試行"
                    ),
                    color=0xE67E22,
                )
                print(f"live half swap fail {ca[:10]}… {err}", flush=True)
                continue
            half = rem / 2.0
            exit_value = half * mult
            pnl = exit_value - half
            live["cash_usd"] = float(live.get("cash_usd") or 0) + exit_value
            live["realized_pnl_usd"] = float(live.get("realized_pnl_usd") or 0) + pnl
            pos["realized_pnl_usd"] = float(pos.get("realized_pnl_usd") or 0) + pnl
            pos["remaining_usd"] = rem - half
            pos["remaining_pct"] = float(pos.get("size_pct") or LIVE_SIZE_PCT_DEFAULT) / 2.0
            pos["half_taken"] = True
            pos["status"] = "half_taken"
            pos["half_taken_at"] = now
            pos["half_taken_price"] = price
            pos["swap_half"] = live_exec.summarize_swap_result(data)
            stats["half"] += 1
            _notify(
                webhook,
                discord_post,
                title=f"🟡 半分利確 · ${pos.get('symbol') or '?'}",
                description=(
                    f"**{mult:.2f}倍** · このPnL **{_fmt_money(pnl)}**\n"
                    f"残り ~${float(pos.get('remaining_usd') or 0):,.2f}\n"
                    f"口座実現PnL **{_fmt_money(float(live.get('realized_pnl_usd') or 0))}**\n"
                    f"`{ca}`"
                ),
                color=0xF1C40F,
                state=state,
            )
            append_live_book(
                book,
                {
                    "event": "half_take",
                    "ca": ca,
                    "symbol": pos.get("symbol"),
                    "entry_price": entry,
                    "exit_price": price,
                    "mult": mult,
                    "pnl_usd": pnl,
                    "remaining_usd": pos["remaining_usd"],
                    "cash_usd": live["cash_usd"],
                    "swap": pos["swap_half"],
                },
            )
            print(f"live half_take {ca[:10]}… mult={mult:.2f} pnl={pnl:.2f}", flush=True)
            continue

        stats["open"] += 1

    # Equity mark (approximate)
    open_mv = 0.0
    for p in active_positions({"live_positions": positions}):
        rem = float(p.get("remaining_usd") or 0)
        entry = float(p.get("entry_price") or 0)
        mark = float(p.get("last_mark_price") or entry or 0)
        if entry > 0 and mark > 0:
            open_mv += rem * (mark / entry)
        else:
            open_mv += rem
    cash = float(live.get("cash_usd") or 0)
    live["equity_usd"] = cash + open_mv

    state["live_positions"] = positions
    state["live"] = live

    return stats

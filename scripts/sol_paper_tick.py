#!/usr/bin/env python3
"""Solana channel paper trading — $100 bankroll per notify channel.

Drives two independent books from local signal-state open_alerts:
  1) StonkFun diggers  → sol-wallets/paper_stonkfun_book.json
  2) Sol smart signals → sol-wallets/paper_sol_smart_book.json

Rules (meme constitution style):
  - LIVE_TRADING=0 always; DexScreener marks only (no box GMGN / Super)
  - Size ~20% (30% if n>=3) of bankroll, capped by cash
  - One open per mint; max concurrent PAPER_MAX_OPEN (default 3)
  - Entry = alert_price_usd at notify
  - Exits (攻撃的ムーンバッグ): TP1 +25% sell 50%; TP2 +60% → leave 15% moonbag;
    main stop −50% before moonbag; moonbag catastrophic −75% only; mark each tick
  - Size default 30% (n≥3 → 40%); PAPER_MAX_OPEN 3–4; no add while moonbag on mint
  - Discord 【紙実況】 ONLY via DISCORD_SOL_PAPER_WEBHOOK_URL
    (never StonkFun / Sol smart signal channels)

Env (shared):
  SOL_PAPER_TICK_SEC=90
  SOL_PAPER_BANKROLL_USD=100
  SOL_PAPER_MAX_OPEN=4
  SOL_PAPER_ALERT_MAX_AGE_SEC=21600
  SOL_PAPER_DISCORD=1          # 【紙実況】 to DISCORD_SOL_PAPER_WEBHOOK_URL only
  SOL_PAPER_JIKEI_SEC=1200     # heartbeat 実況 cadence (~20 min)
  SOL_PAPER_SEED_ON_START=1    # open up to max_open from newest alerts if empty
  PAPER_MARK_HEARTBEAT=0      # forced off (paper_trade mark spam)
  # Aggressive moonbag (defaults applied in _apply_env_for_book):
  # PAPER_SIZE_PCT_DEFAULT=30 PAPER_SIZE_PCT_STRONG=40
  # PAPER_TP1_MULT=1.25 PAPER_TP1_SELL_PCT=0.50
  # PAPER_TP2_MULT=1.60 PAPER_MOONBAG_PCT=0.15
  # PAPER_STOP_MULT=0.50 PAPER_MOON_STOP_MULT=0.25
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trade as paper_mod  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
CHAIN = "solana"

# Force paper-only
os.environ["LIVE_TRADING"] = "0"
if str(os.environ.get("PAPER_MARK_HEARTBEAT") or "").strip() == "":
    os.environ["PAPER_MARK_HEARTBEAT"] = "0"

INTERVAL = float(os.environ.get("SOL_PAPER_TICK_SEC") or "90")
BANKROLL = float(os.environ.get("SOL_PAPER_BANKROLL_USD") or "100")
MAX_OPEN = int(float(os.environ.get("SOL_PAPER_MAX_OPEN") or "4"))
ALERT_MAX_AGE = float(os.environ.get("SOL_PAPER_ALERT_MAX_AGE_SEC") or str(6 * 3600))
SEED_ON_START = str(os.environ.get("SOL_PAPER_SEED_ON_START") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
DISCORD_ON = str(os.environ.get("SOL_PAPER_DISCORD") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
JIKEI_EVERY = float(os.environ.get("SOL_PAPER_JIKEI_SEC") or "1200")
SUMMARY_EVERY = float(os.environ.get("SOL_PAPER_SUMMARY_SEC") or "300")

SOL_DIR = ROOT / "sol-wallets"
LOG_DIR = Path(
    os.environ.get("SOL_PAPER_STATE_DIR")
    or "/home/box/.local/share/scout-wallet-bot"
)
LOG_PATH = Path(os.environ.get("SOL_PAPER_TICK_LOG") or (LOG_DIR / "sol_paper_tick.log"))

CHANNELS: list[dict[str, Any]] = [
    {
        "id": "stonkfun",
        "label": "StonkFun digger",
        "signal_state": SOL_DIR / "raw" / "stonkfun_signal_state.json",
        "book": SOL_DIR / "paper_stonkfun_book.json",
        "fills": SOL_DIR / "paper_stonkfun_fills.jsonl",
        "summary": SOL_DIR / "summary_paper_stonkfun.md",
        "n_key": "digger_count",
    },
    {
        "id": "sol_smart",
        "label": "Solana smart",
        "signal_state": SOL_DIR / "raw" / "sol_smart_signal_state.json",
        "book": SOL_DIR / "paper_sol_smart_book.json",
        "fills": SOL_DIR / "paper_sol_smart_fills.jsonl",
        "summary": SOL_DIR / "summary_paper_sol_smart.md",
        "n_key": "wallet_count",
    },
]

def _pos_flag(p: dict) -> str:
    st = p.get("status") or "open"
    if st == "moonbag" or p.get("moonbag"):
        return "ムーン袋"
    if st in ("tp1_taken", "half_taken") or p.get("tp1_taken"):
        return "TP1後"
    return "open"


_price_cache: dict[str, tuple[float, dict]] = {}
_PRICE_TTL = 12.0
_last_jikkei_at = 0.0
_EVENT_GAP = float(os.environ.get("SOL_PAPER_EVENT_GAP_SEC") or "20")
_last_event_post = 0.0


def log(msg: str) -> None:
    # stdout is redirected to SOL_PAPER_TICK_LOG by the shell wrapper — avoid double-write
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)


def _load_paper_webhook_secret() -> None:
    """Load ONLY the paper 実況 webhook — never signal-channel URLs into this process for posting."""
    try:
        from load_secrets import load as load_secrets

        load_secrets(["DISCORD_SOL_PAPER_WEBHOOK_URL"])
    except Exception:
        pass


def paper_jikkei_webhook() -> str | None:
    """Dedicated 【紙実況】 channel. Never falls back to StonkFun / sol_smart / RH."""
    if not DISCORD_ON:
        return None
    _load_paper_webhook_secret()
    u = (os.environ.get("DISCORD_SOL_PAPER_WEBHOOK_URL") or "").strip()
    return u or None


def _discord_post_raw(url: str, embeds: list | None = None, content: str = "") -> None:
    body: dict[str, Any] = {}
    if content:
        body["content"] = content[:1900]
    if embeds:
        body["embeds"] = embeds[:10]
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url + ("&wait=true" if "?" in url else "?wait=true"),
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _book_snapshot(ch: dict, st: dict | None = None) -> dict:
    st = st if st is not None else load_book(ch["book"], ch["id"])
    paper = paper_mod.ensure_paper_state(st)
    active = paper_mod.active_positions(st)
    unreal = 0.0
    pos_lines = []
    for p in active:
        rem = float(p.get("remaining_usd") or 0)
        entry = float(p.get("entry_price") or 0)
        mark = float(p.get("last_mark_price") or entry or 0)
        mult = float(p.get("last_mark_mult") or (mark / entry if entry else 0) or 0)
        u = rem * mult - rem if mult else 0.0
        unreal += u
        flag = _pos_flag(p)
        pos_lines.append(
            f"· `${p.get('symbol') or '?'}` {mult:.2f}x · 残${rem:.0f} · uPnL ${u:+.2f} · {flag}"
        )
    bankroll = float(st.get("bankroll_usd") or BANKROLL)
    cash = float(paper.get("cash_usd") or bankroll)
    equity = float(paper.get("equity_usd") or bankroll)
    realized = float(paper.get("realized_pnl_usd") or 0)
    return {
        "id": ch["id"],
        "label": ch["label"],
        "bankroll": bankroll,
        "cash": cash,
        "equity": equity,
        "realized": realized,
        "unreal": unreal,
        "active": active,
        "pos_lines": pos_lines,
        "st": st,
    }


def build_jikkei_embeds(reason: str, event_note: str | None = None) -> list[dict]:
    jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    snaps = [_book_snapshot(ch) for ch in CHANNELS]
    total_eq = sum(s["equity"] for s in snaps)
    total_cash = sum(s["cash"] for s in snaps)
    total_rpnl = sum(s["realized"] for s in snaps)
    total_u = sum(s["unreal"] for s in snaps)
    head_lines = [
        f"**理由:** {reason}",
        f"**時刻:** {jst}",
        (
            f"**合算** 純資産 **${total_eq:.2f}** · 現金 ${total_cash:.2f} · "
            f"実現 ${total_rpnl:+.2f} · 含み ${total_u:+.2f}"
        ),
        f"原資 ${BANKROLL:.0f}×2 · LIVE_TRADING=0 · 攻撃的ムーンバッグ · 実注文なし",
    ]
    head_desc = chr(10).join(head_lines)
    if event_note:
        head_desc = event_note + chr(10) + chr(10) + head_desc
    embeds: list[dict] = [
        {
            "title": "【紙実況】Solana 仮想トレード（攻撃的ムーンバッグ）",
            "description": head_desc[:1900],
            "color": 0xF1C40F,
            "footer": {"text": "DISCORD_SOL_PAPER only · signal ch へは投稿しない"},
        }
    ]
    for s in snaps:
        body_lines = [
            f"現金 **${s['cash']:.2f}** · 純資産 **${s['equity']:.2f}**",
            f"実現PnL **${s['realized']:+.2f}** · 含み **${s['unreal']:+.2f}**",
            f"オープン **{len(s['active'])}/{MAX_OPEN}**",
        ]
        if s["pos_lines"]:
            body_lines.append("")
            body_lines.extend(s["pos_lines"][:6])
        else:
            body_lines.append("")
            body_lines.append("· （ポジションなし）")
        embeds.append(
            {
                "title": f"【紙】{s['label']}",
                "description": chr(10).join(body_lines)[:1900],
                "color": 0x3498DB if s["id"] == "stonkfun" else 0x9B59B6,
                "footer": {"text": f"{s['id']} · {jst}"},
            }
        )
    return embeds


def post_jikkei(reason: str, *, force: bool = False, event_note: str | None = None) -> bool:
    """Post 【紙実況】 to DISCORD_SOL_PAPER_WEBHOOK_URL only."""
    global _last_jikkei_at, _last_event_post
    url = paper_jikkei_webhook()
    if not url:
        log("jikkei skip: DISCORD_SOL_PAPER_WEBHOOK_URL missing")
        return False
    now = time.time()
    is_event = reason.startswith("event:") or reason in ("open", "half", "tp1", "tp2", "stop", "seed", "announce")
    if not force:
        if is_event:
            if now - _last_event_post < _EVENT_GAP and reason != "announce":
                return False
        else:
            if now - _last_jikkei_at < JIKEI_EVERY:
                return False
    embeds = build_jikkei_embeds(reason, event_note=event_note)
    try:
        _discord_post_raw(url, embeds=embeds)
        if is_event:
            _last_event_post = now
        if reason in ("heartbeat", "announce", "seed") or not is_event:
            _last_jikkei_at = now
        # persist last post time
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "sol_paper_jikkei_state.json").write_text(
                json.dumps(
                    {
                        "last_jikkei_at": _last_jikkei_at,
                        "last_event_post": _last_event_post,
                        "last_reason": reason,
                        "updated_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + chr(10),
                encoding="utf-8",
            )
        except OSError:
            pass
        log(f"jikkei posted reason={reason}")
        return True
    except Exception as e:
        log(f"jikkei fail: {type(e).__name__}")
        return False


def _restore_jikkei_clock() -> None:
    global _last_jikkei_at, _last_event_post
    p = LOG_DIR / "sol_paper_jikkei_state.json"
    if not p.exists():
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        _last_jikkei_at = float(d.get("last_jikkei_at") or 0)
        _last_event_post = float(d.get("last_event_post") or 0)
    except Exception:
        pass

def fetch_dex_price(ca: str, chain: str = CHAIN) -> dict:
    key = (ca or "").lower()
    now = time.time()
    cached = _price_cache.get(key)
    if cached and now - cached[0] < _PRICE_TTL and cached[1].get("ok"):
        return cached[1]
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        if cached and cached[1].get("ok"):
            out = dict(cached[1])
            out["reason"] = f"stale:{type(e).__name__}"
            return out
        return {"ok": False, "price_usd": None, "reason": type(e).__name__}
    pairs = (data or {}).get("pairs") or []
    if not pairs:
        return {"ok": False, "price_usd": None, "reason": "no_pair"}
    slug = (chain or CHAIN).lower()
    preferred = [p for p in pairs if (p.get("chainId") or "").lower() in (slug, "solana")]
    pool = preferred or pairs

    def liq(p: dict) -> float:
        try:
            return float(((p.get("liquidity") or {}).get("usd")) or 0)
        except (TypeError, ValueError):
            return 0.0

    pair = max(pool, key=liq)
    try:
        price = float(pair.get("priceUsd")) if pair.get("priceUsd") is not None else None
    except (TypeError, ValueError):
        price = None
    mcap = pair.get("marketCap") or pair.get("fdv")
    liq_usd = (pair.get("liquidity") or {}).get("usd")
    try:
        mcap = float(mcap) if mcap is not None else None
    except (TypeError, ValueError):
        mcap = None
    try:
        liq_usd = float(liq_usd) if liq_usd is not None else None
    except (TypeError, ValueError):
        liq_usd = None
    out = {
        "ok": bool(price and price > 0),
        "price_usd": price,
        "mcap_usd": mcap,
        "fdv": mcap,
        "liq_usd": liq_usd,
        "reason": None if price else "no_price",
        "src": "dex",
    }
    if out["ok"]:
        _price_cache[key] = (now, out)
    return out


def empty_book(channel_id: str) -> dict:
    return {
        "channel": channel_id,
        "live_trading": 0,
        "bankroll_usd": BANKROLL,
        "paper": {},
        "paper_positions": [],
        "seen_alert_cas": [],
        "tick_closed_cas": [],
        "seeded_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def load_book(path: Path, channel_id: str) -> dict:
    if not path.exists():
        return empty_book(channel_id)
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_book(channel_id)
    if not isinstance(st, dict):
        return empty_book(channel_id)
    st.setdefault("channel", channel_id)
    st["live_trading"] = 0
    st.setdefault("paper_positions", [])
    st.setdefault("seen_alert_cas", [])
    st.setdefault("tick_closed_cas", [])
    return st


def save_book(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    st["live_trading"] = 0
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    st["updated_jst"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_signal_alerts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get("open_alerts") or [])


def _apply_env_for_book() -> None:
    os.environ["LIVE_TRADING"] = "0"
    os.environ["PAPER_BANKROLL_USD"] = str(BANKROLL)
    os.environ["PAPER_MAX_OPEN"] = str(MAX_OPEN)
    os.environ["PAPER_MAX_ENTRIES_WEEK"] = os.environ.get("PAPER_MAX_ENTRIES_WEEK") or "0"
    os.environ["PAPER_MAX_LOSSES_WEEK"] = os.environ.get("PAPER_MAX_LOSSES_WEEK") or "0"
    if str(os.environ.get("PAPER_MARK_HEARTBEAT") or "").strip() == "":
        os.environ["PAPER_MARK_HEARTBEAT"] = "0"
    # Aggressive moonbag defaults (do not override if already set)
    os.environ.setdefault("PAPER_SIZE_PCT_DEFAULT", "30")
    os.environ.setdefault("PAPER_SIZE_PCT_STRONG", "40")
    os.environ.setdefault("PAPER_TP1_MULT", "1.25")
    os.environ.setdefault("PAPER_TP1_SELL_PCT", "0.50")
    os.environ.setdefault("PAPER_TP2_MULT", "1.60")
    os.environ.setdefault("PAPER_MOONBAG_PCT", "0.15")
    os.environ.setdefault("PAPER_STOP_MULT", "0.50")
    os.environ.setdefault("PAPER_MOON_STOP_MULT", "0.25")


def active_cas(st: dict) -> set[str]:
    out = set()
    for p in paper_mod.active_positions(st):
        ca = (p.get("ca") or "").lower()
        if ca:
            out.add(ca)
    return out


def adopt_alerts(
    st: dict,
    alerts: list[dict],
    *,
    fills: Path,
    n_key: str,
    webhook: str | None,
    discord_post: Callable | None,
    seed: bool,
) -> int:
    """Open paper longs from new (or seed) alerts. Returns opens count."""
    paper_mod.ensure_paper_state(st)
    seen = set(x.lower() for x in (st.get("seen_alert_cas") or []) if x)
    closed = set(x.lower() for x in (st.get("tick_closed_cas") or []) if x)
    closed |= {
        (p.get("ca") or "").lower()
        for p in (st.get("paper_positions") or [])
        if (p.get("status") or "") in ("stopped", "closed", "done")
    }
    held = active_cas(st)
    now = time.time()

    ranked: list[dict] = []
    for a in alerts:
        ca = (a.get("ca") or a.get("mint") or "").lower()
        if not ca:
            continue
        try:
            price = float(a.get("alert_price_usd") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        try:
            posted_at = float(a.get("posted_at") or 0)
        except (TypeError, ValueError):
            posted_at = 0.0
        age = (now - posted_at) if posted_at > 0 else 0.0
        ranked.append({**a, "_ca": ca, "_price": price, "_posted_at": posted_at, "_age": age})

    ranked.sort(key=lambda x: float(x.get("_posted_at") or 0), reverse=True)

    # On first seed: mark ancient alerts seen so we don't backfill dozens later
    if seed and not st.get("seeded_at"):
        for a in ranked:
            if a["_age"] > ALERT_MAX_AGE:
                seen.add(a["_ca"])

    opened = 0
    seeding = bool(seed and not st.get("seeded_at"))
    for a in ranked:
        ca = a["_ca"]
        if ca in seen or ca in closed or ca in held:
            continue
        # Only trade alerts within age window (seed uses same window)
        if a["_age"] > ALERT_MAX_AGE:
            seen.add(ca)
            continue
        ok, reason = paper_mod.can_open_paper(st, chain=CHAIN)
        if not ok:
            if reason == "max_open_positions":
                break
            seen.add(ca)
            continue
        # one open per mint (extra guard)
        if ca in active_cas(st):
            seen.add(ca)
            continue
        try:
            n = int(a.get(n_key) or a.get("n") or a.get("wallet_count") or a.get("digger_count") or 1)
        except (TypeError, ValueError):
            n = 1
        pos = paper_mod.open_paper_position(
            st,
            fills,
            ca=ca,
            symbol=a.get("symbol"),
            entry_price=float(a["_price"]),
            n=n,
            chain=CHAIN,
            mcap=a.get("alert_mcap"),
            liq=a.get("alert_liq"),
            webhook=webhook if discord_post else None,
            discord_post=discord_post,
        )
        seen.add(ca)
        if pos:
            held.add(ca)
            opened += 1
            log(
                f"open [{st.get('channel')}] {a.get('symbol') or '?'} {ca[:10]}… "
                f"entry={a['_price']} n={n} age={a['_age']:.0f}s"
            )
            if seeding:
                if opened >= MAX_OPEN:
                    break
            else:
                # normal tick: at most one new open per channel per tick
                break
        else:
            log(f"open blocked [{st.get('channel')}] {ca[:10]}…")

    st["seen_alert_cas"] = list(seen)[-2000:]
    if seeding:
        st["seeded_at"] = datetime.now(timezone.utc).isoformat()
        # After seed fill, mark other in-window alerts seen so we don't burst later
        if opened >= MAX_OPEN:
            for a in ranked:
                if a["_age"] <= ALERT_MAX_AGE:
                    seen.add(a["_ca"])
            st["seen_alert_cas"] = list(seen)[-2000:]
    return opened


def write_summary(ch: dict, st: dict) -> None:
    paper = paper_mod.ensure_paper_state(st)
    positions = st.get("paper_positions") or []
    active = [p for p in positions if (p.get("status") or "") in paper_mod.ACTIVE_STATUSES]
    stopped = [p for p in positions if (p.get("status") or "") == "stopped"]
    half = [p for p in positions if p.get("half_taken") or p.get("tp1_taken") or p.get("moonbag")]
    closed_pnl = []
    wins = losses = 0
    for p in positions:
        if (p.get("status") or "") not in ("stopped", "closed", "done", "half_taken", "tp1_taken", "moonbag"):
            continue
        # count fully closed only for win rate
        if (p.get("status") or "") in ("stopped", "closed", "done"):
            rp = float(p.get("realized_pnl_usd") or 0)
            closed_pnl.append(rp)
            if rp > 0:
                wins += 1
            elif rp < 0:
                losses += 1
    decided = wins + losses
    win_rate = (100.0 * wins / decided) if decided else 0.0
    unreal = 0.0
    for p in active:
        rem = float(p.get("remaining_usd") or 0)
        entry = float(p.get("entry_price") or 0)
        mark = float(p.get("last_mark_price") or entry or 0)
        if entry > 0 and mark > 0:
            unreal += rem * (mark / entry) - rem
    br = float(paper.get("cash_usd") or 0)  # noqa: F841 — clarity
    bankroll = float(st.get("bankroll_usd") or BANKROLL)
    equity = float(paper.get("equity_usd") or bankroll)
    cash = float(paper.get("cash_usd") or bankroll)
    realized = float(paper.get("realized_pnl_usd") or 0)

    fills_counts: dict[str, int] = {}
    fills_path: Path = ch["fills"]
    if fills_path.exists():
        for line in fills_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = str(row.get("event") or "")
            if ev:
                fills_counts[ev] = fills_counts.get(ev, 0) + 1

    lines = [
        f"# 【紙】{ch['label']} 仮想トレード",
        "",
        f"- 生成: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}",
        f"- チャンネル: `{ch['id']}` · LIVE_TRADING=**0** · 実注文なし",
        f"- 原資: **${bankroll:.2f}** · 現金 **${cash:.2f}** · 純資産 **${equity:.2f}**",
        f"- 実現PnL: **${realized:+.2f}** · 含み損益: **${unreal:+.2f}**",
        f"- 勝率: **{win_rate:.0f}%** ({wins}W/{losses}L · 決着{decided})",
        f"- ルール: **攻撃的ムーンバッグ** サイズ30%(n≥3→40%) / 同時最大{MAX_OPEN} / 1mint1本 / TP1+25%で50% / TP2+60%→ムーン15% / ストップ-50% / ムーン袋破局-75%",
        f"- 価格: DexScreenerのみ（box GMGNなし）",
        f"- 帳簿: `{ch['book'].name}` · fills `{ch['fills'].name}`",
        f"- fills: " + (", ".join(f"{k}={v}" for k, v in sorted(fills_counts.items())) or "（なし）"),
        "",
        "## オープンポジション",
    ]
    if not active:
        lines.append("- （なし）")
    else:
        for p in active:
            mult = float(p.get("last_mark_mult") or 0)
            rem = float(p.get("remaining_usd") or 0)
            u = rem * mult - rem if mult else 0.0
            flag = _pos_flag(p)
            lines.append(
                f"- `${p.get('symbol') or '?'}` {mult:.2f}x · 残${rem:.2f} · "
                f"uPnL ${u:+.2f} · {flag} · `{(p.get('ca') or '')[:12]}…`"
            )
    lines += ["", "## 最近クローズ（ストップ）"]
    if not stopped:
        lines.append("- （なし）")
    else:
        for p in stopped[-8:]:
            lines.append(
                f"- `${p.get('symbol') or '?'}` mult={float(p.get('last_mark_mult') or 0):.2f}x · "
                f"PnL ${float(p.get('realized_pnl_usd') or 0):+.2f}"
            )
    lines += [
        "",
        "## 注意",
        "- 【紙実況】は DISCORD_SOL_PAPER_WEBHOOK_URL 専用（シグナルchへは投稿しない）",
        "- 本サマリーはローカル更新。実弾トレードには絶対に使わない",
        "",
    ]
    out: Path = ch["summary"]
    out.write_text("\n".join(lines), encoding="utf-8")


def tick_channel(ch: dict, *, force_seed: bool = False) -> dict:
    _apply_env_for_book()
    st = load_book(ch["book"], ch["id"])
    st["bankroll_usd"] = BANKROLL
    paper_mod.ensure_paper_state(st)
    # ensure bankroll matches config on fresh books
    paper = st["paper"]
    if not st.get("paper_positions") and abs(float(paper.get("cash_usd") or 0) - BANKROLL) > 1e-6:
        if float(paper.get("realized_pnl_usd") or 0) == 0 and not paper.get("equity_curve"):
            paper["cash_usd"] = BANKROLL
            paper["equity_usd"] = BANKROLL

    alerts = load_signal_alerts(ch["signal_state"])
    # Never pass signal-channel webhooks into paper_trade — 実況は別経路
    need_seed = force_seed or (SEED_ON_START and not st.get("seeded_at"))
    opened = adopt_alerts(
        st,
        alerts,
        fills=ch["fills"],
        n_key=ch["n_key"],
        webhook=None,
        discord_post=None,
        seed=need_seed,
    )

    before = {
        (p.get("ca") or "").lower(): (
            p.get("status"),
            bool(p.get("half_taken") or p.get("tp1_taken")),
            bool(p.get("moonbag")),
        )
        for p in (st.get("paper_positions") or [])
    }
    stats = paper_mod.process_paper_positions(
        st,
        ch["fills"],
        CHAIN,
        lambda ca, _ch: fetch_dex_price(ca, CHAIN),
        webhook=None,
        discord_post=None,
    )
    closed = list(st.get("tick_closed_cas") or [])
    event_notes: list[str] = []
    for p in st.get("paper_positions") or []:
        ca = (p.get("ca") or "").lower()
        prev_st, prev_tp1, prev_moon = before.get(ca, (None, False, False))
        now_st = p.get("status")
        now_tp1 = bool(p.get("half_taken") or p.get("tp1_taken"))
        now_moon = bool(p.get("moonbag") or now_st == "moonbag")
        if prev_st in paper_mod.ACTIVE_STATUSES and now_st in ("stopped", "closed", "done"):
            if ca and ca not in closed:
                closed.append(ca)
            log(
                f"exit [{ch['id']}] {p.get('symbol')} {ca[:10]}… "
                f"status={now_st} mult={p.get('last_mark_mult')}"
            )
            event_notes.append(
                f"⛔ `{ch['id']}` ${p.get('symbol') or '?'} {now_st} · "
                f"{float(p.get('last_mark_mult') or 0):.2f}x · "
                f"PnL ${float(p.get('realized_pnl_usd') or 0):+.2f}"
            )
        elif (not prev_moon) and now_moon:
            event_notes.append(
                f"🚀 `{ch['id']}` ${p.get('symbol') or '?'} ムーン袋へ · "
                f"{float(p.get('last_mark_mult') or 0):.2f}x · "
                f"残 ${float(p.get('remaining_usd') or 0):.2f}"
            )
        elif (not prev_tp1) and now_tp1 and not now_moon:
            event_notes.append(
                f"🎯 `{ch['id']}` ${p.get('symbol') or '?'} TP1利確 · "
                f"{float(p.get('last_mark_mult') or 0):.2f}x"
            )
    st["tick_closed_cas"] = closed[-500:]
    save_book(ch["book"], st)
    write_summary(ch, st)
    active_n = len(paper_mod.active_positions(st))
    paper = st.get("paper") or {}
    return {
        "id": ch["id"],
        "label": ch["label"],
        "opened": opened,
        "active": active_n,
        "marked": stats.get("marked", 0),
        "half": stats.get("half", 0),
        "tp1": stats.get("tp1", 0),
        "tp2": stats.get("tp2", 0),
        "stop": stats.get("stop", 0),
        "cash": float(paper.get("cash_usd") or 0),
        "equity": float(paper.get("equity_usd") or 0),
        "realized": float(paper.get("realized_pnl_usd") or 0),
        "alerts": len(alerts),
        "event_notes": event_notes,
        "equity_ms": int(stats.get("equity_ms") or 0),
    }


def run_once(*, force_seed: bool = False, announce: bool = False) -> int:
    if str(os.environ.get("LIVE_TRADING") or "0").strip() not in ("0", "", "false", "no"):
        log("REFUSE: LIVE_TRADING must be 0")
        return 2
    os.environ["LIVE_TRADING"] = "0"
    os.environ["PAPER_MARK_HEARTBEAT"] = os.environ.get("PAPER_MARK_HEARTBEAT") or "0"
    results = []
    all_notes: list[str] = []
    opened_total = half_total = tp2_total = stop_total = eq_ms = 0
    for ch in CHANNELS:
        try:
            r = tick_channel(ch, force_seed=force_seed)
            results.append(r)
            opened_total += int(r.get("opened") or 0)
            half_total += int(r.get("half") or 0)
            tp2_total += int(r.get("tp2") or 0)
            stop_total += int(r.get("stop") or 0)
            eq_ms += int(r.get("equity_ms") or 0)
            all_notes.extend(r.get("event_notes") or [])
            if r.get("opened"):
                all_notes.append(f"📥 `{r['id']}` 新規オープン +{r['opened']}")
            log(
                f"tick {r['id']}: open+={r['opened']} active={r['active']} "
                f"marked={r['marked']} half={r['half']} tp2={r.get('tp2',0)} stop={r['stop']} "
                f"cash=${r['cash']:.2f} eq=${r['equity']:.2f} "
                f"rpnl=${r['realized']:+.2f} alerts={r['alerts']}"
            )
        except Exception as e:
            log(f"tick fail {ch['id']}: {type(e).__name__}: {e}")

    if announce:
        # skip duplicate opening if we just posted announce <90s ago (restart race)
        if (time.time() - _last_jikkei_at) < 90:
            log("announce skip: recent jikkei already posted")
        else:
            post_jikkei(
                "announce",
                force=True,
                event_note="🟢 紙トレード実況スタート（原資 $100×2 · 攻撃的ムーンバッグ）",
            )
    elif all_notes or half_total or tp2_total or stop_total or opened_total or eq_ms:
        note = chr(10).join(all_notes[:8]) if all_notes else None
        reason = "event:fill"
        if stop_total:
            reason = "event:stop"
        elif tp2_total:
            reason = "event:tp2"
        elif half_total:
            reason = "event:tp1"
        elif opened_total:
            reason = "event:open"
        elif eq_ms:
            reason = "event:milestone"
        post_jikkei(reason, event_note=note)
    else:
        post_jikkei("heartbeat")
    return 0



def main() -> int:
    ap = argparse.ArgumentParser(description="Solana dual-channel paper tick")
    ap.add_argument("--once", action="store_true", help="Single tick then exit")
    ap.add_argument("--seed", action="store_true", help="Force seed from open_alerts")
    ap.add_argument("--announce", action="store_true", help="Force 【紙実況】 opening post")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SOL_DIR.mkdir(parents=True, exist_ok=True)
    _restore_jikkei_clock()
    wh_ok = bool(paper_jikkei_webhook())
    log(
        f"start interval={INTERVAL}s bankroll=${BANKROLL:.0f} max_open={MAX_OPEN} "
        f"jikkei={int(DISCORD_ON)} webhook={'ok' if wh_ok else 'missing'} "
        f"jikkei_every={JIKEI_EVERY:.0f}s seed={int(SEED_ON_START)} LIVE_TRADING=0"
    )

    if args.once:
        return run_once(force_seed=args.seed, announce=args.announce)

    # Opening 実況 once per process start
    first = True
    while True:
        t0 = time.time()
        run_once(
            force_seed=args.seed and first,
            announce=first or args.announce,
        )
        args.seed = False
        args.announce = False
        first = False
        elapsed = time.time() - t0
        time.sleep(max(1.0, INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("stop")
        raise SystemExit(0)

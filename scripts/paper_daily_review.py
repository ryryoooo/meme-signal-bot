#!/usr/bin/env python3
"""Daily Japanese paper-trade review from paper_log.jsonl / state / book.

Writes a short markdown summary for the last JST calendar day (or PAPER_DAILY_HOURS).
Optionally posts to Discord paper webhook. Never enables live trading.

Env:
  PAPER_LOG_PATH=paper_log.jsonl
  PAPER_BOOK_PATH=paper_book.jsonl
  STATE_PATH=state.json
  PAPER_DAILY_PATH=paper_daily.md          # also writes paper_daily_YYYY-MM-DD.md
  PAPER_DAILY_HOURS=24                    # lookback if no day filter
  PAPER_DAILY_DATE=YYYY-MM-DD             # optional JST day override
  DISCORD_PAPER_WEBHOOK_URL / DISCORD_WEBHOOK_URL
  PAPER_DAILY_POST_DISCORD=1
  LIVE_TRADING must stay 0
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


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


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def reason_key(r: str) -> str:
    if r.startswith("safety_fail") or r == "safety":
        return "safety"
    if r.startswith("cooldown"):
        return "cooldown"
    if r == "already_seen":
        return "already_seen"
    return (r or "unknown")[:48]


def discord_post(webhook: str, content: str = "", embeds: list | None = None) -> None:
    body: dict = {}
    if content:
        body["content"] = content[:1900]
    if embeds:
        body["embeds"] = embeds
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # Discord webhooks often return 204 No Content
        if e.code not in (200, 204):
            raise


def main() -> int:
    if env_bool("LIVE_TRADING", False):
        print("paper_daily_review: refuse LIVE_TRADING=1", flush=True)
        return 2

    log_path = Path(os.environ.get("PAPER_LOG_PATH", str(ROOT / "paper_log.jsonl")))
    book_path = Path(os.environ.get("PAPER_BOOK_PATH", str(ROOT / "paper_book.jsonl")))
    state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json")))
    out_path = Path(os.environ.get("PAPER_DAILY_PATH", str(ROOT / "paper_daily.md")))
    if not log_path.is_absolute():
        log_path = ROOT / log_path
    if not book_path.is_absolute():
        book_path = ROOT / book_path
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    day_s = (os.environ.get("PAPER_DAILY_DATE") or "").strip()
    if day_s:
        day = datetime.strptime(day_s, "%Y-%m-%d").replace(tzinfo=JST)
    else:
        # previous completed JST day when run at ~00:00 JST; else today so far
        if now_jst.hour < 1:
            day = (now_jst - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            day = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day + timedelta(days=1)
    hours = float(os.environ.get("PAPER_DAILY_HOURS", "24"))
    lookback = now_utc - timedelta(hours=hours)

    rows = load_jsonl(log_path)
    book = load_jsonl(book_path)
    state = load_state(state_path)
    paper = state.get("paper") or state.get("paper_state") or {}
    if not isinstance(paper, dict):
        paper = {}

    def in_window(ts: datetime | None) -> bool:
        if ts is None:
            return False
        tj = ts.astimezone(JST)
        if day_s or True:
            # prefer calendar day JST; also accept lookback overlap for sparse logs
            if day <= tj < day_end:
                return True
        return ts >= lookback

    posted = skipped = followups = 0
    reasons: Counter[str] = Counter()
    wins = losses = marks = 0
    day_rows = 0
    for row in rows:
        ts = parse_ts(row.get("ts") or row.get("at") or row.get("time"))
        if not in_window(ts) and ts is not None:
            continue
        if ts is None and day_s:
            continue
        if ts is not None:
            day_rows += 1
        ev = str(row.get("event") or "")
        if ev == "multiplier_followup":
            followups += 1
            continue
        if "posted" in row:
            if row.get("posted"):
                posted += 1
            else:
                skipped += 1
                reasons[reason_key(str(row.get("reason") or "unknown"))] += 1
            continue
        # rough win/loss from marks / closed events
        pnl = row.get("pnl_usd") or row.get("realized_pnl_usd") or row.get("pnl")
        if pnl is not None:
            try:
                pv = float(pnl)
                marks += 1
                if pv > 0:
                    wins += 1
                elif pv < 0:
                    losses += 1
            except (TypeError, ValueError):
                pass
        if ev in ("paper_close", "stopped", "take_profit", "stop_loss", "half_take"):
            marks += 1
            try:
                pv = float(row.get("pnl_usd") or row.get("realized_pnl_usd") or 0)
            except (TypeError, ValueError):
                pv = 0.0
            if pv > 0 or ev in ("take_profit", "half_take"):
                wins += 1
            elif pv < 0 or ev in ("stopped", "stop_loss"):
                losses += 1

    book_ev: Counter[str] = Counter()
    for row in book:
        ts = parse_ts(row.get("ts") or row.get("at"))
        if ts is not None and not in_window(ts):
            continue
        book_ev[str(row.get("event") or "unknown")] += 1
        pnl = row.get("pnl_usd") or row.get("realized_pnl_usd")
        if pnl is not None:
            try:
                pv = float(pnl)
                marks += 1
                if pv > 0:
                    wins += 1
                elif pv < 0:
                    losses += 1
            except (TypeError, ValueError):
                pass

    # if log has almost no timestamps matching day, fall back to all-time counts
    fallback = False
    if posted + skipped + followups == 0 and rows:
        fallback = True
        for row in rows:
            ev = str(row.get("event") or "")
            if ev == "multiplier_followup":
                followups += 1
                continue
            if "posted" not in row:
                continue
            if row.get("posted"):
                posted += 1
            else:
                skipped += 1
                reasons[reason_key(str(row.get("reason") or "unknown"))] += 1

    equity = paper.get("equity_usd")
    cash = paper.get("cash_usd")
    realized = paper.get("realized_pnl_usd")
    positions = state.get("paper_positions") or []
    open_n = sum(1 for p in positions if (p.get("status") or "") in ("open", "half_taken"))

    day_label = day.strftime("%Y-%m-%d")
    lines = [
        f"# 紙トレード日次レビュー（{day_label} JST）",
        "",
        f"- 生成: **{now_jst.strftime('%Y-%m-%d %H:%M')} JST**",
        f"- 対象日: **{day_label}**（JST 0:00–24:00）"
        + (" · ※ログが薄いため累計フォールバック" if fallback else ""),
        f"- 投稿: **{posted}** / 見送り: **{skipped}** / 倍率FU: **{followups}**",
        f"- 目安勝敗（マークあり）: 勝 **{wins}** / 負 **{losses}** / マーク数 **{marks}**",
        f"- 状態: 純資産 **${float(equity or 0):.2f}** · 現金 **${float(cash or 0):.2f}** · "
        f"実現PnL **${float(realized or 0):+.2f}** · open系 **{open_n}**",
        "",
        "## 見送り理由トップ",
    ]
    if reasons:
        for k, v in reasons.most_common(12):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- （なし / ログ不足）")
    lines += ["", "## 帳簿イベント（当日または近傍）"]
    if book_ev:
        for k, v in book_ev.most_common(12):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- （帳簿イベントなし）")
    lines += [
        "",
        "## 注意",
        "- 実注文なし（LIVE_TRADING ブロック）。学習用サマリーのみ。",
        "- Discord paper webhook があれば任意投稿。",
        "",
    ]
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    dated = out_path.with_name(f"paper_daily_{day_label}.md")
    dated.write_text(text, encoding="utf-8")
    print(f"paper_daily written {out_path} and {dated} posted={posted} skipped={skipped}")

    if env_bool("PAPER_DAILY_POST_DISCORD", True):
        wh = (
            os.environ.get("DISCORD_PAPER_WEBHOOK_URL")
            or os.environ.get("DISCORD_WEBHOOK_URL")
            or ""
        ).strip()
        if wh:
            try:
                discord_post(
                    wh,
                    embeds=[
                        {
                            "title": f"紙トレード日次 {day_label} JST",
                            "description": (
                                f"投稿 **{posted}** / 見送り **{skipped}** / FU **{followups}**\n"
                                f"勝敗目安 勝{wins}/負{losses} · "
                                f"純資産 ${float(equity or 0):.2f} · "
                                f"実現 ${float(realized or 0):+.2f} · open {open_n}\n"
                                + (
                                    "トップ見送り: "
                                    + ", ".join(f"{k}={v}" for k, v in reasons.most_common(5))
                                    if reasons
                                    else "見送り理由なし"
                                )
                            )[:1900],
                            "color": 0x3498DB,
                            "footer": {"text": "paper daily · LIVE_TRADING=0"},
                        }
                    ],
                )
                print("paper_daily discord ok")
            except Exception as e:
                print(f"paper_daily discord fail: {type(e).__name__}", flush=True)
        else:
            print("paper_daily discord skipped: no webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

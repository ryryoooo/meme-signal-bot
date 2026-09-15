#!/usr/bin/env python3
"""Standalone RH meme overlap signal → Discord webhook. No trading."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Quote / gas placeholders — never treat as meme CA
SKIP_CA = {
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "0x0000000000000000000000000000000000000000",
}
SKIP_SYMBOLS = {
    "ETH", "WETH", "USDC", "USDT", "DAI", "WBTC", "USDG", "USD", "SOL", "BNB", "WBNB",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit(f"missing env {name}")
    return v


def post_json(url: str, payload: dict, headers: dict | None = None, retries: int = 4) -> dict:
    data = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": "meme-discord-bot/1.0"}
    if headers:
        hdrs.update(headers)
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode() or "{}"
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"raw": body, "status": resp.status}
        except urllib.error.HTTPError as e:
            last = e
            wait = int(e.headers.get("Retry-After") or (2 ** i))
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(wait)
                continue
            raise SystemExit(f"HTTP {e.code}: {e.read()[:300]!r}")
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise SystemExit(f"request failed: {last}")


def discord_webhook(url: str, content: str, embeds: list | None = None) -> None:
    payload: dict = {"content": content[:1900]}
    if embeds:
        payload["embeds"] = embeds[:10]
    post_json(url, payload)


def load_watchlist(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        raise SystemExit(f"watchlist missing: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        addr = (o.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        # prefer pass_pnl / realized>0 when available; keep all for v1 breadth
        out[addr] = o
    return out


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"seen_signal_keys": [], "last_poll_ts": None}


def save_state(path: Path, state: dict) -> None:
    # keep last 500 signal keys
    keys = state.get("seen_signal_keys") or []
    state["seen_signal_keys"] = keys[-500:]
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_dex_trades(api_key: str, chain: str, page: int, per_page: int, min_usd: float) -> list[dict]:
    body = {
        "chains": [chain],
        "pagination": {"page": page, "per_page": per_page},
        "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
        "filters": {"trade_value_usd": {"min": min_usd}},
    }
    url = "https://api.nansen.ai/api/v1/smart-money/dex-trades"
    data = post_json(url, body, headers={"apikey": api_key})
    return data.get("data") or []


def parse_ts(s: str) -> float:
    # Nansen timestamps are usually ISO
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def detect_signals(
    trades: list[dict],
    watch: set[str],
    window_sec: int,
    min_wallets: int,
    min_usd: float,
) -> list[dict]:
    """Group BUY-ish trades by token_bought_address within sliding window of watch wallets."""
    # Keep buys: token_bought is the meme CA (sold is usually quote)
    by_token: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        trader = (t.get("trader_address") or "").lower()
        if trader not in watch:
            continue
        ca = (t.get("token_bought_address") or "").lower()
        if not ca.startswith("0x") or ca in SKIP_CA:
            continue
        sym = (t.get("token_bought_symbol") or "").upper().strip()
        if sym in SKIP_SYMBOLS:
            continue
        usd = float(t.get("trade_value_usd") or 0)
        if usd < min_usd:
            continue
        by_token[ca].append(t)

    signals = []
    for ca, rows in by_token.items():
        rows.sort(key=lambda x: parse_ts(x.get("block_timestamp") or ""))
        # sliding: for each trade as first, find distinct wallets within window
        for i, first in enumerate(rows):
            t0 = parse_ts(first.get("block_timestamp") or "")
            wallets: dict[str, dict] = {}
            for j in range(i, len(rows)):
                r = rows[j]
                tj = parse_ts(r.get("block_timestamp") or "")
                if tj - t0 > window_sec:
                    break
                w = (r.get("trader_address") or "").lower()
                if w not in wallets:
                    wallets[w] = r
            if len(wallets) >= min_wallets:
                sig_key = f"{ca}:{','.join(sorted(wallets.keys()))}:{int(t0)}"
                signals.append(
                    {
                        "key": sig_key,
                        "ca": ca,
                        "n": len(wallets),
                        "t0": t0,
                        "elapsed": max(parse_ts(r.get("block_timestamp") or "") for r in wallets.values()) - t0,
                        "wallets": [
                            {
                                "address": w,
                                "label": (wallets[w].get("trader_address_label") or "")[:80],
                                "usd": float(wallets[w].get("trade_value_usd") or 0),
                                "symbol": wallets[w].get("token_bought_symbol"),
                                "ts": wallets[w].get("block_timestamp"),
                            }
                            for w in sorted(wallets.keys())
                        ],
                        "symbol": first.get("token_bought_symbol"),
                        "safety": "unchecked",
                    }
                )
    # dedupe by ca keeping highest n then earliest
    best: dict[str, dict] = {}
    for s in signals:
        prev = best.get(s["ca"])
        if not prev or s["n"] > prev["n"] or (s["n"] == prev["n"] and s["t0"] < prev["t0"]):
            best[s["ca"]] = s
    return list(best.values())


def format_signal(s: dict, chain: str) -> str:
    chain_jp = {
        "robinhood": "Robinhoodチェーン",
        "arc": "Arcチェーン",
        "solana": "Solana",
    }.get(chain, chain)
    sym = s.get("symbol") or "不明"
    n = s["n"]
    # plain language strength
    if n >= 3:
        headline = f"買いが重なった（{n}人）· やや強い"
    else:
        headline = f"買いが重なった（{n}人）"
    elapsed = int(s.get("elapsed") or 0)
    if elapsed < 60:
        when = f"約{elapsed}秒のあいだ"
    else:
        when = f"約{elapsed // 60}分{elapsed % 60}秒のあいだ"
    total_usd = sum(float(w.get("usd") or 0) for w in s["wallets"])
    lines = [
        f"🔔 **{headline}**",
        f"コイン: **${sym}**（{chain_jp}）",
        f"コントラクト:",
        f"`{s['ca']}`",
        f"{when}に監視中の勝ち財布が同じコインを購入",
        f"購入合計の目安: 約 ${total_usd:,.0f}",
        "安全チェック: まだ自動では見ていない（自分で確認）",
        "",
        "誰が買ったか:",
    ]
    for w in s["wallets"]:
        short = w["address"][:6] + "…" + w["address"][-4:]
        lab = (w.get("label") or "").strip()
        name = lab if lab and not lab.startswith("0x") else short
        lines.append(f"• {name} · 約 ${float(w.get('usd') or 0):,.0f}")
    lines.append("")
    lines.append("※お知らせだけです。自動では買いません。")
    return "\n".join(lines)


def run_once(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    api_key = env("NANSEN_API_KEY")
    webhook = env("DISCORD_WEBHOOK_URL")
    chain = os.environ.get("CHAIN", "robinhood")
    window = int(os.environ.get("WINDOW_SECONDS", "900"))
    min_wallets = int(os.environ.get("MIN_WALLETS", "2"))
    min_usd = float(os.environ.get("MIN_TRADE_USD", "50"))
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "../rh-wallets/wallets.jsonl"))).resolve()
    state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json"))).resolve()

    watch = load_watchlist(watch_path)
    watch_set = set(watch.keys())
    state = load_state(state_path)
    seen = set(state.get("seen_signal_keys") or [])

    trades: list[dict] = []
    for page in range(1, args.pages + 1):
        batch = fetch_dex_trades(api_key, chain, page, args.per_page, min_usd)
        if not batch:
            break
        trades.extend(batch)
        time.sleep(0.35)

    signals = detect_signals(trades, watch_set, window, min_wallets, min_usd)
    posted = 0
    for s in signals:
        if s["key"] in seen:
            continue
        discord_webhook(webhook, format_signal(s, chain))
        seen.add(s["key"])
        posted += 1
        print(f"posted {s['ca']} n={s['n']}")
        time.sleep(0.5)

    state["seen_signal_keys"] = list(seen)
    state["last_poll_ts"] = datetime.now(timezone.utc).isoformat()
    state["last_trade_rows"] = len(trades)
    state["watch_size"] = len(watch_set)
    save_state(state_path, state)
    print(f"done trades={len(trades)} signals={len(signals)} posted={posted} watch={len(watch_set)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="poll forever")
    p.add_argument("--test-webhook", action="store_true")
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--per-page", type=int, default=100)
    args = p.parse_args()

    load_dotenv(ROOT / ".env")

    if args.test_webhook:
        url = env("DISCORD_WEBHOOK_URL")
        discord_webhook(url, "✅ 接続OK（通知のみ・自動売買なし）")
        print("test ok")
        return 0

    if args.loop:
        poll = int(os.environ.get("POLL_SECONDS", "60"))
        while True:
            try:
                run_once(args)
            except Exception as e:
                print(f"error: {e}", file=sys.stderr)
            time.sleep(poll)
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())

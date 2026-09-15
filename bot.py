#!/usr/bin/env python3
"""Standalone RH meme overlap signal → Discord webhook. No trading."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
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

# Nansen chain slug → DexScreener slug / GoPlus numeric / explorer base
CHAIN_META = {
    "robinhood": {
        "dex_slug": "robinhood",
        "goplus_id": "4663",
        "explorer": "https://robinhoodchain.blockscout.com/token/",
        "jp": "Robinhoodチェーン",
    },
    "arc": {
        "dex_slug": "arc",
        "goplus_id": None,  # unknown / unsupported
        "explorer": None,
        "jp": "Arcチェーン",
    },
    "solana": {
        "dex_slug": "solana",
        "goplus_id": "solana",
        "explorer": "https://solscan.io/token/",
        "jp": "Solana",
    },
}

LIQ_MCAP_MIN = 0.30
NANSEN_SLEEP = 0.8  # polite pause between Nansen pages


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


def http_get_json(url: str, headers: dict | None = None, timeout: int = 25) -> dict | list | None:
    hdrs = {"User-Agent": "meme-discord-bot/1.1", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode() or "{}"
            return json.loads(body)
    except Exception as e:
        print(f"get_json fail {url.split('?')[0]}: {type(e).__name__}", file=sys.stderr)
        return None


def post_json(url: str, payload: dict, headers: dict | None = None, retries: int = 4) -> dict:
    data = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": "meme-discord-bot/1.1"}
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
            err_body = e.read()[:300]
            raise SystemExit(f"HTTP {e.code}: {err_body!r}")
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise SystemExit(f"request failed: {last}")


def discord_webhook(url: str, content: str = "", embeds: list | None = None) -> None:
    payload: dict = {}
    if content:
        payload["content"] = content[:1900]
    if embeds:
        payload["embeds"] = embeds[:10]
    if not payload:
        payload["content"] = "."
    post_json(url, payload)


def wallet_passes_filter(o: dict, min_realized: float) -> bool:
    if bool(o.get("pass_pnl")):
        return True
    try:
        if float(o.get("realized_pnl_usd") or 0) > min_realized:
            return True
    except (TypeError, ValueError):
        pass
    # gmgn-style field if present
    for key in ("pnl_usd", "gmgn_pnl_usd"):
        if key in o and o[key] is not None:
            try:
                if float(o[key]) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def load_watchlist(path: Path, min_realized: float) -> tuple[dict[str, dict], int, bool]:
    """Return (filtered_map, raw_count, used_fallback)."""
    raw: dict[str, dict] = {}
    if not path.exists():
        raise SystemExit(f"watchlist missing: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        addr = (o.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        raw[addr] = o
    filtered = {a: o for a, o in raw.items() if wallet_passes_filter(o, min_realized)}
    if not filtered:
        print(
            f"WARNING: watchlist filter emptied list (raw={len(raw)}); falling back to all",
            file=sys.stderr,
        )
        return raw, len(raw), True
    return filtered, len(raw), False


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"seen_signal_keys": [], "ca_last_posted": {}, "last_poll_ts": None}


def save_state(path: Path, state: dict) -> None:
    keys = state.get("seen_signal_keys") or []
    state["seen_signal_keys"] = keys[-500:]
    # prune old ca_last_posted entries (> 7d)
    now = time.time()
    ca_map = state.get("ca_last_posted") or {}
    state["ca_last_posted"] = {
        k: v for k, v in ca_map.items() if isinstance(v, (int, float)) and now - float(v) < 7 * 86400
    }
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_paper_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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
                    }
                )
    best: dict[str, dict] = {}
    for s in signals:
        prev = best.get(s["ca"])
        if not prev or s["n"] > prev["n"] or (s["n"] == prev["n"] and s["t0"] < prev["t0"]):
            best[s["ca"]] = s
    return list(best.values())


def _num(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_dexscreener(ca: str, chain: str) -> dict:
    """Fetch DexScreener token pairs; prefer matching chain slug."""
    meta = CHAIN_META.get(chain, {})
    dex_slug = meta.get("dex_slug") or chain
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    data = http_get_json(url)
    pairs = []
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    if not pairs:
        return {"ok": False, "reason": "no_pair", "liq_usd": None, "mcap_usd": None, "fdv": None, "url": None, "pair": None}

    # prefer robinhood (or requested) chainId
    preferred = [p for p in pairs if (p.get("chainId") or "").lower() == dex_slug.lower()]
    pool = preferred or pairs
    # pick highest liquidity
    def liq_of(p):
        return _num((p.get("liquidity") or {}).get("usd")) or 0.0

    pair = max(pool, key=liq_of)
    liq = _num((pair.get("liquidity") or {}).get("usd"))
    mcap = _num(pair.get("marketCap"))
    fdv = _num(pair.get("fdv"))
    pair_url = pair.get("url")
    return {
        "ok": True,
        "reason": None,
        "liq_usd": liq,
        "mcap_usd": mcap,
        "fdv": fdv,
        "url": pair_url,
        "pair": pair.get("pairAddress"),
        "chainId": pair.get("chainId"),
        "symbol": (pair.get("baseToken") or {}).get("symbol"),
    }


def fetch_goplus(ca: str, chain: str) -> dict:
    """Try GoPlus token security. On unsupported/fail → skip (not a hard fail)."""
    meta = CHAIN_META.get(chain, {})
    gid = meta.get("goplus_id")
    if not gid:
        print(f"goplus=skip (no chain id for {chain})")
        return {"status": "skip", "reason": "no_chain_id"}
    url = f"https://api.gopluslabs.io/api/v1/token_security/{gid}?contract_addresses={urllib.parse.quote(ca)}"
    data = http_get_json(url)
    if not isinstance(data, dict):
        print("goplus=skip (request failed)")
        return {"status": "skip", "reason": "request_fail"}
    code = data.get("code")
    result = data.get("result") or {}
    # GoPlus returns result keyed by lowercase address
    info = result.get(ca.lower()) or result.get(ca) or {}
    if code not in (0, 1, "0", "1") or not info:
        # unsupported chain or empty
        print(f"goplus=skip (code={code} empty={not bool(info)})")
        return {"status": "skip", "reason": f"code={code}"}

    is_hp = str(info.get("is_honeypot") or "0") in ("1", "true", "True")
    cannot_sell = str(info.get("cannot_sell_all") or "0") in ("1", "true", "True")
    try:
        buy_tax = float(info.get("buy_tax") or 0)
    except (TypeError, ValueError):
        buy_tax = 0.0
    try:
        sell_tax = float(info.get("sell_tax") or 0)
    except (TypeError, ValueError):
        sell_tax = 0.0
    # high tax: >10% either side (tax often returned as fraction 0-1 or percent)
    if buy_tax > 1:
        buy_tax = buy_tax / 100.0
    if sell_tax > 1:
        sell_tax = sell_tax / 100.0
    high_tax = buy_tax >= 0.10 or sell_tax >= 0.10

    if is_hp or cannot_sell or high_tax:
        reasons = []
        if is_hp:
            reasons.append("honeypot")
        if cannot_sell:
            reasons.append("cannot_sell")
        if high_tax:
            reasons.append(f"high_tax(b={buy_tax:.0%}/s={sell_tax:.0%})")
        return {"status": "fail", "reason": ",".join(reasons), "buy_tax": buy_tax, "sell_tax": sell_tax}
    return {"status": "pass", "reason": None, "buy_tax": buy_tax, "sell_tax": sell_tax}


def safety_check(ca: str, chain: str) -> dict:
    """
    Strict gate:
    - DexScreener liq/mcap (or fdv) >= 0.30; no mcap/fdv → fail no_mcap
    - GoPlus honeypot/cannot_sell/high_tax → fail; unsupported → continue with note
    """
    dex = fetch_dexscreener(ca, chain)
    go = fetch_goplus(ca, chain)

    liq = dex.get("liq_usd")
    mcap = dex.get("mcap_usd")
    fdv = dex.get("fdv")
    denom = mcap if mcap and mcap > 0 else (fdv if fdv and fdv > 0 else None)
    ratio = None
    if liq is not None and denom:
        ratio = liq / denom

    fail_reasons: list[str] = []
    if not dex.get("ok"):
        fail_reasons.append(dex.get("reason") or "no_pair")
    elif denom is None:
        fail_reasons.append("no_mcap")
    elif ratio is None or ratio < LIQ_MCAP_MIN:
        fail_reasons.append(f"liq_ratio={ratio:.2f}" if ratio is not None else "liq_ratio=na")

    if go.get("status") == "fail":
        fail_reasons.append(f"goplus:{go.get('reason')}")

    ok = len(fail_reasons) == 0
    go_note = "goplus=skip" if go.get("status") == "skip" else f"goplus={go.get('status')}"
    print(
        f"safety ca={ca[:10]}… ok={ok} ratio={ratio} {go_note} reasons={fail_reasons or ['ok']}"
    )

    # Japanese short summary for message
    if ok:
        ratio_txt = f"流動性/時価≈{ratio:.0%}" if ratio is not None else "流動性OK"
        jp = f"通過（{ratio_txt}"
        if go.get("status") == "skip":
            jp += "・契約検査は未対応のためスキップ"
        else:
            jp += "・契約検査OK"
        jp += "）"
    else:
        jp_bits = []
        for r in fail_reasons:
            if r == "no_mcap":
                jp_bits.append("時価総額なし")
            elif r == "no_pair":
                jp_bits.append("取引ペアなし")
            elif r.startswith("liq_ratio"):
                jp_bits.append("流動性が薄い")
            elif "honeypot" in r:
                jp_bits.append("売れない疑い")
            elif "cannot_sell" in r:
                jp_bits.append("売却制限")
            elif "high_tax" in r:
                jp_bits.append("手数料が高い")
            else:
                jp_bits.append("検査NG")
        jp = "見送り（" + "・".join(jp_bits) + "）"

    return {
        "ok": ok,
        "reasons": fail_reasons,
        "ratio": ratio,
        "liq_usd": liq,
        "mcap_usd": mcap,
        "fdv": fdv,
        "dex_url": dex.get("url"),
        "goplus": go.get("status"),
        "jp": jp,
        "symbol_hint": dex.get("symbol"),
    }


def strength_label(n: int, total_usd: float) -> tuple[str, int]:
    """Return (Japanese strength phrase, Discord embed color)."""
    if n >= 3 and total_usd >= 500:
        return "かなり強い", 0xE74C3C  # red-ish
    if n >= 3:
        return "やや強い", 0xF39C12  # orange
    return "買いが重なった", 0x3498DB  # blue


def build_embed(s: dict, chain: str, safety: dict) -> dict:
    meta = CHAIN_META.get(chain, {})
    chain_jp = meta.get("jp") or chain
    sym = s.get("symbol") or safety.get("symbol_hint") or "不明"
    n = s["n"]
    total_usd = sum(float(w.get("usd") or 0) for w in s["wallets"])
    strength, color = strength_label(n, total_usd)

    elapsed = int(s.get("elapsed") or 0)
    if elapsed < 60:
        when = f"約{elapsed}秒のあいだ"
    else:
        when = f"約{elapsed // 60}分{elapsed % 60}秒のあいだ"

    title = f"{strength}（{n}人）· ${sym}"
    description = (
        f"{chain_jp}で、{when}に監視中の勝ち財布が同じコインを購入しました。\n"
        f"購入合計の目安: 約 ${total_usd:,.0f}\n"
        f"安全チェック: {safety.get('jp') or '未実施'}"
    )

    fields = [
        {"name": "コントラクト", "value": f"`{s['ca']}`", "inline": False},
    ]

    # links
    dex_slug = meta.get("dex_slug") or chain
    dex_url = safety.get("dex_url") or f"https://dexscreener.com/{dex_slug}/{s['ca']}"
    link_lines = [f"[DexScreener]({dex_url})"]
    explorer_base = meta.get("explorer")
    if explorer_base:
        link_lines.append(f"[エクスプローラー]({explorer_base}{s['ca']})")
    fields.append({"name": "リンク", "value": " · ".join(link_lines), "inline": False})

    who_lines = []
    for w in s["wallets"]:
        short = w["address"][:6] + "…" + w["address"][-4:]
        lab = (w.get("label") or "").strip()
        name = lab if lab and not lab.startswith("0x") else short
        who_lines.append(f"• {name} · 約 ${float(w.get('usd') or 0):,.0f}")
    fields.append({"name": "誰が買ったか", "value": "\n".join(who_lines)[:1000], "inline": False})

    return {
        "title": title[:256],
        "description": description[:4000],
        "color": color,
        "fields": fields,
        "footer": {"text": "お知らせのみ・自動では買いません"},
    }


def run_once(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    api_key = env("NANSEN_API_KEY")
    webhook = env("DISCORD_WEBHOOK_URL")
    chain = os.environ.get("CHAIN", "robinhood").strip().lower()
    window = int(os.environ.get("WINDOW_SECONDS", "900"))
    min_wallets = int(os.environ.get("MIN_WALLETS", "2"))
    min_usd = float(os.environ.get("MIN_TRADE_USD", "50"))
    cooldown = int(os.environ.get("COOLDOWN_SECONDS", "21600"))
    min_realized = float(os.environ.get("WATCH_MIN_REALIZED_USD", "0"))
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets/wallets.jsonl"))).resolve()
    if not watch_path.exists():
        alt = (ROOT / "../rh-wallets/wallets.jsonl").resolve()
        if alt.exists():
            watch_path = alt
    state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json"))).resolve()
    paper_path = Path(os.environ.get("PAPER_LOG_PATH", str(ROOT / "paper_log.jsonl"))).resolve()

    watch, raw_count, fallback = load_watchlist(watch_path, min_realized)
    watch_set = set(watch.keys())
    print(
        f"watchlist raw={raw_count} filtered={len(watch_set)} "
        f"min_realized={min_realized} fallback={fallback}"
    )

    state = load_state(state_path)
    seen = set(state.get("seen_signal_keys") or [])
    ca_last: dict = dict(state.get("ca_last_posted") or {})
    now = time.time()

    trades: list[dict] = []
    nansen_ok = True
    try:
        for page in range(1, args.pages + 1):
            batch = fetch_dex_trades(api_key, chain, page, args.per_page, min_usd)
            if not batch:
                break
            trades.extend(batch)
            if page < args.pages:
                time.sleep(NANSEN_SLEEP)
    except SystemExit as e:
        msg = str(e)
        # Arc / unsupported chain: skip gracefully, don't break RH path
        if chain == "arc" or "unsupported" in msg.lower() or "400" in msg or "422" in msg:
            print(f"Nansen rejected chain={chain}; skipping gracefully: {msg[:200]}")
            nansen_ok = False
            append_paper_log(
                paper_path,
                {"event": "nansen_skip", "chain": chain, "reason": msg[:300], "posted": False},
            )
            state["last_poll_ts"] = datetime.now(timezone.utc).isoformat()
            state["watch_size"] = len(watch_set)
            state["nansen_ok"] = False
            save_state(state_path, state)
            return 0
        raise

    if not nansen_ok:
        return 0

    signals = detect_signals(trades, watch_set, window, min_wallets, min_usd)
    posted = 0
    skipped = 0

    for s in signals:
        ca = s["ca"]
        total_usd = sum(float(w.get("usd") or 0) for w in s["wallets"])
        base_row = {
            "ca": ca,
            "symbol": s.get("symbol"),
            "n": s["n"],
            "total_usd": total_usd,
            "key": s["key"],
            "chain": chain,
        }

        if s["key"] in seen:
            append_paper_log(paper_path, {**base_row, "posted": False, "reason": "already_seen"})
            skipped += 1
            continue

        last_ts = ca_last.get(ca)
        if last_ts is not None and (now - float(last_ts)) < cooldown:
            remain = int(cooldown - (now - float(last_ts)))
            print(f"cooldown skip {ca} remain={remain}s")
            append_paper_log(
                paper_path,
                {**base_row, "posted": False, "reason": f"cooldown_remain={remain}"},
            )
            seen.add(s["key"])
            skipped += 1
            continue

        safety = safety_check(ca, chain)
        s["safety"] = safety
        if not safety["ok"]:
            reason = "safety_fail:" + ",".join(safety.get("reasons") or ["unknown"])
            print(f"skip {ca} {reason}")
            append_paper_log(
                paper_path,
                {
                    **base_row,
                    "posted": False,
                    "reason": reason,
                    "safety_jp": safety.get("jp"),
                    "ratio": safety.get("ratio"),
                    "goplus": safety.get("goplus"),
                },
            )
            seen.add(s["key"])
            skipped += 1
            continue

        embed = build_embed(s, chain, safety)
        discord_webhook(webhook, content="", embeds=[embed])
        seen.add(s["key"])
        ca_last[ca] = now
        posted += 1
        print(f"posted {ca} n={s['n']} total_usd={total_usd:.0f}")
        append_paper_log(
            paper_path,
            {
                **base_row,
                "posted": True,
                "reason": "posted",
                "safety_jp": safety.get("jp"),
                "ratio": safety.get("ratio"),
                "goplus": safety.get("goplus"),
            },
        )
        time.sleep(0.5)

    state["seen_signal_keys"] = list(seen)
    state["ca_last_posted"] = ca_last
    state["last_poll_ts"] = datetime.now(timezone.utc).isoformat()
    state["last_trade_rows"] = len(trades)
    state["watch_size"] = len(watch_set)
    state["watch_raw"] = raw_count
    state["watch_fallback"] = fallback
    state["nansen_ok"] = True
    save_state(state_path, state)
    print(
        f"done trades={len(trades)} signals={len(signals)} posted={posted} "
        f"skipped={skipped} watch={len(watch_set)}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="poll forever")
    p.add_argument("--test-webhook", action="store_true")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--per-page", type=int, default=100)
    args = p.parse_args()

    load_dotenv(ROOT / ".env")

    if args.test_webhook:
        url = env("DISCORD_WEBHOOK_URL")
        discord_webhook(
            url,
            content="",
            embeds=[
                {
                    "title": "接続OK",
                    "description": "通知のみ・自動売買なし",
                    "color": 0x2ECC71,
                }
            ],
        )
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

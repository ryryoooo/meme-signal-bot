#!/usr/bin/env python3
"""Solana smart-wallet buy detector → Discord (dedicated channel only).

Merges watchlists (NOT stonkfun_diggers, NOT RH) and polls free Solana RPC for
buys by watched wallets on any mint. Posts ONLY to DISCORD_SOL_SMART_WEBHOOK_URL
— never falls back to DISCORD_WEBHOOK_URL or DISCORD_STONKFUN_WEBHOOK_URL.

Watchlists (merge/dedupe, tag source):
  sol-wallets/sol_smart_7d_active.jsonl
  sol-wallets/sol_dump_dip_smart.jsonl
  sol-wallets/sol_smart_similar_74pB.jsonl
  sol-wallets/watch_candidates_sol.jsonl

Env:
  DISCORD_SOL_SMART_WEBHOOK_URL  required for posts (skip notify if empty)
  SOLANA_RPC_URL                 default https://solana-rpc.publicnode.com
  SOL_SMART_SIGNAL_STATE         default sol-wallets/raw/sol_smart_signal_state.json
  SOL_SMART_POLL_SECONDS         default 30
  SOL_SMART_MIN_WALLETS          default 1
  SOL_SMART_SIG_LIMIT            per-wallet sigs per tick (default 8)
  SOL_SMART_COOLDOWN_SECONDS     per (mint,wallet) cooldown (default 600)
  SOL_SMART_SIGNAL_TEST=1        post one テスト embed
  SOL_SMART_TICK_ONCE=1          one scan then exit
  SOL_SMART_ALERT_MAX_AGE_H      open_alerts follow-up window (default 24h)
  LIVE_TRADING=0 / no box GMGN
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")
os.environ.setdefault("GMGN_SMARTMONEY", "0")
os.environ.setdefault("GMGN_MARKET", "0")

from load_secrets import load as load_secrets  # noqa: E402
import gmgn_token as gmgn_tok  # noqa: E402

JST = timezone(timedelta(hours=9))
RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
STATE_PATH = Path(
    os.environ.get("SOL_SMART_SIGNAL_STATE")
    or str(ROOT / "sol-wallets" / "raw" / "sol_smart_signal_state.json")
)
WATCHLIST_SPECS = [
    ("sol_smart_7d_active", ROOT / "sol-wallets" / "sol_smart_7d_active.jsonl"),
    ("sol_dump_dip_smart", ROOT / "sol-wallets" / "sol_dump_dip_smart.jsonl"),
    ("sol_smart_similar_74pB", ROOT / "sol-wallets" / "sol_smart_similar_74pB.jsonl"),
    ("watch_candidates_sol", ROOT / "sol-wallets" / "watch_candidates_sol.jsonl"),
]
UA = os.environ.get(
    "SOL_SMART_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/sol-smart-signal; +https://github.com/ryryoooo/meme-signal-bot)",
)

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "USD1ttGY1N17NEE4GMPE8VGRJWKSUJUJmKnhKm8Qj7",
}

MULTIPLIER_MILESTONES = (1.5, 2.0, 3.0, 5.0)
ALERT_MAX_AGE_DEFAULT_H = 24


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} sol_smart_signal {msg}", flush=True)


def jst_label(ts: float | None = None, *, seconds: bool = False) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), JST)
    if seconds:
        return dt.strftime("%Y-%m-%d %H:%M:%S JST")
    return dt.strftime("%Y-%m-%d %H:%M JST")


def notify_stamp(footer_base: str = "sol_smart_signal") -> tuple[str, dict, str]:
    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    ts = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    jst = now_jst.strftime("%Y-%m-%d %H:%M JST")
    base = (footer_base or "").strip()
    footer = {"text": (f"{base} · 投稿 {jst}" if base else f"投稿 {jst}")[:2048]}
    return ts, footer, jst


def fmt_usd(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"${n:,.0f}"
    if n >= 1:
        return f"${n:.2f}"
    return f"${n:.4f}"


def fmt_price(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1:
        return f"${n:.4f}"
    if n >= 1e-4:
        return f"${n:.6f}"
    return f"${n:.2e}"


def fmt_mult(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 10:
        return f"{n:.1f}x"
    return f"{n:.2f}x"


def _num(v) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n <= 0:
        return None
    return n


def short_addr(a: str, n: int = 4) -> str:
    a = a or ""
    if len(a) <= n * 2 + 1:
        return a
    return f"{a[:n]}…{a[-n:]}"


def resolve_sol_smart_webhook() -> str:
    """ONLY DISCORD_SOL_SMART_WEBHOOK_URL — never RH or StonkFun."""
    load_secrets(["DISCORD_SOL_SMART_WEBHOOK_URL"])
    return (os.environ.get("DISCORD_SOL_SMART_WEBHOOK_URL") or "").strip()


def _extract_json_blob(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    content = raw
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict) and "data" in outer:
            c = outer["data"]
            if isinstance(c, dict):
                c = c.get("content") or c.get("text") or ""
            if isinstance(c, str):
                content = c
    except json.JSONDecodeError:
        content = raw
    content = re.sub(r"^```\w*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content)
    for pat in (r"\{[\s\S]*\"pairs\"[\s\S]*\}", r"\{[\s\S]*\"data\"[\s\S]*\}", r"\{[\s\S]*\}"):
        m = re.search(pat, content)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    return None


def http_get_text(url: str, timeout: int = 25) -> str | None:
    headers = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), "-A", UA, url],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    return None


def rpc(method: str, params: list, timeout: int = 40) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    last: Exception | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                RPC_URL,
                data=payload.encode(),
                headers={"content-type": "application/json", "User-Agent": UA},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode() or "{}")
            if body.get("error"):
                raise RuntimeError(body["error"])
            return body.get("result")
        except Exception as e:
            last = e
            time.sleep(0.3 * (1.7**attempt))
    r = subprocess.run(
        [
            "curl", "-sS", "-m", str(timeout), "-A", UA, "-X", "POST", RPC_URL,
            "-H", "content-type: application/json", "-d", payload,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        body = json.loads(r.stdout)
        if body.get("error"):
            raise RuntimeError(body["error"])
        return body.get("result")
    raise RuntimeError(f"RPC {method} failed: {last}")


def rpc_batch(calls: list[tuple[str, list]], timeout: int = 55) -> list:
    if not calls:
        return []
    if len(calls) == 1:
        try:
            return [rpc(calls[0][0], calls[0][1], timeout=timeout)]
        except Exception:
            return [None]
    payload = json.dumps(
        [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    )
    try:
        req = urllib.request.Request(
            RPC_URL,
            data=payload.encode(),
            headers={"content-type": "application/json", "User-Agent": UA},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode() or "[]")
        if isinstance(body, dict):
            return [body.get("result")]
        out: list = [None] * len(calls)
        for item in body:
            idx = item.get("id")
            if isinstance(idx, int) and 0 <= idx < len(out) and not item.get("error"):
                out[idx] = item.get("result")
        return out
    except Exception as e:
        log(f"batch fail ({type(e).__name__}) → sequential")
        out = []
        for m, p in calls:
            try:
                out.append(rpc(m, p, timeout=min(35, timeout)))
            except Exception:
                out.append(None)
            time.sleep(0.04)
        return out


def discord_post(webhook: str, embeds: list[dict], content: str = "") -> bool:
    if not webhook:
        return False
    payload: dict = {"embeds": embeds[:10]}
    if content:
        payload["content"] = content[:1900]
    data = json.dumps(payload).encode()
    url = webhook if "wait=" in webhook else (
        webhook + ("&" if "?" in webhook else "?") + "wait=true"
    )
    req = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            resp.read()
        return 200 <= getattr(resp, "status", 200) < 300
    except urllib.error.HTTPError as e:
        log(f"discord HTTP {e.code}")
        return False
    except Exception as e:
        log(f"discord err {type(e).__name__}")
        return False


def load_watch_wallets() -> dict[str, dict]:
    """Merge/dedupe addresses across watchlists; tag sources. Skip diggers."""
    out: dict[str, dict] = {}
    for src, path in WATCHLIST_SPECS:
        if not path.exists():
            log(f"watchlist missing: {path.name}")
            continue
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            addr = (row.get("address") or "").strip()
            if not addr:
                continue
            n += 1
            if addr not in out:
                out[addr] = {
                    "address": addr,
                    "sources": [src],
                    "score": row.get("score"),
                    "hit_mints": row.get("n_cas") or row.get("n_mints") or row.get("n_shared"),
                    "tags": list(row.get("tags") or []),
                    "symbol_hint": (row.get("symbols") or [None])[0] if isinstance(row.get("symbols"), list) else None,
                }
            else:
                if src not in out[addr]["sources"]:
                    out[addr]["sources"].append(src)
                sc = row.get("score")
                if sc is not None and (
                    out[addr].get("score") is None or float(sc or 0) > float(out[addr].get("score") or 0)
                ):
                    out[addr]["score"] = sc
                for t in row.get("tags") or []:
                    if t not in out[addr]["tags"]:
                        out[addr]["tags"].append(t)
        log(f"watchlist {src}: {n} rows")
    return out


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "wallet_cursors": {},
        "seen": {},
        "mint_cache": {},
        "pending": {},
        "posted": {},
        "open_alerts": [],
        "test_sent": False,
        "stats": {"ticks": 0, "posts": 0, "skips_no_webhook": 0, "followups": 0},
    }


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for key in ("seen", "posted"):
        bucket = st.get(key) or {}
        st[key] = {k: v for k, v in bucket.items() if now - float(v or 0) < 86400 * 3}
    pending = st.get("pending") or {}
    st["pending"] = {
        k: v
        for k, v in pending.items()
        if now - float((v or {}).get("first_ts") or 0) < 3600
    }
    mint_cache = st.get("mint_cache") or {}
    st["mint_cache"] = {
        k: v
        for k, v in mint_cache.items()
        if now - float((v or {}).get("at") or 0) < 3600 * 6
    }
    max_age = env_int("SOL_SMART_ALERT_MAX_AGE_H", ALERT_MAX_AGE_DEFAULT_H) * 3600
    alerts = st.get("open_alerts") or []
    kept = []
    for a in alerts:
        try:
            age = now - float((a or {}).get("posted_at") or 0)
        except (TypeError, ValueError):
            continue
        if age <= max_age + 3600:
            kept.append(a)
    st["open_alerts"] = kept[-200:]
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_token_meta(mint: str, cache: dict) -> dict:
    """Dex/Gecko via jina (free; no GMGN). Cache positives + negatives."""
    now = time.time()
    hit = cache.get(mint)
    if hit and now - float(hit.get("at") or 0) < 1800:
        return hit.get("token") or {"mint": mint, "symbol": "?", "name": ""}

    meta = {"mint": mint, "symbol": "?", "name": "", "priceUsd": None, "marketCapUsd": None, "source": None}

    raw = http_get_text(f"https://r.jina.ai/http://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=28)
    data = _extract_json_blob(raw or "")
    pairs = (data or {}).get("pairs") or [] if isinstance(data, dict) else []
    if pairs:
        sol = [p for p in pairs if (p.get("chainId") or "").lower() in ("solana", "sol")]
        pool = sol or pairs
        mint_l = mint.lower()

        def score(p):
            base = ((p.get("baseToken") or {}).get("address") or "").lower()
            is_base = 1 if base == mint_l else 0
            try:
                liq = float(((p.get("liquidity") or {}).get("usd") or 0) or 0)
            except (TypeError, ValueError):
                liq = 0.0
            return (is_base, liq)

        best = max(pool, key=score)
        base = best.get("baseToken") or {}
        quote = best.get("quoteToken") or {}
        if (base.get("address") or "").lower() == mint_l:
            meta["symbol"] = base.get("symbol") or "?"
            meta["name"] = base.get("name") or ""
        elif (quote.get("address") or "").lower() == mint_l:
            meta["symbol"] = quote.get("symbol") or "?"
            meta["name"] = quote.get("name") or ""
        else:
            meta["symbol"] = base.get("symbol") or "?"
            meta["name"] = base.get("name") or ""
        meta["priceUsd"] = _num(best.get("priceUsd"))
        meta["marketCapUsd"] = _num(best.get("marketCap") or best.get("fdv"))
        meta["source"] = "dexscreener_via_jina"
        cache[mint] = {"at": now, "token": meta}
        return meta

    raw = http_get_text(
        f"https://r.jina.ai/http://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}",
        timeout=28,
    )
    data = _extract_json_blob(raw or "")
    attrs = ((data or {}).get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if isinstance(attrs, dict):
        meta["symbol"] = attrs.get("symbol") or attrs.get("name") or "?"
        meta["name"] = attrs.get("name") or ""
        meta["priceUsd"] = _num(attrs.get("price_usd"))
        meta["marketCapUsd"] = _num(attrs.get("fdv_usd") or attrs.get("market_cap_usd"))
        meta["source"] = "gecko_via_jina"
    cache[mint] = {"at": now, "token": meta}
    return meta


def fetch_price_snapshot(mint: str) -> dict:
    out: dict = {"price_usd": None, "mcap_usd": None, "source": None}
    raw = http_get_text(f"https://r.jina.ai/http://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=28)
    data = _extract_json_blob(raw or "")
    pairs = (data or {}).get("pairs") or [] if isinstance(data, dict) else []
    if pairs:
        sol = [p for p in pairs if (p.get("chainId") or "").lower() in ("solana", "sol")]
        pool = sol or pairs
        mint_l = mint.lower()

        def score(p):
            base = ((p.get("baseToken") or {}).get("address") or "").lower()
            is_base = 1 if base == mint_l else 0
            try:
                liq = float(((p.get("liquidity") or {}).get("usd") or 0) or 0)
            except (TypeError, ValueError):
                liq = 0.0
            return (is_base, liq)

        best = max(pool, key=score)
        out["price_usd"] = _num(best.get("priceUsd"))
        out["mcap_usd"] = _num(best.get("marketCap") or best.get("fdv"))
        out["source"] = "dexscreener_via_jina"
        if out["price_usd"]:
            return out
    raw = http_get_text(
        f"https://r.jina.ai/http://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}",
        timeout=28,
    )
    data = _extract_json_blob(raw or "")
    attrs = ((data or {}).get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if isinstance(attrs, dict):
        out["price_usd"] = out["price_usd"] or _num(attrs.get("price_usd"))
        out["mcap_usd"] = out["mcap_usd"] or _num(attrs.get("fdv_usd") or attrs.get("market_cap_usd"))
        out["source"] = out["source"] or "gecko_via_jina"
    return out


def compute_mult(alert_price, now_price, alert_mcap=None, now_mcap=None) -> float | None:
    ap, np_ = _num(alert_price), _num(now_price)
    if ap and np_:
        return np_ / ap
    am, nm = _num(alert_mcap), _num(now_mcap)
    if am and nm:
        return nm / am
    return None


def _tb_amount(tb: dict) -> float:
    ui = tb.get("uiTokenAmount") or {}
    try:
        if ui.get("uiAmount") is not None:
            return float(ui["uiAmount"])
        return float(ui.get("uiAmountString") or 0)
    except (TypeError, ValueError):
        return 0.0


def buys_from_tx(res: dict, wallet: str) -> list[tuple[str, float]]:
    """Return [(mint, delta)] where wallet gained SPL tokens (any swap)."""
    if not res:
        return []
    meta = res.get("meta") or {}
    if meta.get("err"):
        return []
    pre = {
        (tb.get("owner"), tb.get("mint")): _tb_amount(tb)
        for tb in (meta.get("preTokenBalances") or [])
    }
    post = {
        (tb.get("owner"), tb.get("mint")): _tb_amount(tb)
        for tb in (meta.get("postTokenBalances") or [])
    }
    mints = {k[1] for k in list(pre) + list(post) if k[0] == wallet and k[1]}
    out: list[tuple[str, float]] = []
    for mint in mints:
        if mint in SKIP_MINTS:
            continue
        dlt = post.get((wallet, mint), 0.0) - pre.get((wallet, mint), 0.0)
        if dlt > 0:
            out.append((mint, dlt))
    out.sort(key=lambda x: -x[1])
    return out


def build_embed(
    *,
    token: dict,
    wallets: list[dict],
    sig: str,
    block_time: int | None,
    alert_price: float | None = None,
    alert_mcap: float | None = None,
    mult: float | None = None,
    test: bool = False,
) -> dict:
    sym = token.get("symbol") or "?"
    mint = token.get("mint") or ""
    n_w = len(wallets)
    title_prefix = "【テスト】" if test else "【SolSmart】"
    srcs = sorted({s for w in wallets for s in (w.get("sources") or [])})
    src_s = ",".join(srcs[:3]) if srcs else "—"
    title = f"{title_prefix} ${sym} · smart×{n_w} · {src_s}"

    w_lines = []
    for w in wallets[:5]:
        addr = w.get("address") or ""
        src = ",".join((w.get("sources") or [])[:2]) or "?"
        hits = w.get("hit_mints", "?")
        w_lines.append(f"`{short_addr(addr)}` · {src} · hits {hits}")
    if len(wallets) > 5:
        w_lines.append(f"…他 {len(wallets) - 5} 件")

    px = _num(alert_price) or _num(token.get("priceUsd"))
    mc = _num(alert_mcap) or _num(token.get("marketCapUsd"))
    price_mc = f"{fmt_price(px)} / MC {fmt_usd(mc)}" if (px or mc) else "—"
    mult_s = fmt_mult(mult) if mult is not None else "—"

    gmgn_url = gmgn_tok.token_app_url("sol", mint)
    ts_iso, footer, jst = notify_stamp("sol_smart_signal" + (" · test" if test else ""))
    buy_jst = jst_label(float(block_time), seconds=False) if block_time else None
    notify_val = f"**{jst}**"
    if buy_jst and buy_jst != jst:
        notify_val += f"\n(約定 {buy_jst})"

    fields = [
        {"name": "通知時刻", "value": notify_val, "inline": True},
        {"name": "価格 / MC（通知時）", "value": price_mc, "inline": True},
        {"name": "倍率", "value": f"**{mult_s}**" if mult_s != "—" else "—", "inline": True},
        {
            "name": f"Smart wallets（{n_w}）",
            "value": "\n".join(w_lines) or "—",
            "inline": False,
        },
        {
            "name": "GMGNアプリで開く",
            "value": f"[開く]({gmgn_url})",
            "inline": False,
        },
    ]
    color = 0xF1C40F if test else 0x2ECC71
    desc = "配線テスト投稿です。" if test else f"Solana smart wallet が ${sym} を購入。"
    return {
        "title": title[:256],
        "description": desc[:4000],
        "color": color,
        "fields": fields,
        "footer": footer,
        "timestamp": ts_iso,
    }


def build_mult_followup_embed(alert: dict, mult: float, now_snap: dict, milestone: float) -> dict:
    sym = alert.get("symbol") or "?"
    mint = alert.get("mint") or alert.get("ca") or ""
    gmgn_url = gmgn_tok.token_app_url("sol", mint)
    ts_iso, footer, jst = notify_stamp("sol_smart_signal · 倍率")
    alert_px = _num(alert.get("alert_price_usd"))
    alert_mc = _num(alert.get("alert_mcap"))
    now_px = _num(now_snap.get("price_usd"))
    now_mc = _num(now_snap.get("mcap_usd"))
    fields = [
        {"name": "通知時刻", "value": f"**{alert.get('alert_jst') or '—'}**", "inline": True},
        {
            "name": "通知時点からの倍率",
            "value": f"**{fmt_mult(mult)}**（マイルストーン {milestone:g}x）",
            "inline": True,
        },
        {
            "name": "価格（通知 → 現在）",
            "value": f"{fmt_price(alert_px)} → {fmt_price(now_px)}",
            "inline": False,
        },
        {
            "name": "MC（通知 → 現在）",
            "value": f"{fmt_usd(alert_mc)} → {fmt_usd(now_mc)}",
            "inline": False,
        },
        {
            "name": "GMGNアプリで開く",
            "value": f"[開く]({gmgn_url})",
            "inline": False,
        },
    ]
    return {
        "title": f"【倍率】{sym} {milestone:g}x"[:256],
        "description": f"通知時点から約 **{fmt_mult(mult)}** です。",
        "color": 0x9B59B6,
        "fields": fields,
        "footer": footer,
        "timestamp": ts_iso,
    }


def post_test_embed(webhook: str) -> bool:
    demo_mint = "So11111111111111111111111111111111111111112"
    demo_token = {
        "mint": demo_mint,
        "symbol": "TEST",
        "priceUsd": 0.00012,
        "marketCapUsd": 12000,
    }
    embed = build_embed(
        token=demo_token,
        wallets=[
            {
                "address": "Smart1111111111111111111111111111111111111",
                "sources": ["sol_smart_7d_active"],
                "hit_mints": 3,
            },
            {
                "address": "Smart2222222222222222222222222222222222222",
                "sources": ["sol_dump_dip_smart", "sol_smart_similar_74pB"],
                "hit_mints": 2,
            },
        ],
        sig="test",
        block_time=None,
        alert_price=0.00012,
        alert_mcap=12000,
        mult=1.0,
        test=True,
    )
    embed["description"] = (
        "**テスト投稿** — DISCORD_SOL_SMART_WEBHOOK_URL 配線確認。\n"
        "新チャンネル: Solana smart wallets（7d / dump-dip / similar74pB / candidates）。\n"
        "RH・StonkFun webhook には送りません。"
    )
    ok = discord_post(webhook, [embed], content="")
    _, _, jst = notify_stamp("sol_smart_signal · test")
    log(f"test embed {'ok' if ok else 'FAIL'} jst={jst}")
    return ok


def process_multiplier_followups(st: dict, webhook: str) -> int:
    if not webhook:
        return 0
    now = time.time()
    max_age = env_int("SOL_SMART_ALERT_MAX_AGE_H", ALERT_MAX_AGE_DEFAULT_H) * 3600
    alerts = list(st.get("open_alerts") or [])
    posted = 0
    for alert in alerts:
        try:
            posted_at = float(alert.get("posted_at") or 0)
        except (TypeError, ValueError):
            continue
        if now - posted_at > max_age:
            continue
        mint = alert.get("mint") or alert.get("ca")
        if not mint:
            continue
        alert_price = _num(alert.get("alert_price_usd"))
        alert_mcap = _num(alert.get("alert_mcap"))
        if not alert_price and not alert_mcap:
            continue
        last_fu = float(alert.get("last_followup_at") or 0)
        if last_fu and now - last_fu < 45:
            continue
        snap = fetch_price_snapshot(mint)
        mult = compute_mult(alert_price, snap.get("price_usd"), alert_mcap, snap.get("mcap_usd"))
        alert["last_followup_at"] = now
        if mult is None:
            continue
        alert["last_followup_mult"] = mult
        alert["last_price_usd"] = snap.get("price_usd")
        alert["last_mcap"] = snap.get("mcap_usd")
        hit = list(alert.get("milestones_hit") or [])
        next_ms = None
        for ms in MULTIPLIER_MILESTONES:
            if mult >= ms and ms not in hit:
                next_ms = ms
                break
        if next_ms is None:
            continue
        embed = build_mult_followup_embed(alert, mult, snap, next_ms)
        if discord_post(webhook, [embed]):
            hit.append(next_ms)
            alert["milestones_hit"] = hit
            posted += 1
            st.setdefault("stats", {})["followups"] = int(
                (st.get("stats") or {}).get("followups") or 0
            ) + 1
            log(
                f"FOLLOWUP {alert.get('symbol')} mint={short_addr(mint, 6)} "
                f"mult={mult:.2f} milestone={next_ms:g}x"
            )
            time.sleep(0.35)
    st["open_alerts"] = alerts
    return posted


def maybe_notify(
    *,
    webhook: str,
    st: dict,
    mint: str,
    token: dict,
    wallet_row: dict,
    sig: str,
    block_time: int | None,
    min_wallets: int,
    cooldown: int,
) -> bool:
    wallet = wallet_row["address"]
    seen_key = f"{mint}:{wallet}:{sig}"
    if seen_key in (st.get("seen") or {}):
        return False
    st.setdefault("seen", {})[seen_key] = time.time()

    cool_key = f"{mint}:{wallet}"
    last = float((st.get("posted") or {}).get(cool_key) or 0)
    if time.time() - last < cooldown:
        return False

    pending = st.setdefault("pending", {})
    entry = pending.get(mint) or {
        "first_ts": time.time(),
        "wallets": {},
        "sig": sig,
        "block_time": block_time,
    }
    entry["wallets"][wallet] = {
        "address": wallet,
        "hit_mints": wallet_row.get("hit_mints"),
        "score": wallet_row.get("score"),
        "sources": list(wallet_row.get("sources") or []),
        "sig": sig,
        "block_time": block_time,
    }
    entry["sig"] = sig
    entry["block_time"] = block_time or entry.get("block_time")
    entry["token"] = token
    pending[mint] = entry

    wallets = list(entry["wallets"].values())
    if len(wallets) < min_wallets:
        log(f"pending mint={short_addr(mint, 6)} wallets={len(wallets)}/{min_wallets}")
        return False

    if not webhook:
        st.setdefault("stats", {})["skips_no_webhook"] = int(
            (st.get("stats") or {}).get("skips_no_webhook") or 0
        ) + 1
        log("SKIP post — DISCORD_SOL_SMART_WEBHOOK_URL empty (no RH/StonkFun fallback)")
        return False

    alert_price = _num(token.get("priceUsd"))
    alert_mcap = _num(token.get("marketCapUsd"))
    if alert_price is None and alert_mcap is None:
        snap0 = fetch_price_snapshot(mint)
        alert_price = _num(snap0.get("price_usd"))
        alert_mcap = _num(snap0.get("mcap_usd"))
        if alert_price:
            token["priceUsd"] = alert_price
        if alert_mcap:
            token["marketCapUsd"] = alert_mcap

    mult = 1.0 if (alert_price or alert_mcap) else None
    embed = build_embed(
        token=token,
        wallets=wallets,
        sig=entry.get("sig") or sig,
        block_time=entry.get("block_time") or block_time,
        alert_price=alert_price,
        alert_mcap=alert_mcap,
        mult=mult,
    )
    ok = discord_post(webhook, [embed])
    if ok:
        now = time.time()
        for w in wallets:
            st.setdefault("posted", {})[f"{mint}:{w['address']}"] = now
        pending.pop(mint, None)
        st.setdefault("stats", {})["posts"] = int((st.get("stats") or {}).get("posts") or 0) + 1
        _, _, jst = notify_stamp("sol_smart_signal")
        open_alerts = [a for a in (st.get("open_alerts") or []) if (a.get("mint") or "") != mint]
        open_alerts.append(
            {
                "mint": mint,
                "ca": mint,
                "symbol": token.get("symbol"),
                "alert_price_usd": alert_price,
                "alert_mcap": alert_mcap,
                "alert_jst": jst,
                "posted_at": now,
                "milestones_hit": [],
                "wallet_count": len(wallets),
                "sig": entry.get("sig") or sig,
            }
        )
        st["open_alerts"] = open_alerts[-200:]
        log(
            f"POSTED {token.get('symbol')} mint={short_addr(mint, 6)} "
            f"wallets={len(wallets)} px={alert_price} mc={alert_mcap} webhook=dedicated"
        )
    return ok


def scan_once(st: dict, wallets: dict[str, dict], webhook: str) -> int:
    min_wallets = max(1, env_int("SOL_SMART_MIN_WALLETS", 1))
    sig_limit = env_int("SOL_SMART_SIG_LIMIT", 8)
    cooldown = env_int("SOL_SMART_COOLDOWN_SECONDS", 600)
    boot = not bool(st.get("wallet_cursors"))

    log(f"wallets={len(wallets)} webhook={'dedicated' if webhook else 'MISSING'} boot={boot}")

    found = 0
    cursors = st.setdefault("wallet_cursors", {})
    mint_cache = st.setdefault("mint_cache", {})

    addrs = list(wallets.keys())
    for i in range(0, len(addrs), 6):
        chunk = addrs[i : i + 6]
        calls = [("getSignaturesForAddress", [a, {"limit": sig_limit}]) for a in chunk]
        results = rpc_batch(calls)
        for addr, sigs in zip(chunk, results):
            sigs = sigs or []
            if not isinstance(sigs, list):
                continue
            if boot:
                if sigs:
                    cursors[addr] = sigs[0].get("signature")
                continue
            last = cursors.get(addr)
            new_sigs = []
            for s in sigs:
                if s.get("err") is not None:
                    continue
                sig = s.get("signature")
                if not sig:
                    continue
                if last and sig == last:
                    break
                new_sigs.append(s)
            if sigs:
                cursors[addr] = sigs[0].get("signature")
            if not new_sigs:
                continue
            new_sigs = list(reversed(new_sigs[:sig_limit]))
            for s in new_sigs:
                sig = s["signature"]
                try:
                    tx = rpc(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}],
                        timeout=45,
                    )
                except Exception as e:
                    log(f"tx fail {short_addr(sig, 6)} {type(e).__name__}")
                    continue
                gains = buys_from_tx(tx or {}, addr)
                if not gains:
                    continue
                for mint, _amt in gains[:3]:
                    token = fetch_token_meta(mint, mint_cache)
                    if maybe_notify(
                        webhook=webhook,
                        st=st,
                        mint=mint,
                        token=token,
                        wallet_row=wallets[addr],
                        sig=sig,
                        block_time=s.get("blockTime"),
                        min_wallets=min_wallets,
                        cooldown=cooldown,
                    ):
                        found += 1
                time.sleep(0.05)
        time.sleep(0.08)

    if boot:
        log("boot: cursors seeded — skip historical")

    try:
        fu = process_multiplier_followups(st, webhook)
        if fu:
            log(f"followups={fu}")
    except Exception as e:
        log(f"followup err {type(e).__name__}: {e}")

    st.setdefault("stats", {})["ticks"] = int((st.get("stats") or {}).get("ticks") or 0) + 1
    st["last_tick_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(st)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Sol smart wallets → Discord (dedicated webhook)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true", help="post テスト embed")
    args = ap.parse_args()

    webhook = resolve_sol_smart_webhook()
    wallets = load_watch_wallets()
    st = load_state()
    poll = env_int("SOL_SMART_POLL_SECONDS", 30)
    once = args.once or os.environ.get("SOL_SMART_TICK_ONCE") == "1"
    do_test = args.test or os.environ.get("SOL_SMART_SIGNAL_TEST") == "1"

    log(
        f"start wallets={len(wallets)} poll={poll}s "
        f"webhook={'dedicated_ok' if webhook else 'EMPTY_skip_posts'} "
        f"min_wallets={env_int('SOL_SMART_MIN_WALLETS', 1)} live=0"
    )

    if do_test:
        if not webhook:
            log("TEST skipped — DISCORD_SOL_SMART_WEBHOOK_URL empty (no RH/StonkFun fallback)")
        elif not st.get("test_sent") or args.test or os.environ.get("SOL_SMART_SIGNAL_TEST") == "1":
            if post_test_embed(webhook):
                st["test_sent"] = True
                save_state(st)

    if once:
        scan_once(st, wallets, webhook)
        return 0

    while True:
        wallets = load_watch_wallets() or wallets
        webhook = resolve_sol_smart_webhook()
        try:
            n = scan_once(st, wallets, webhook)
            if n:
                log(f"tick posts={n}")
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
            save_state(st)
        time.sleep(max(10, poll))


if __name__ == "__main__":
    raise SystemExit(main())

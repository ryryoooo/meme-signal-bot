#!/usr/bin/env python3
"""StonkFun digger buy detector → Discord (dedicated channel only).

Polls free Solana RPC for digger wallet signatures + StonkFun public API for
active launches. When a digger buys a StonkFun mint, posts an embed to
DISCORD_STONKFUN_WEBHOOK_URL only — NEVER falls back to DISCORD_WEBHOOK_URL
(RH smart-money channel).

Env:
  DISCORD_STONKFUN_WEBHOOK_URL  required for posts (skip notify if empty)
  SOLANA_RPC_URL                default https://solana-rpc.publicnode.com
  STONKFUN_API                  default https://www.stonkfun.xyz/api/public/v1
  STONKFUN_DIGGERS_PATH         default sol-wallets/stonkfun_diggers.jsonl
  STONKFUN_SIGNAL_STATE         default sol-wallets/raw/stonkfun_signal_state.json
  STONKFUN_POLL_SECONDS         default 20
  STONKFUN_MIN_DIGGERS          default 1
  STONKFUN_SIG_LIMIT            per-wallet sigs per tick (default 12)
  STONKFUN_COOLDOWN_SECONDS     per (mint,wallet) cooldown (default 600)
  STONKFUN_SIGNAL_TEST=1        post one テスト embed then continue/exit
  STONKFUN_TICK_ONCE=1          one scan then exit
  LIVE_TRADING=0 / no box GMGN
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
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
SF_API = (os.environ.get("STONKFUN_API") or "https://www.stonkfun.xyz/api/public/v1").rstrip("/")
RPC_URL = (os.environ.get("SOLANA_RPC_URL") or "https://solana-rpc.publicnode.com").strip()
DIGGERS_PATH = Path(
    os.environ.get("STONKFUN_DIGGERS_PATH") or str(ROOT / "sol-wallets" / "stonkfun_diggers.jsonl")
)
STATE_PATH = Path(
    os.environ.get("STONKFUN_SIGNAL_STATE")
    or str(ROOT / "sol-wallets" / "raw" / "stonkfun_signal_state.json")
)
UA = os.environ.get(
    "STONK_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/stonkfun-signal; +https://github.com/ryryoooo/meme-signal-bot)",
)

# Quote / system mints — never the "bought" token
SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USD1ttGY1N17NEE4GMPE8VGRJWKSUJUJmKnhKm8Qj7",
}

SKIP_OWNERS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
}


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} stonkfun_signal {msg}", flush=True)


def jst_label(ts: float | None = None, *, seconds: bool = False) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), JST)
    if seconds:
        return dt.strftime("%Y-%m-%d %H:%M:%S JST")
    return dt.strftime("%Y-%m-%d %H:%M JST")


def notify_stamp(footer_base: str = "stonkfun_signal") -> tuple[str, dict, str]:
    """UTC ISO for embed.timestamp + JST wall-time for field/footer (RH-style)."""
    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    ts = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    jst = now_jst.strftime("%Y-%m-%d %H:%M JST")
    base = (footer_base or "").strip()
    footer = {"text": (f"{base} · 投稿 {jst}" if base else f"投稿 {jst}")[:2048]}
    return ts, footer, jst


def short_addr(a: str, n: int = 4) -> str:
    a = a or ""
    if len(a) <= n * 2 + 1:
        return a
    return f"{a[:n]}…{a[-n:]}"


def resolve_stonkfun_webhook() -> str:
    """ONLY DISCORD_STONKFUN_WEBHOOK_URL — never RH DISCORD_WEBHOOK_URL."""
    load_secrets(["DISCORD_STONKFUN_WEBHOOK_URL"])
    return (os.environ.get("DISCORD_STONKFUN_WEBHOOK_URL") or "").strip()


def http_get_json(url: str, timeout: int = 30) -> dict:
    last: Exception | None = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except Exception as e:
            last = e
            time.sleep(0.35 * (1.6**attempt))
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), "-A", UA, "-H", "Accept: application/json", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        last = RuntimeError(r.stderr or f"curl rc={r.returncode}")
    except Exception as e:
        last = e
    raise RuntimeError(f"GET failed {url}: {last}")


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


def load_diggers(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        log(f"diggers missing: {path}")
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        addr = (row.get("address") or "").strip()
        if addr:
            out[addr] = row
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
        "test_sent": False,
        "stats": {"ticks": 0, "posts": 0, "skips_no_webhook": 0},
    }


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # prune seen / posted
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
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_launch_catalog() -> dict[str, dict]:
    """Recent StonkFun mints (status=new + graduated newest)."""
    by_mint: dict[str, dict] = {}
    for status, pages in (("new", 3), ("graduated", 2)):
        for page in range(1, pages + 1):
            url = f"{SF_API}/tokens?status={status}&sort=newest&pageSize=50&page={page}"
            try:
                data = http_get_json(url, timeout=25)
            except Exception as e:
                log(f"catalog {status} p{page} fail: {type(e).__name__}")
                continue
            for t in (data.get("data") or {}).get("tokens") or []:
                mint = t.get("mint")
                if not mint or t.get("symbol") == "STONK":
                    continue
                by_mint[mint] = {
                    "mint": mint,
                    "symbol": t.get("symbol") or "?",
                    "name": t.get("name") or "",
                    "pool": t.get("pool"),
                    "status": t.get("status") or status,
                    "createdAt": t.get("createdAt"),
                    "marketCapUsd": float((t.get("market") or {}).get("marketCapUsd") or 0),
                    "peakMarketCapUsd": float((t.get("market") or {}).get("peakMarketCapUsd") or 0),
                }
            time.sleep(0.08)
    return by_mint


def lookup_stonkfun_mint(mint: str, cache: dict) -> dict | None:
    """Return token meta if mint is on StonkFun; cache negatives briefly."""
    now = time.time()
    hit = cache.get(mint)
    if hit and now - float(hit.get("at") or 0) < 1800:
        return hit.get("token")
    try:
        data = http_get_json(f"{SF_API}/tokens/{mint}", timeout=20)
        tok = (data.get("data") or {}).get("token")
        if isinstance(tok, dict) and tok.get("mint"):
            meta = {
                "mint": tok.get("mint"),
                "symbol": tok.get("symbol") or "?",
                "name": tok.get("name") or "",
                "pool": tok.get("pool"),
                "status": tok.get("status"),
                "createdAt": tok.get("createdAt"),
                "marketCapUsd": float((tok.get("market") or {}).get("marketCapUsd") or 0),
                "peakMarketCapUsd": float((tok.get("market") or {}).get("peakMarketCapUsd") or 0),
            }
            cache[mint] = {"at": now, "token": meta}
            return meta
    except Exception:
        pass
    cache[mint] = {"at": now, "token": None}
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
    """Return [(mint, delta)] where wallet gained SPL tokens."""
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
    diggers: list[dict],
    sig: str,
    block_time: int | None,
    test: bool = False,
) -> dict:
    sym = token.get("symbol") or "?"
    mint = token.get("mint") or ""
    status = token.get("status") or ""
    mc = float(token.get("marketCapUsd") or 0)
    title_prefix = "【テスト】" if test else "【StonkFun】"
    title = f"{title_prefix} {sym} digger buy"
    digger_lines = []
    for d in diggers[:5]:
        addr = d.get("address") or ""
        real = d.get("realized_pnl_usd_est")
        extra = f" pnl≈${float(real):,.0f}" if isinstance(real, (int, float)) else ""
        digger_lines.append(
            f"`{short_addr(addr)}` hits={d.get('hit_mints', '?')} score={d.get('score', '?')}{extra}"
        )
    gmgn_url = gmgn_tok.token_app_url("sol", mint)
    ts_iso, footer, jst = notify_stamp("stonkfun_signal" + (" · test" if test else ""))
    # Buy-time JST if block_time known (else notify time)
    buy_jst = jst_label(float(block_time), seconds=False) if block_time else jst
    fields = [
        {"name": "Symbol", "value": str(sym), "inline": True},
        {"name": "Status", "value": str(status or "—"), "inline": True},
        {"name": "MC (USD)", "value": f"{mc:,.0f}" if mc else "—", "inline": True},
        {"name": "Mint", "value": f"`{mint}`", "inline": False},
        {"name": "Diggers", "value": "\n".join(digger_lines) or "—", "inline": False},
        {
            "name": "リンク",
            "value": f"[GMGNアプリで開く]({gmgn_url})",
            "inline": False,
        },
        {
            "name": "通知時刻",
            "value": f"**{jst}**" + (f"\n(buy {buy_jst})" if block_time else ""),
            "inline": True,
        },
        {
            "name": "投稿",
            "value": f"**{jst}**",
            "inline": True,
        },
    ]
    color = 0xF1C40F if test else 0x00C2FF
    return {
        "title": title[:256],
        "description": (
            "StonkFun digger signal (Solana). "
            + ("**テスト投稿** — channel wiring check." if test else "Watchlist digger bought this launch.")
        )[:4000],
        "color": color,
        "fields": fields,
        "footer": footer,
        "timestamp": ts_iso,
    }


def post_test_embed(webhook: str) -> bool:
    demo_mint = "So11111111111111111111111111111111111111112"
    gmgn_url = gmgn_tok.token_app_url("sol", demo_mint)
    ts_iso, footer, jst = notify_stamp("stonkfun_signal · test")
    embed = {
        "title": "【テスト】 StonkFun digger channel",
        "description": (
            "**テスト投稿** — DISCORD_STONKFUN_WEBHOOK_URL 配線確認。\n"
            "今後、StonkFun digger ウォレットの買いシグナルはこのチャンネルのみに流れます"
            "（RH `DISCORD_WEBHOOK_URL` には送りません）。\n"
            "リンクは GMGNアプリ universal link / 通知時刻は JST 表示。"
        ),
        "color": 0xF1C40F,
        "fields": [
            {"name": "Channel", "value": "dedicated StonkFun", "inline": True},
            {"name": "Fallback", "value": "none (RH blocked)", "inline": True},
            {
                "name": "リンク",
                "value": f"[GMGNアプリで開く]({gmgn_url})",
                "inline": False,
            },
            {"name": "通知時刻", "value": f"**{jst}**", "inline": True},
            {"name": "投稿", "value": f"**{jst}**", "inline": True},
        ],
        "footer": footer,
        "timestamp": ts_iso,
    }
    ok = discord_post(webhook, [embed], content="")
    log(f"test embed {'ok' if ok else 'FAIL'} jst={jst} gmgn={gmgn_url}")
    return ok


def maybe_notify(
    *,
    webhook: str,
    st: dict,
    mint: str,
    token: dict,
    digger: dict,
    sig: str,
    block_time: int | None,
    min_diggers: int,
    cooldown: int,
) -> bool:
    wallet = digger["address"]
    seen_key = f"{mint}:{wallet}:{sig}"
    if seen_key in (st.get("seen") or {}):
        return False
    st.setdefault("seen", {})[seen_key] = time.time()

    cool_key = f"{mint}:{wallet}"
    last = float((st.get("posted") or {}).get(cool_key) or 0)
    if time.time() - last < cooldown:
        return False

    pending = st.setdefault("pending", {})
    entry = pending.get(mint) or {"first_ts": time.time(), "wallets": {}, "sig": sig, "block_time": block_time}
    entry["wallets"][wallet] = {
        "address": wallet,
        "hit_mints": digger.get("hit_mints"),
        "score": digger.get("score"),
        "sig": sig,
        "block_time": block_time,
    }
    entry["sig"] = sig
    entry["block_time"] = block_time or entry.get("block_time")
    entry["token"] = token
    pending[mint] = entry

    wallets = list(entry["wallets"].values())
    if len(wallets) < min_diggers:
        log(f"pending mint={short_addr(mint, 6)} diggers={len(wallets)}/{min_diggers}")
        return False

    if not webhook:
        st.setdefault("stats", {})["skips_no_webhook"] = int(
            (st.get("stats") or {}).get("skips_no_webhook") or 0
        ) + 1
        log("SKIP post — DISCORD_STONKFUN_WEBHOOK_URL empty (no RH fallback)")
        return False

    embed = build_embed(
        token=token,
        diggers=wallets,
        sig=entry.get("sig") or sig,
        block_time=entry.get("block_time") or block_time,
    )
    ok = discord_post(webhook, [embed])
    if ok:
        now = time.time()
        for w in wallets:
            st.setdefault("posted", {})[f"{mint}:{w['address']}"] = now
        pending.pop(mint, None)
        st.setdefault("stats", {})["posts"] = int((st.get("stats") or {}).get("posts") or 0) + 1
        log(
            f"POSTED {token.get('symbol')} mint={short_addr(mint, 6)} "
            f"diggers={len(wallets)} webhook=dedicated"
        )
    return ok


def scan_once(st: dict, diggers: dict[str, dict], webhook: str) -> int:
    min_diggers = max(1, env_int("STONKFUN_MIN_DIGGERS", 1))
    sig_limit = env_int("STONKFUN_SIG_LIMIT", 12)
    cooldown = env_int("STONKFUN_COOLDOWN_SECONDS", 600)
    boot = not bool(st.get("wallet_cursors"))

    catalog = fetch_launch_catalog()
    log(f"catalog mints={len(catalog)} diggers={len(diggers)} webhook={'dedicated' if webhook else 'MISSING'}")

    found = 0
    cursors = st.setdefault("wallet_cursors", {})
    mint_cache = st.setdefault("mint_cache", {})

    # Seed cursors on first run without alerting historical buys
    addrs = list(diggers.keys())
    for i in range(0, len(addrs), 6):
        chunk = addrs[i : i + 6]
        calls = [
            ("getSignaturesForAddress", [a, {"limit": sig_limit}])
            for a in chunk
        ]
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
            # oldest first
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
                    token = catalog.get(mint)
                    if not token:
                        token = lookup_stonkfun_mint(mint, mint_cache)
                    if not token:
                        continue
                    if maybe_notify(
                        webhook=webhook,
                        st=st,
                        mint=mint,
                        token=token,
                        digger=diggers[addr],
                        sig=sig,
                        block_time=s.get("blockTime"),
                        min_diggers=min_diggers,
                        cooldown=cooldown,
                    ):
                        found += 1
                time.sleep(0.05)
        time.sleep(0.08)

    if boot:
        log("boot: cursors seeded — skip historical")
    st.setdefault("stats", {})["ticks"] = int((st.get("stats") or {}).get("ticks") or 0) + 1
    st["last_tick_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(st)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="StonkFun digger → Discord (dedicated webhook)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true", help="post テスト embed")
    args = ap.parse_args()

    webhook = resolve_stonkfun_webhook()
    # Explicit: never touch DISCORD_WEBHOOK_URL for posts
    if webhook and webhook == (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip():
        # Same URL string would mean user pointed both at same channel — still ok to post
        # to "stonkfun" var; we do not *read* RH var for posting.
        pass

    diggers = load_diggers(DIGGERS_PATH)
    st = load_state()
    poll = env_int("STONKFUN_POLL_SECONDS", 20)
    once = args.once or os.environ.get("STONKFUN_TICK_ONCE") == "1"
    do_test = args.test or os.environ.get("STONKFUN_SIGNAL_TEST") == "1"

    log(
        f"start diggers={len(diggers)} poll={poll}s "
        f"webhook={'dedicated_ok' if webhook else 'EMPTY_skip_posts'} "
        f"min_diggers={env_int('STONKFUN_MIN_DIGGERS', 1)} live=0"
    )

    if do_test:
        if not webhook:
            log("TEST skipped — DISCORD_STONKFUN_WEBHOOK_URL empty (no RH fallback)")
        elif not st.get("test_sent") or args.test or os.environ.get("STONKFUN_SIGNAL_TEST") == "1":
            if post_test_embed(webhook):
                st["test_sent"] = True
                save_state(st)

    if once:
        scan_once(st, diggers, webhook)
        return 0

    while True:
        diggers = load_diggers(DIGGERS_PATH) or diggers
        webhook = resolve_stonkfun_webhook()
        try:
            n = scan_once(st, diggers, webhook)
            if n:
                log(f"tick posts={n}")
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
            save_state(st)
        time.sleep(max(8, poll))


if __name__ == "__main__":
    raise SystemExit(main())

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
  STONKFUN_ALERT_MAX_AGE_H      open_alerts follow-up window (default 24h)
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
import re

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

# RH-style moderate milestones (each once per mint)
MULTIPLIER_MILESTONES = (1.5, 2.0, 3.0, 5.0)
ALERT_MAX_AGE_DEFAULT_H = 24


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
    if n != n or n <= 0:  # NaN / non-positive
        return None
    return n


def _extract_json_blob(raw: str):
    """Best-effort JSON from bare body or jina markdown wrapper."""
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


def fetch_price_snapshot(mint: str) -> dict:
    """Price/MC for mult. Prefer StonkFun API; fallback Dex/Gecko via jina (box 429)."""
    out: dict = {"price_usd": None, "mcap_usd": None, "source": None}
    # 1) StonkFun public API (works from box)
    try:
        data = http_get_json(f"{SF_API}/tokens/{mint}", timeout=18)
        tok = (data.get("data") or {}).get("token") or {}
        market = tok.get("market") or {}
        px = _num(market.get("priceUsd"))
        mc = _num(market.get("marketCapUsd") or market.get("fdvUsd"))
        if px or mc:
            out["price_usd"] = px
            out["mcap_usd"] = mc
            out["source"] = "stonkfun"
            if px:
                return out
    except Exception:
        pass
    # 2) DexScreener via jina
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
        px = _num(best.get("priceUsd"))
        mc = _num(best.get("marketCap") or best.get("fdv"))
        if px or mc:
            out["price_usd"] = out["price_usd"] or px
            out["mcap_usd"] = out["mcap_usd"] or mc
            out["source"] = out["source"] or "dexscreener_via_jina"
            if out["price_usd"]:
                return out
    # 3) GeckoTerminal solana via jina
    raw = http_get_text(
        f"https://r.jina.ai/http://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}",
        timeout=28,
    )
    data = _extract_json_blob(raw or "")
    attrs = ((data or {}).get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if isinstance(attrs, dict):
        px = _num(attrs.get("price_usd"))
        mc = _num(attrs.get("fdv_usd") or attrs.get("market_cap_usd"))
        if px or mc:
            out["price_usd"] = out["price_usd"] or px
            out["mcap_usd"] = out["mcap_usd"] or mc
            out["source"] = out["source"] or "gecko_via_jina"
    return out


def compute_mult(alert_price, now_price, alert_mcap=None, now_mcap=None) -> float | None:
    """Prefer price ratio; else mcap ratio (∝ price). Never invent."""
    ap, np_ = _num(alert_price), _num(now_price)
    if ap and np_:
        return np_ / ap
    am, nm = _num(alert_mcap), _num(now_mcap)
    if am and nm:
        return nm / am
    return None


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
        "open_alerts": [],
        "test_sent": False,
        "stats": {"ticks": 0, "posts": 0, "skips_no_webhook": 0, "followups": 0},
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
    max_age = env_int("STONKFUN_ALERT_MAX_AGE_H", ALERT_MAX_AGE_DEFAULT_H) * 3600
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
                market = t.get("market") or {}
                by_mint[mint] = {
                    "mint": mint,
                    "symbol": t.get("symbol") or "?",
                    "name": t.get("name") or "",
                    "pool": t.get("pool"),
                    "status": t.get("status") or status,
                    "createdAt": t.get("createdAt"),
                    "priceUsd": float(market.get("priceUsd") or 0) or None,
                    "marketCapUsd": float(market.get("marketCapUsd") or 0) or None,
                    "peakMarketCapUsd": float(market.get("peakMarketCapUsd") or 0) or None,
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
            market = tok.get("market") or {}
            meta = {
                "mint": tok.get("mint"),
                "symbol": tok.get("symbol") or "?",
                "name": tok.get("name") or "",
                "pool": tok.get("pool"),
                "status": tok.get("status"),
                "createdAt": tok.get("createdAt"),
                "priceUsd": float(market.get("priceUsd") or 0) or None,
                "marketCapUsd": float(market.get("marketCapUsd") or 0) or None,
                "peakMarketCapUsd": float(market.get("peakMarketCapUsd") or 0) or None,
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
    alert_price: float | None = None,
    alert_mcap: float | None = None,
    mult: float | None = None,
    test: bool = False,
) -> dict:
    sym = token.get("symbol") or "?"
    mint = token.get("mint") or ""
    status = token.get("status") or "—"
    n_dig = len(diggers)
    title_prefix = "【テスト】" if test else "【StonkFun】"
    title = f"{title_prefix} ${sym} · digger×{n_dig} · {status}"

    digger_lines = []
    for d in diggers[:5]:
        addr = d.get("address") or ""
        hits = d.get("hit_mints", "?")
        digger_lines.append(f"`{short_addr(addr)}` · hits {hits}")
    if len(diggers) > 5:
        digger_lines.append(f"…他 {len(diggers) - 5} 件")

    px = _num(alert_price) or _num(token.get("priceUsd"))
    mc = _num(alert_mcap) or _num(token.get("marketCapUsd"))
    price_mc = f"{fmt_price(px)} / MC {fmt_usd(mc)}" if (px or mc) else "—"
    mult_s = fmt_mult(mult) if mult is not None else "—"

    gmgn_url = gmgn_tok.token_app_url("sol", mint)
    ts_iso, footer, jst = notify_stamp("stonkfun_signal" + (" · test" if test else ""))
    buy_jst = jst_label(float(block_time), seconds=False) if block_time else None
    notify_val = f"**{jst}**"
    if buy_jst and buy_jst != jst:
        notify_val += f"\n(約定 {buy_jst})"

    fields = [
        {"name": "通知時刻", "value": notify_val, "inline": True},
        {"name": "価格 / MC（通知時）", "value": price_mc, "inline": True},
        {"name": "倍率", "value": f"**{mult_s}**" if mult_s != "—" else "—", "inline": True},
        {
            "name": f"Diggers（{n_dig}）",
            "value": "\n".join(digger_lines) or "—",
            "inline": False,
        },
        {
            "name": "GMGNアプリで開く",
            "value": f"[開く]({gmgn_url})",
            "inline": False,
        },
    ]
    color = 0xF1C40F if test else 0x00C2FF
    desc = "配線テスト投稿です。" if test else f"StonkFun digger が ${sym} を購入。"
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
    ts_iso, footer, jst = notify_stamp("stonkfun_signal · 倍率")
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
        "status": "new",
        "priceUsd": 0.00012,
        "marketCapUsd": 12000,
    }
    embed = build_embed(
        token=demo_token,
        diggers=[
            {"address": "DiggEr111111111111111111111111111111111111", "hit_mints": 3},
            {"address": "DiggEr222222222222222222222222222222222222", "hit_mints": 2},
        ],
        sig="test",
        block_time=None,
        alert_price=0.00012,
        alert_mcap=12000,
        mult=1.0,
        test=True,
    )
    # clarify test-only description
    embed["description"] = (
        "**テスト投稿** — DISCORD_STONKFUN_WEBHOOK_URL 配線確認。\n"
        "新レイアウト: 通知時刻 / 価格·MC / 倍率 / Diggers / GMGNアプリ。\n"
        "RH `DISCORD_WEBHOOK_URL` には送りません。"
    )
    ok = discord_post(webhook, [embed], content="")
    _, _, jst = notify_stamp("stonkfun_signal · test")
    log(f"test embed {'ok' if ok else 'FAIL'} jst={jst} layout=v2")
    return ok


def process_multiplier_followups(st: dict, webhook: str) -> int:
    """Post moderate milestone updates (1.5/2/3/5x once each)."""
    if not webhook:
        return 0
    now = time.time()
    max_age = env_int("STONKFUN_ALERT_MAX_AGE_H", ALERT_MAX_AGE_DEFAULT_H) * 3600
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
        # throttle price fetches a bit per alert
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

    # Prefer catalog/token price; refresh once from StonkFun/Dex if missing
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

    # At notify time mult is ~1.0 when we have a baseline; else —
    mult = 1.0 if (alert_price or alert_mcap) else None

    embed = build_embed(
        token=token,
        diggers=wallets,
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
        _, _, jst = notify_stamp("stonkfun_signal")
        # open_alerts for milestone follow-ups (one row per mint)
        open_alerts = [a for a in (st.get("open_alerts") or []) if (a.get("mint") or "") != mint]
        open_alerts.append(
            {
                "mint": mint,
                "ca": mint,
                "symbol": token.get("symbol"),
                "status": token.get("status"),
                "alert_price_usd": alert_price,
                "alert_mcap": alert_mcap,
                "alert_jst": jst,
                "posted_at": now,
                "milestones_hit": [],
                "digger_count": len(wallets),
                "sig": entry.get("sig") or sig,
            }
        )
        st["open_alerts"] = open_alerts[-200:]
        log(
            f"POSTED {token.get('symbol')} mint={short_addr(mint, 6)} "
            f"diggers={len(wallets)} px={alert_price} mc={alert_mcap} webhook=dedicated"
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

    # Moderate multiplier follow-ups for recent alerts
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

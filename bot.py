#!/usr/bin/env python3
"""Standalone RH/Arc meme overlap signal → Discord webhook. Paper only.

Primary trade source: GMGN smartmoney (gmgn-cli).
Watchlist-strict by default (ALLOW_GMGN_CLUSTER=0).
Nansen: optional wallet-list refresh only (--refresh-wallets), NOT dex-trades polling.
LIVE_TRADING is blocked (stub only) until paper gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from load_secrets import load as load_box_secrets
    from load_secrets import write_gmgn_dotenv
except ImportError:  # Actions / VPS without load_secrets helper
    def load_box_secrets(names=None):  # type: ignore
        return {n: bool(os.environ.get(n)) for n in (names or [])}

    def write_gmgn_dotenv(path=None):  # type: ignore
        key = (os.environ.get("GMGN_API_KEY") or "").strip()
        if not key:
            return False
        dest = path or (Path.home() / ".config/gmgn/.env")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"GMGN_API_KEY={key}\n", encoding="utf-8")
        try:
            dest.chmod(0o600)
        except OSError:
            pass
        return True


import paper_trade as paper_mod

SKIP_CA = {
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "0x0000000000000000000000000000000000000000",
}
SKIP_SYMBOLS = {
    "ETH", "WETH", "USDC", "USDT", "DAI", "WBTC", "USDG", "USD", "SOL", "BNB", "WBNB",
}

CHAIN_META = {
    "robinhood": {
        "dex_slug": "robinhood",
        "gmgn_chain": "robinhood",
        "goplus_id": "4663",
        "explorer": "https://robinhoodchain.blockscout.com/token/",
        "blockscout_hosts": [
            "https://robinhoodchain.blockscout.com",
            "https://api.blockscout.com/4663",
        ],
        "jp": "Robinhoodチェーン",
    },
    "arc": {
        "dex_slug": "arc",
        "gmgn_chain": "arc",
        "goplus_id": None,
        "explorer": None,
        "blockscout_hosts": [],
        "jp": "Arcチェーン",
    },
    "solana": {
        "dex_slug": "solana",
        "gmgn_chain": "sol",
        "goplus_id": "solana",
        "explorer": "https://solscan.io/token/",
        "blockscout_hosts": [],
        "jp": "Solana",
    },
}

LIQ_MCAP_MIN = 0.30
MULTIPLIER_MILESTONES = (1.5, 2.0, 3.0, 5.0)
ALERT_MAX_AGE_SEC = 24 * 3600
FOLLOWUP_COOLDOWN_SEC = 30 * 60
NANSEN_SLEEP = 0.8
DEFAULT_COOLDOWN_SECONDS = 7200  # 2h
MAX_SKIP_NOTICES_PER_RUN = 3
# Paper: $300 bankroll, FOUNDATION risk (1 pos, 20/30%, +100% half, -40% stop, max5/week, 3-loss week stop)
# LIVE_TRADING: never place real orders. Env LIVE_TRADING must stay 0 until paper gate.



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


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def resolve_paper_webhook(signal: str | None = None) -> str | None:
    """Paper-channel webhook; falls back to signal webhook with a warning if unset."""
    paper = (os.environ.get("DISCORD_PAPER_WEBHOOK_URL") or "").strip()
    if paper:
        return paper
    sig = (signal or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if sig:
        print(
            "WARNING: DISCORD_PAPER_WEBHOOK_URL missing — paper posts fall back to DISCORD_WEBHOOK_URL",
            file=sys.stderr,
        )
        return sig
    return None


def resolve_discord_webhooks() -> tuple[str, str]:
    """Signal webhook (required) + paper webhook (optional, falls back with warning)."""
    signal = env("DISCORD_WEBHOOK_URL")
    paper = resolve_paper_webhook(signal)
    assert paper  # signal non-empty ⇒ fallback always yields a URL
    return signal, paper


def http_get_json(url: str, headers: dict | None = None, timeout: int = 25) -> dict | list | None:
    hdrs = {"User-Agent": "meme-discord-bot/2.0", "Accept": "application/json"}
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
    hdrs = {"Content-Type": "application/json", "User-Agent": "meme-discord-bot/2.0"}
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


def discord_webhook(url: str, content: str = "", embeds: list | None = None) -> dict | None:
    """Post webhook; prefer wait=true to capture message id for follow-ups."""
    payload: dict = {}
    if content:
        payload["content"] = content[:1900]
    if embeds:
        payload["embeds"] = embeds[:10]
    if not payload:
        payload["content"] = "."
    base = url.split("?")[0]
    wait_url = base + ("&" if "?" in url else "?") + "wait=true"
    # If wait URL already has query from original, rebuild carefully
    if "wait=" not in url:
        sep = "&" if "?" in url else "?"
        wait_url = url + sep + "wait=true"
    else:
        wait_url = url
    try:
        return post_json(wait_url, payload)
    except SystemExit as e:
        # fallback without wait
        print(f"webhook wait failed ({e}); retry without wait", file=sys.stderr)
        post_json(url.split("?")[0] if "wait=" in url else url, payload)
        return None


def wallet_passes_filter(o: dict, min_realized: float) -> bool:
    if bool(o.get("pass_pnl")):
        return True
    try:
        if float(o.get("realized_pnl_usd") or 0) > min_realized:
            return True
    except (TypeError, ValueError):
        pass
    for key in ("pnl_usd", "gmgn_pnl_usd"):
        if key in o and o[key] is not None:
            try:
                if float(o[key]) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def load_watchlist(path: Path, min_realized: float) -> tuple[dict[str, dict], int, bool]:
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
    return {
        "seen_signal_keys": [],
        "ca_last_posted": {},
        "open_alerts": [],
        "paper_positions": [],
        "paper": {},
        "last_poll_ts": None,
    }


def save_state(path: Path, state: dict) -> None:
    keys = state.get("seen_signal_keys") or []
    state["seen_signal_keys"] = keys[-500:]
    now = time.time()
    ca_map = state.get("ca_last_posted") or {}
    state["ca_last_posted"] = {
        k: v for k, v in ca_map.items() if isinstance(v, (int, float)) and now - float(v) < 7 * 86400
    }
    alerts = state.get("open_alerts") or []
    pruned = []
    for a in alerts:
        try:
            age = now - float(a.get("posted_at") or 0)
        except (TypeError, ValueError):
            continue
        if age <= ALERT_MAX_AGE_SEC + 3600:
            pruned.append(a)
    state["open_alerts"] = pruned[-200:]
    positions = state.get("paper_positions") or []
    kept_pos = []
    for p in positions:
        st = (p.get("status") or "open")
        if st in ("open", "half_taken"):
            kept_pos.append(p)
        else:
            # keep recent closed briefly
            try:
                closed_at = float(p.get("closed_at") or p.get("opened_at") or 0)
            except (TypeError, ValueError):
                continue
            if now - closed_at < 7 * 86400:
                kept_pos.append(p)
    state["paper_positions"] = kept_pos[-100:]
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_paper_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_paper_book(path: Path, row: dict) -> None:
    """Append virtual paper-trade event. Never places real orders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row.setdefault("paper", True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def default_watchlist_path(chain: str) -> Path:
    if chain == "arc":
        p = ROOT / "arc-wallets" / "wallets.jsonl"
        if p.exists():
            return p
    return ROOT / "rh-wallets" / "wallets.jsonl"


def live_trading_blocked() -> None:
    """Hard stub: refuse live trading regardless of env typo."""
    if env_bool("LIVE_TRADING", False):
        print(
            "LIVE_TRADING=1 ignored — live trading blocked until paper gate. "
            "Forcing paper-only mode.",
            file=sys.stderr,
        )


def parse_ts(s) -> float:
    if s is None or s == "":
        return 0.0
    if isinstance(s, (int, float)):
        v = float(s)
        # ms vs s
        return v / 1000.0 if v > 1e12 else v
    try:
        s = str(s)
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return parse_ts(float(s))
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _num(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "—"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}K"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.6f}"


# ---------------------------------------------------------------------------
# Trade sources
# ---------------------------------------------------------------------------


class GmgnError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # auth | rate | other


def gmgn_key_looks_placeholder(key: str) -> bool:
    low = key.lower()
    return (
        not key
        or low.startswith("install")
        or "skills add" in low
        or "gmgn-skills" in low
        or len(key) < 16
    )


def fetch_gmgn_smartmoney(chain: str, limit: int, side: str = "buy") -> tuple[list[dict], str | None]:
    """Run gmgn-cli track smartmoney. Returns (normalized_trades, error_kind|None)."""
    meta = CHAIN_META.get(chain, {})
    gmgn_chain = meta.get("gmgn_chain") or chain
    write_gmgn_dotenv()
    key = (os.environ.get("GMGN_API_KEY") or "").strip()
    if gmgn_key_looks_placeholder(key):
        print(
            "GMGN_API_KEY looks invalid/placeholder (AUTH_KEY_INVALID expected). "
            "Soft-continue with fallback + multiplier updates.",
            file=sys.stderr,
        )
        return [], "auth"

    cli = shutil.which("gmgn-cli")
    if not cli:
        print("gmgn-cli not found on PATH", file=sys.stderr)
        return [], "other"

    cmd = [
        cli,
        "track",
        "smartmoney",
        "--chain",
        gmgn_chain,
        "--side",
        side,
        "--limit",
        str(max(1, min(200, limit))),
        "--raw",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        print("gmgn-cli timeout", file=sys.stderr)
        return [], "other"
    except Exception as e:
        print(f"gmgn-cli spawn fail: {type(e).__name__}", file=sys.stderr)
        return [], "other"

    err = (proc.stderr or "") + "\n" + (proc.stdout or "")
    err_u = err.upper()
    if proc.returncode != 0 or "AUTH_KEY_INVALID" in err_u or "API KEY INVALID" in err_u:
        kind = "auth" if ("AUTH_KEY_INVALID" in err_u or "401" in err or "API KEY INVALID" in err_u) else "other"
        if "RATE_LIMIT" in err_u or "429" in err:
            kind = "rate"
        # Never print key; truncate stderr safely
        safe = re.sub(r"(GMGN_API_KEY|apikey|api[_-]?key)[=:\s]+\S+", r"\1=***", err, flags=re.I)
        print(f"gmgn-cli failed kind={kind}: {safe.strip()[:400]}", file=sys.stderr)
        return [], kind

    out = (proc.stdout or "").strip()
    if not out:
        print("gmgn-cli empty stdout", file=sys.stderr)
        return [], "other"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print("gmgn-cli non-JSON stdout", file=sys.stderr)
        return [], "other"

    rows = data.get("list") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print("gmgn-cli unexpected shape", file=sys.stderr)
        return [], "other"

    trades: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        side_i = (item.get("side") or "").lower()
        if side and side_i and side_i != side.lower():
            continue
        maker = (item.get("maker") or (item.get("maker_info") or {}).get("address") or "").lower()
        ca = (item.get("base_address") or item.get("token_address") or "").lower()
        if not maker.startswith("0x") or not ca.startswith("0x"):
            continue
        if ca in SKIP_CA:
            continue
        base_tok = item.get("base_token") or {}
        sym = (base_tok.get("symbol") or item.get("symbol") or "").strip()
        if sym.upper() in SKIP_SYMBOLS:
            continue
        usd = _num(item.get("amount_usd") or item.get("cost_usd")) or 0.0
        ts = item.get("timestamp") or item.get("block_timestamp")
        label = ""
        mi = item.get("maker_info") or {}
        if isinstance(mi, dict):
            label = (mi.get("name") or mi.get("twitter_username") or "")[:80]
        trades.append(
            {
                "trader_address": maker,
                "trader_address_label": label,
                "token_bought_address": ca,
                "token_bought_symbol": sym,
                "trade_value_usd": usd,
                "block_timestamp": ts,
                "price_usd": _num(item.get("price_usd")),
                "source": "gmgn",
                "tx": item.get("transaction_hash"),
            }
        )
    print(f"gmgn smartmoney trades={len(trades)} chain={gmgn_chain} limit={limit}")
    return trades, None


def fetch_onchain_fallback(watch: set[str], chain: str, min_usd: float) -> list[dict]:
    """Best-effort RH explorer wallet activity. Never invent trades.

    DexScreener cannot list wallet trades. Public Blockscout for RH is often
    Cloudflare-blocked; Pro API needs a key. If nothing works, return [].
    """
    meta = CHAIN_META.get(chain, {})
    hosts = meta.get("blockscout_hosts") or []
    if not hosts:
        print("onchain fallback: no explorer hosts configured; returning empty")
        return []

    # Sample a small subset of watch wallets to avoid hammering
    sample = sorted(watch)[:8]
    trades: list[dict] = []
    any_ok = False
    for host in hosts:
        host_ok = False
        for addr in sample:
            url = f"{host}/api/v2/addresses/{addr}/token-transfers?type=ERC-20"
            data = http_get_json(url, timeout=12)
            if not isinstance(data, dict) or "items" not in data:
                # first miss on a host → likely Cloudflare/402; skip rest of host
                if not host_ok:
                    break
                continue
            host_ok = True
            any_ok = True
            for it in data.get("items") or []:
                # Heuristic: incoming ERC-20 to wallet ≈ buy-ish (weak)
                to_a = ((it.get("to") or {}).get("hash") or "").lower()
                fr_a = ((it.get("from") or {}).get("hash") or "").lower()
                tok = it.get("token") or {}
                ca = (tok.get("address_hash") or tok.get("address") or "").lower()
                if to_a != addr or not ca.startswith("0x") or ca in SKIP_CA:
                    continue
                sym = (tok.get("symbol") or "").upper()
                if sym in SKIP_SYMBOLS:
                    continue
                # No reliable USD on explorer without extra calls — skip if unknown
                usd = 0.0
                if usd < min_usd and min_usd > 0:
                    # keep with 0 only if min_usd is 0; else skip (no fake USD)
                    continue
                ts = it.get("timestamp")
                trades.append(
                    {
                        "trader_address": addr,
                        "trader_address_label": "",
                        "token_bought_address": ca,
                        "token_bought_symbol": tok.get("symbol"),
                        "trade_value_usd": usd,
                        "block_timestamp": ts,
                        "source": "onchain",
                        "tx": (it.get("transaction_hash") or it.get("tx_hash")),
                        "counterparty": fr_a,
                    }
                )
        if any_ok:
            break

    if not any_ok:
        print(
            "onchain fallback: RH explorer APIs unreachable "
            "(Cloudflare / Pro API key required). DexScreener cannot list wallet trades. "
            "Rely on GMGN for new signals; multipliers still use DexScreener prices.",
            file=sys.stderr,
        )
        return []
    print(f"onchain fallback trades={len(trades)} (best-effort, may lack USD)")
    return trades


def fetch_nansen_dex_trades(api_key: str, chain: str, page: int, per_page: int, min_usd: float) -> list[dict]:
    """Legacy Nansen dex-trades — ONLY when NANSEN_FOR_TRADES=1 (not default)."""
    body = {
        "chains": [chain],
        "pagination": {"page": page, "per_page": per_page},
        "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
        "filters": {"trade_value_usd": {"min": min_usd}},
    }
    url = "https://api.nansen.ai/api/v1/smart-money/dex-trades"
    data = post_json(url, body, headers={"apikey": api_key})
    return data.get("data") or []


def refresh_wallets_nansen(watch_path: Path, pages: int = 2) -> int:
    """Infrequent Nansen pnl-leaderboard refresh → merge into wallets.jsonl."""
    load_box_secrets(["NANSEN_API_KEY"])
    api_key = env("NANSEN_API_KEY")
    chain = os.environ.get("CHAIN", "robinhood").strip().lower()
    existing: dict[str, dict] = {}
    if watch_path.exists():
        for line in watch_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            a = (o.get("address") or "").lower()
            if a.startswith("0x"):
                existing[a] = o

    added = 0
    for page in range(1, pages + 1):
        body = {
            "chains": [chain],
            "pagination": {"page": page, "per_page": 100},
            "order_by": [{"field": "realized_pnl_usd", "direction": "DESC"}],
            "filters": {},
        }
        # try common timeframes
        for tf in ("30d", "7d"):
            body_tf = dict(body)
            body_tf["date"] = {"from": "", "to": ""}  # API may ignore
            try:
                url = "https://api.nansen.ai/api/v1/smart-money/pnl-leaderboard"
                # Minimal payload variants
                payloads = [
                    {
                        "chain": chain,
                        "pagination": {"page": page, "per_page": 50},
                        "order_by": [{"field": "realized_pnl_usd", "direction": "DESC"}],
                        "timeframe": tf,
                    },
                    {
                        "chains": [chain],
                        "pagination": {"page": page, "per_page": 50},
                        "order_by": [{"field": "realized_pnl_usd", "direction": "DESC"}],
                        "filters": {"include_smart_money_labels": ["Smart Trader", "Fund", "30D Smart Trader"]},
                    },
                ]
                rows = None
                for payload in payloads:
                    try:
                        data = post_json(url, payload, headers={"apikey": api_key})
                        rows = data.get("data") or data.get("result") or []
                        if rows:
                            break
                    except SystemExit as e:
                        print(f"nansen refresh try fail: {str(e)[:120]}", file=sys.stderr)
                        continue
                if not rows:
                    continue
                for r in rows:
                    addr = (r.get("address") or r.get("trader_address") or "").lower()
                    if not addr.startswith("0x"):
                        continue
                    realized = _num(r.get("realized_pnl_usd") or r.get("realizedPnlUsd")) or 0.0
                    prev = existing.get(addr) or {}
                    merged = {
                        **prev,
                        "address": addr,
                        "address_label": r.get("address_label") or r.get("label") or prev.get("address_label") or "",
                        "realized_pnl_usd": max(realized, float(prev.get("realized_pnl_usd") or 0)),
                        "pass_pnl": True if realized > 0 or prev.get("pass_pnl") else prev.get("pass_pnl", False),
                        "source_endpoints": list(
                            dict.fromkeys(
                                list(prev.get("source_endpoints") or []) + ["pnl-leaderboard"]
                            )
                        ),
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                    }
                    if addr not in existing:
                        added += 1
                    existing[addr] = merged
            finally:
                time.sleep(NANSEN_SLEEP)

    watch_path.parent.mkdir(parents=True, exist_ok=True)
    with watch_path.open("w", encoding="utf-8") as f:
        for a in sorted(existing.keys()):
            f.write(json.dumps(existing[a], ensure_ascii=False) + "\n")
    print(f"refresh-wallets done total={len(existing)} newly_seen≈{added} path={watch_path}")
    return 0


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------


def detect_signals(
    trades: list[dict],
    watch: set[str],
    window_sec: int,
    min_wallets: int,
    min_usd: float,
) -> list[dict]:
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
        if min_usd > 0 and usd < min_usd:
            continue
        by_token[ca].append(t)

    signals = []
    for ca, rows in by_token.items():
        rows.sort(key=lambda x: parse_ts(x.get("block_timestamp")))
        for i, first in enumerate(rows):
            t0 = parse_ts(first.get("block_timestamp"))
            wallets: dict[str, dict] = {}
            for j in range(i, len(rows)):
                r = rows[j]
                tj = parse_ts(r.get("block_timestamp"))
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
                        "elapsed": max(parse_ts(r.get("block_timestamp")) for r in wallets.values()) - t0,
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


def fetch_dexscreener(ca: str, chain: str) -> dict:
    meta = CHAIN_META.get(chain, {})
    dex_slug = meta.get("dex_slug") or chain
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    data = http_get_json(url)
    pairs = []
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    if not pairs:
        return {
            "ok": False,
            "reason": "no_pair",
            "liq_usd": None,
            "mcap_usd": None,
            "fdv": None,
            "price_usd": None,
            "url": None,
            "pair": None,
        }

    preferred = [p for p in pairs if (p.get("chainId") or "").lower() == dex_slug.lower()]
    pool = preferred or pairs

    def liq_of(p):
        return _num((p.get("liquidity") or {}).get("usd")) or 0.0

    pair = max(pool, key=liq_of)
    liq = _num((pair.get("liquidity") or {}).get("usd"))
    mcap = _num(pair.get("marketCap"))
    fdv = _num(pair.get("fdv"))
    price = _num(pair.get("priceUsd"))
    pair_url = pair.get("url")
    return {
        "ok": True,
        "reason": None,
        "liq_usd": liq,
        "mcap_usd": mcap,
        "fdv": fdv,
        "price_usd": price,
        "url": pair_url,
        "pair": pair.get("pairAddress"),
        "chainId": pair.get("chainId"),
        "symbol": (pair.get("baseToken") or {}).get("symbol"),
    }


def fetch_goplus(ca: str, chain: str) -> dict:
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
    info = result.get(ca.lower()) or result.get(ca) or {}
    if code not in (0, 1, "0", "1") or not info:
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
    dex = fetch_dexscreener(ca, chain)
    go = fetch_goplus(ca, chain)

    liq = dex.get("liq_usd")
    mcap = dex.get("mcap_usd")
    fdv = dex.get("fdv")
    price = dex.get("price_usd")
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
    print(f"safety ca={ca[:10]}… ok={ok} ratio={ratio} {go_note} reasons={fail_reasons or ['ok']}")

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
        "price_usd": price,
        "dex_url": dex.get("url"),
        "goplus": go.get("status"),
        "jp": jp,
        "symbol_hint": dex.get("symbol"),
    }


def strength_label(n: int, total_usd: float) -> tuple[str, int]:
    if n >= 3 and total_usd >= 500:
        return "かなり強い", 0xE74C3C
    if n >= 3:
        return "やや強い", 0xF39C12
    return "買いが重なった", 0x3498DB


def build_embed(s: dict, chain: str, safety: dict, source_mode: str) -> dict:
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

    if source_mode == "gmgn_cluster":
        who_jp = "GMGNスマートマネー（監視リスト外含む）"
        desc_extra = f"出典: {who_jp}\n"
    elif source_mode == "onchain":
        who_jp = "オンチェーン（探索・限定）"
        desc_extra = f"出典: {who_jp}\n"
    else:
        who_jp = "監視中の勝ち財布"
        desc_extra = "出典: 監視リスト ∩ GMGNスマートマネー\n"

    title = f"{strength}（{n}人）· ${sym}"
    ratio = safety.get("ratio")
    ratio_txt = f"{ratio:.0%}" if isinstance(ratio, (int, float)) else "—"
    description = (
        f"{desc_extra}"
        f"{chain_jp}で、{when}に{who_jp}が同じコインを購入しました。\n"
        f"購入合計の目安: 約 ${total_usd:,.0f}\n"
        f"安全チェック: {safety.get('jp') or '未実施'}"
    )

    fields = [
        {"name": "コントラクト", "value": f"`{s['ca']}`", "inline": False},
        {"name": "時価総額", "value": fmt_usd(safety.get("mcap_usd") or safety.get("fdv")), "inline": True},
        {"name": "流動性", "value": fmt_usd(safety.get("liq_usd")), "inline": True},
        {"name": "liq/mcap", "value": ratio_txt, "inline": True},
    ]

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
    fields.append({"name": "誰が買ったか", "value": "\n".join(who_lines)[:1000] or "—", "inline": False})

    return {
        "title": title[:256],
        "description": description[:4000],
        "color": color,
        "fields": fields,
        "footer": {"text": "お知らせのみ・自動では買いません"},
    }


def build_multiplier_embed(alert: dict, mult: float, dex: dict, milestone: float | None) -> dict:
    sym = alert.get("symbol") or dex.get("symbol") or "不明"
    ca = alert.get("ca")
    mcap = dex.get("mcap_usd") or dex.get("fdv")
    liq = dex.get("liq_usd")
    title = f"さっきの通知から {mult:.1f}倍 · ${sym}"
    if milestone:
        title = f"さっきの通知から {milestone:g}倍到達 · ${sym}"
    dex_url = dex.get("url") or f"https://dexscreener.com/robinhood/{ca}"
    return {
        "title": title[:256],
        "description": (
            f"通知時の価格から約 **{mult:.2f}倍** です。\n"
            f"現在 時価総額 {fmt_usd(mcap)} / 流動性 {fmt_usd(liq)}"
        )[:4000],
        "color": 0x9B59B6,
        "fields": [
            {"name": "コントラクト", "value": f"`{ca}`", "inline": False},
            {"name": "時価総額", "value": fmt_usd(mcap), "inline": True},
            {"name": "流動性", "value": fmt_usd(liq), "inline": True},
            {"name": "リンク", "value": f"[DexScreener]({dex_url})", "inline": False},
        ],
        "footer": {"text": "倍率フォローアップ・自動売買なし"},
    }


def build_skip_embed(s: dict, safety: dict, chain: str) -> dict:
    sym = s.get("symbol") or safety.get("symbol_hint") or "不明"
    reasons = safety.get("reasons") or []
    bits: list[str] = []
    for r in reasons:
        rs = str(r)
        if rs == "no_mcap":
            bits.append("時価なし")
        elif rs == "no_pair":
            bits.append("ペアなし")
        elif rs.startswith("liq_ratio"):
            bits.append("薄い板")
        elif "honeypot" in rs:
            bits.append("honeypot")
        elif "cannot_sell" in rs:
            bits.append("売却制限")
        elif "high_tax" in rs:
            bits.append("手数料高")
        else:
            bits.append("検査NG")
    reason_jp = "・".join(bits) if bits else (safety.get("jp") or "見送り")
    meta = CHAIN_META.get(chain, {})
    return {
        "title": f"見送り · ${sym}"[:256],
        "description": (
            f"{reason_jp}\n"
            f"監視交差 {s.get('n', '?')}人 · 自動では買いません"
        )[:4000],
        "color": 0x95A5A6,
        "fields": [
            {"name": "コントラクト", "value": f"`{s.get('ca')}`", "inline": False},
            {"name": "理由", "value": (safety.get("jp") or reason_jp)[:500], "inline": False},
        ],
        "footer": {"text": f"スキップ通知 · {meta.get('jp') or chain}"},
    }


def process_multiplier_followups(state: dict, webhook: str, chain: str, paper_path: Path) -> int:
    now = time.time()
    alerts = state.get("open_alerts") or []
    posted = 0
    for alert in alerts:
        try:
            posted_at = float(alert.get("posted_at") or 0)
        except (TypeError, ValueError):
            continue
        if now - posted_at > ALERT_MAX_AGE_SEC:
            continue
        alert_price = _num(alert.get("alert_price_usd"))
        if not alert_price or alert_price <= 0:
            continue
        ca = alert.get("ca")
        if not ca:
            continue
        dex = fetch_dexscreener(ca, chain)
        price_now = _num(dex.get("price_usd"))
        if not price_now or price_now <= 0:
            continue
        mult = price_now / alert_price
        hit = list(alert.get("milestones_hit") or [])
        next_ms = None
        for ms in MULTIPLIER_MILESTONES:
            if mult >= ms and ms not in hit:
                next_ms = ms
                break
        # Milestone crossings only (each once): 1.5x / 2x / 3x / 5x
        if next_ms is None:
            continue
        milestone = next_ms
        embed = build_multiplier_embed(alert, mult, dex, milestone)
        discord_webhook(webhook, embeds=[embed])
        if milestone is not None:
            hit.append(milestone)
            alert["milestones_hit"] = hit
        alert["last_followup_at"] = now
        alert["last_followup_mult"] = mult
        posted += 1
        append_paper_log(
            paper_path,
            {
                "event": "multiplier_followup",
                "ca": ca,
                "mult": mult,
                "milestone": milestone,
                "posted": True,
            },
        )
        print(f"followup {ca[:10]}… mult={mult:.2f} milestone={milestone}")
        time.sleep(0.4)
    state["open_alerts"] = alerts
    return posted


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------


def collect_trades(args: argparse.Namespace, chain: str, min_usd: float, watch_set: set[str]) -> tuple[list[dict], str, str | None]:
    """Returns (trades, source_name, gmgn_error_kind)."""
    nansen_for_trades = env_bool("NANSEN_FOR_TRADES", False)
    trades: list[dict] = []
    gmgn_err: str | None = None

    if nansen_for_trades:
        print("WARNING: NANSEN_FOR_TRADES=1 — using Nansen dex-trades (credit cost)", file=sys.stderr)
        load_box_secrets(["NANSEN_API_KEY"])
        api_key = env("NANSEN_API_KEY")
        for page in range(1, args.pages + 1):
            batch = fetch_nansen_dex_trades(api_key, chain, page, args.per_page, min_usd)
            if not batch:
                break
            trades.extend(batch)
            if page < args.pages:
                time.sleep(NANSEN_SLEEP)
        return trades, "nansen", None

    limit = int(os.environ.get("GMGN_LIMIT", str(args.per_page or 100)))
    trades, gmgn_err = fetch_gmgn_smartmoney(chain, limit=limit, side="buy")
    if trades:
        return trades, "gmgn", None

    # GMGN failed → on-chain best-effort
    print(f"GMGN unavailable (err={gmgn_err}); trying on-chain fallback", file=sys.stderr)
    oc = fetch_onchain_fallback(watch_set, chain, min_usd=0)  # USD often missing
    if oc:
        return oc, "onchain", gmgn_err
    return [], "none", gmgn_err


def run_once(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    load_box_secrets()
    write_gmgn_dotenv()
    live_trading_blocked()

    webhook, paper_webhook = resolve_discord_webhooks()
    chain = os.environ.get("CHAIN", "robinhood").strip().lower()
    window = int(os.environ.get("WINDOW_SECONDS", "900"))
    min_wallets = int(os.environ.get("MIN_WALLETS", "2"))
    min_usd = float(os.environ.get("MIN_TRADE_USD", "50"))
    cooldown = int(os.environ.get("COOLDOWN_SECONDS", str(DEFAULT_COOLDOWN_SECONDS)))
    min_realized = float(os.environ.get("WATCH_MIN_REALIZED_USD", "0"))
    allow_cluster = env_bool("ALLOW_GMGN_CLUSTER", False)
    default_wl = default_watchlist_path(chain)
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(default_wl))).resolve()
    if not watch_path.exists():
        alt = (ROOT / "../rh-wallets/wallets.jsonl").resolve()
        if alt.exists() and chain != "arc":
            watch_path = alt
    state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json"))).resolve()
    paper_path = Path(os.environ.get("PAPER_LOG_PATH", str(ROOT / "paper_log.jsonl"))).resolve()
    book_path = Path(os.environ.get("PAPER_BOOK_PATH", str(ROOT / "paper_book.jsonl"))).resolve()

    watch, raw_count, fallback = load_watchlist(watch_path, min_realized)
    watch_set = set(watch.keys())
    print(
        f"watchlist raw={raw_count} filtered={len(watch_set)} "
        f"min_realized={min_realized} fallback={fallback} "
        f"NANSEN_FOR_TRADES={int(env_bool('NANSEN_FOR_TRADES', False))} "
        f"ALLOW_GMGN_CLUSTER={int(allow_cluster)} chain={chain} "
        f"PAPER_BANKROLL_USD={paper_mod.bankroll_usd()}"
    )

    state = load_state(state_path)
    paper_mod.ensure_paper_state(state)

    # Multiplier milestones + paper marks (works even if GMGN auth fails)
    fu = process_multiplier_followups(state, webhook, chain, paper_path)
    paper_stats = paper_mod.process_paper_positions(
        state,
        book_path,
        chain,
        fetch_dexscreener,
        webhook=paper_webhook,
        discord_post=discord_webhook,
    )
    if fu or paper_stats.get("marked") or paper_stats.get("half") or paper_stats.get("stop"):
        save_state(state_path, state)

    trades, source_name, gmgn_err = collect_trades(args, chain, min_usd, watch_set)

    if gmgn_err == "auth":
        print(
            "AUTH_KEY_INVALID / GMGN key bad — soft exit after multiplier/paper updates. "
            "Set a real GMGN_API_KEY (box-secrets desktop + GitHub secret).",
            file=sys.stderr,
        )
        append_paper_log(
            paper_path,
            {"event": "gmgn_auth_invalid", "posted": False, "source": source_name},
        )

    if not trades:
        print(
            f"no trades source={source_name} gmgn_err={gmgn_err}; "
            f"done (followups={fu} paper={paper_stats})"
        )
        state["last_poll_ts"] = datetime.now(timezone.utc).isoformat()
        state["watch_size"] = len(watch_set)
        state["trade_source"] = source_name
        state["gmgn_err"] = gmgn_err
        save_state(state_path, state)
        summary_path = Path(os.environ.get("PAPER_SUMMARY_PATH", str(ROOT / "paper_summary.md"))).resolve()
        try:
            paper_mod.write_paper_summary(
                paper_path, state_path, book_path, summary_path, load_state, milestones=MULTIPLIER_MILESTONES
            )
        except Exception as e:
            print(f"paper_summary fail: {type(e).__name__}", file=sys.stderr)
        return 0

    # Watchlist-strict: only post when ≥2 makers in filtered watchlist
    signals_wl = detect_signals(trades, watch_set, window, min_wallets, min_usd)
    source_mode = "watchlist"
    if signals_wl:
        signals = signals_wl
        source_mode = "watchlist"
    elif allow_cluster:
        all_makers = {(t.get("trader_address") or "").lower() for t in trades}
        all_makers.discard("")
        signals = detect_signals(trades, all_makers, window, min_wallets, min_usd)
        source_mode = (
            "gmgn_cluster"
            if source_name == "gmgn"
            else ("onchain" if source_name == "onchain" else "gmgn_cluster")
        )
        print(
            f"ALLOW_GMGN_CLUSTER=1; watchlist=0 using {source_mode} "
            f"makers={len(all_makers)} signals={len(signals)}"
        )
    else:
        signals = []
        source_mode = "watchlist"
        print("watchlist-strict: no watchlist overlap signals (cluster disabled)")

    seen = set(state.get("seen_signal_keys") or [])
    ca_last: dict = dict(state.get("ca_last_posted") or {})
    open_alerts: list = list(state.get("open_alerts") or [])
    now = time.time()
    posted = 0
    skipped = 0
    skip_notices = 0

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
            "source": source_name,
            "source_mode": source_mode,
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
            if skip_notices < MAX_SKIP_NOTICES_PER_RUN:
                try:
                    discord_webhook(webhook, embeds=[build_skip_embed(s, safety, chain)])
                    skip_notices += 1
                except Exception as e:
                    print(f"skip notice failed: {type(e).__name__}", file=sys.stderr)
            seen.add(s["key"])
            skipped += 1
            continue

        embed = build_embed(s, chain, safety, source_mode)
        resp = discord_webhook(webhook, content="", embeds=[embed])
        msg_id = None
        if isinstance(resp, dict):
            msg_id = resp.get("id")
        seen.add(s["key"])
        ca_last[ca] = now
        alert = {
            "ca": ca,
            "symbol": s.get("symbol") or safety.get("symbol_hint"),
            "alert_price_usd": safety.get("price_usd"),
            "alert_mcap": safety.get("mcap_usd") or safety.get("fdv"),
            "alert_liq": safety.get("liq_usd"),
            "posted_at": now,
            "message_id": msg_id,
            "milestones_hit": [],
            "source_mode": source_mode,
        }
        open_alerts = [a for a in open_alerts if (a.get("ca") or "").lower() != ca]
        open_alerts.append(alert)
        posted += 1
        print(
            f"posted {ca} n={s['n']} total_usd={total_usd:.0f} "
            f"mcap={safety.get('mcap_usd')} liq={safety.get('liq_usd')} ratio={safety.get('ratio')}"
        )
        append_paper_log(
            paper_path,
            {
                **base_row,
                "posted": True,
                "reason": "posted",
                "safety_jp": safety.get("jp"),
                "ratio": safety.get("ratio"),
                "mcap": safety.get("mcap_usd"),
                "liq": safety.get("liq_usd"),
                "goplus": safety.get("goplus"),
                "message_id": msg_id,
            },
        )
        # Virtual paper entry (FOUNDATION: 1 pos, 20/30%, week caps)
        if safety.get("price_usd"):
            paper_mod.open_paper_position(
                state,
                book_path,
                ca=ca,
                symbol=s.get("symbol") or safety.get("symbol_hint"),
                entry_price=float(safety["price_usd"]),
                n=int(s["n"]),
                chain=chain,
                mcap=safety.get("mcap_usd") or safety.get("fdv"),
                liq=safety.get("liq_usd"),
                webhook=paper_webhook,
                discord_post=discord_webhook,
            )
        time.sleep(0.5)

    state["seen_signal_keys"] = list(seen)
    state["ca_last_posted"] = ca_last
    state["open_alerts"] = open_alerts
    state["last_poll_ts"] = datetime.now(timezone.utc).isoformat()
    state["last_trade_rows"] = len(trades)
    state["watch_size"] = len(watch_set)
    state["watch_raw"] = raw_count
    state["watch_fallback"] = fallback
    state["trade_source"] = source_name
    state["source_mode"] = source_mode
    state["gmgn_err"] = gmgn_err
    save_state(state_path, state)
    summary_path = Path(os.environ.get("PAPER_SUMMARY_PATH", str(ROOT / "paper_summary.md"))).resolve()
    try:
        paper_mod.write_paper_summary(
            paper_path, state_path, book_path, summary_path, load_state, milestones=MULTIPLIER_MILESTONES
        )
    except Exception as e:
        print(f"paper_summary fail: {type(e).__name__}", file=sys.stderr)
    print(
        f"done source={source_name}/{source_mode} trades={len(trades)} signals={len(signals)} "
        f"posted={posted} skipped={skipped} skip_notices={skip_notices} "
        f"followups={fu} paper={paper_stats} watch={len(watch_set)}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="poll forever")
    p.add_argument("--test-webhook", action="store_true")
    p.add_argument("--refresh-wallets", action="store_true", help="Nansen pnl-leaderboard → wallets.jsonl (infrequent)")
    p.add_argument("--paper-summary", action="store_true", help="Write paper_summary.md from logs/state")
    p.add_argument("--pages", type=int, default=2, help="legacy Nansen pages if NANSEN_FOR_TRADES=1")
    p.add_argument("--per-page", type=int, default=100, help="GMGN --limit / Nansen per_page")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    load_box_secrets()
    live_trading_blocked()

    if args.test_webhook:
        url = env("DISCORD_WEBHOOK_URL")
        discord_webhook(
            url,
            content="",
            embeds=[
                {
                    "title": "接続OK",
                    "description": (
                        "通知のみ・紙トレード仮想$300・実注文なし"
                        "（GMGN主・Nansenは財布更新のみ）"
                    ),
                    "color": 0x2ECC71,
                }
            ],
        )
        print("test ok")
        return 0

    if args.paper_summary:
        state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json"))).resolve()
        paper_path = Path(os.environ.get("PAPER_LOG_PATH", str(ROOT / "paper_log.jsonl"))).resolve()
        book_path = Path(os.environ.get("PAPER_BOOK_PATH", str(ROOT / "paper_book.jsonl"))).resolve()
        out_path = Path(os.environ.get("PAPER_SUMMARY_PATH", str(ROOT / "paper_summary.md"))).resolve()
        paper_webhook = resolve_paper_webhook()
        paper_mod.write_paper_summary(
            paper_path,
            state_path,
            book_path,
            out_path,
            load_state,
            milestones=MULTIPLIER_MILESTONES,
            webhook=paper_webhook,
            discord_post=discord_webhook if paper_webhook else None,
        )
        return 0

    if args.refresh_wallets:
        chain = os.environ.get("CHAIN", "robinhood").strip().lower()
        watch_path = Path(
            os.environ.get("WATCHLIST_PATH", str(default_watchlist_path(chain)))
        ).resolve()
        return refresh_wallets_nansen(watch_path, pages=max(1, args.pages))

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

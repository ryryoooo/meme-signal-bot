#!/usr/bin/env python3
"""Standalone RH/Arc meme overlap signal → Discord webhook.

Primary trade source: GMGN smartmoney (gmgn-cli) + FOMO leaderboard buys (throttled).
Watchlist-strict by default (ALLOW_GMGN_CLUSTER=0).
Nansen: optional wallet-list refresh only (--refresh-wallets), NOT dex-trades polling.
LIVE_TRADING: Arc-only real swaps when LIVE_TRADING=1 (RH stays paper/notify).
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
import gmgn_token as gmgn_tok
import live_trade as live_mod

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
        "goplus_id": None,  # GoPlus may skip Arc for now
        "explorer": "https://arc-scan.org/token/",
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

# Heat gates from 2x+ alert sample (provisional): winners had cluster≥~$208, liq≥~$1.6k
LIQ_MCAP_MIN = float(os.environ.get("LIQ_MCAP_MIN", "0.15"))
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "2500"))
MIN_MCAP_USD = float(os.environ.get("MIN_MCAP_USD", "5000"))
MIN_CLUSTER_USD = float(os.environ.get("MIN_CLUSTER_USD", "150"))
MIN_VOLUME_H24_USD = float(os.environ.get("MIN_VOLUME_H24_USD", "8000"))
MIN_VOLUME_M5_USD = float(os.environ.get("MIN_VOLUME_M5_USD", "800"))
MIN_BUY_VOLUME_M5_USD = float(os.environ.get("MIN_BUY_VOLUME_M5_USD", "500"))  # life
MIN_BUYS_M5 = int(os.environ.get("MIN_BUYS_M5", "8"))
REQUIRE_GRADUATED = (os.environ.get("REQUIRE_GRADUATED") or "0").strip().lower() in ("1", "true", "yes")
ALLOW_PRE_GRAD = (os.environ.get("ALLOW_PRE_GRAD") or "1").strip().lower() in ("1", "true", "yes")
PRE_GRAD_MIN_VOLUME_M5 = float(os.environ.get("PRE_GRAD_MIN_VOLUME_M5", "800"))
REQUIRE_BUY_INCREASE = (os.environ.get("REQUIRE_BUY_INCREASE") or "1").strip().lower() in ("1", "true", "yes")
MIN_M5_SELL_RATIO = float(os.environ.get("MIN_M5_SELL_RATIO", "0"))  # 0=off; set >0 for two-way
MIN_ABS_PRICE_CHANGE_M5 = float(os.environ.get("MIN_ABS_PRICE_CHANGE_M5", "3"))  # abs %; flat charts fail
REQUIRE_PRICE_MOVE = (os.environ.get("REQUIRE_PRICE_MOVE") or "1").strip().lower() in ("1", "true", "yes")
MIN_ABS_PRICE_MOVE_H1 = float(os.environ.get("MIN_ABS_PRICE_MOVE_H1", "5"))  # OR with m5 when REQUIRE_PRICE_MOVE
MAX_PRICE_CHANGE_M5_PCT = float(os.environ.get("MAX_PRICE_CHANGE_M5_PCT", "60"))
MAX_PRICE_CHANGE_H1_PCT = float(os.environ.get("MAX_PRICE_CHANGE_H1_PCT", "250"))
MAX_M5_BUY_RATIO = float(os.environ.get("MAX_M5_BUY_RATIO", "0.92"))  # one-sided tape
DROP_BOT_WALLETS = (os.environ.get("DROP_BOT_WALLETS") or "1").strip().lower() in ("1", "true", "yes")
MIN_HOLDERS = int(os.environ.get("MIN_HOLDERS", "80"))
HOLDERS_REQUIRED = (os.environ.get("HOLDERS_REQUIRED") or "0").strip().lower() in ("1", "true", "yes")
MIN_WALLET_QUALITY = float(os.environ.get("MIN_WALLET_QUALITY", "1.0"))
MIN_AVG_WALLET_QUALITY = float(os.environ.get("MIN_AVG_WALLET_QUALITY", "0.6"))
DROP_WEAK_WALLETS = (os.environ.get("DROP_WEAK_WALLETS") or "1").strip().lower() in ("1", "true", "yes")
WEAK_WALLET_MAX_SCORE = float(os.environ.get("WEAK_WALLET_MAX_SCORE", "0.5"))
WATCH_MIN_REALIZED_HARD = float(os.environ.get("WATCH_MIN_REALIZED_HARD", "500"))
REQUIRE_CONSISTENT_PNL = (os.environ.get("REQUIRE_CONSISTENT_PNL") or "1").strip().lower() in ("1", "true", "yes")
WATCH_MIN_WINRATE = float(os.environ.get("WATCH_MIN_WINRATE", "0.45"))
WATCH_MIN_TRADES = int(os.environ.get("WATCH_MIN_TRADES", "15"))
WATCH_MIN_AVG_PNL_PER_TRADE = float(os.environ.get("WATCH_MIN_AVG_PNL_PER_TRADE", "50"))
ALLOW_FOMO_WITHOUT_WR = (os.environ.get("ALLOW_FOMO_WITHOUT_WR") or "0").strip().lower() in ("1", "true", "yes")
MIN_TOKEN_AGE_SEC = int(os.environ.get("MIN_TOKEN_AGE_SEC", "900"))  # legacy floor
MAX_TOKEN_AGE_SEC = int(os.environ.get("MAX_TOKEN_AGE_SEC", "604800"))  # 7d hard cap (0=off)
# Playbook windows (sec)
SET1_MIN_AGE_SEC = int(os.environ.get("SET1_MIN_AGE_SEC", "1800"))  # 30m
SET1_MAX_AGE_SEC = int(os.environ.get("SET1_MAX_AGE_SEC", "172800"))  # 48h
SET2_MIN_AGE_SEC = int(os.environ.get("SET2_MIN_AGE_SEC", "172800"))  # 48h
SET2_MAX_AGE_SEC = int(os.environ.get("SET2_MAX_AGE_SEC", "604800"))  # 7d
SET1_MIN_PUMP_PCT = float(os.environ.get("SET1_MIN_PUMP_PCT", "40"))  # first leg
SET1_CORR_M5_MAX = float(os.environ.get("SET1_CORR_M5_MAX", "25"))  # not still parabolic on m5
SET2_MIN_DUMP_PCT = float(os.environ.get("SET2_MIN_DUMP_PCT", "-50"))  # h24 ≤ this (drawdown)
SET2_MAX_M5_ABS = float(os.environ.get("SET2_MAX_M5_ABS", "10"))  # sideways
SET2_MAX_H1_ABS = float(os.environ.get("SET2_MAX_H1_ABS", "30"))
SET2_MIN_VOLUME_H24 = float(os.environ.get("SET2_MIN_VOLUME_H24", "8000"))
PLAYBOOK_REQUIRED = (os.environ.get("PLAYBOOK_REQUIRED") or "1").strip().lower() in ("1", "true", "yes")
LP_LOCK_MIN = 0.01  # locked+burned share of LP
# LP burn/lock is advisory by default (RH UniV3 often reports locked=0).
# Set LP_LOCK_REQUIRED=1 to hard-fail unlocked LP again.
LP_DOMINATE_MAX = 0.30  # top unlocked LP holder
BURN_LP_ADDRS = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000000000000000000000000",
}
MULTIPLIER_MILESTONES = (1.5, 2.0, 3.0, 5.0)
ALERT_MAX_AGE_SEC = 24 * 3600
FOLLOWUP_COOLDOWN_SEC = 30 * 60
NANSEN_SLEEP = 0.8
DEFAULT_COOLDOWN_SECONDS = 7200  # 2h
MAX_SKIP_NOTICES_PER_RUN = 3
# Paper: $300 bankroll, FOUNDATION risk (max 5 open, 20/30%, +100% half, -40% stop; week caps off by default)
# LIVE_TRADING: Arc-only when LIVE_TRADING=1 + LIVE_CHAINS includes arc. RH never live-trades.



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


def resolve_paper_webhook(signal: str | None = None, chain: str | None = None) -> str | None:
    """Paper-channel webhook; falls back to signal webhook with a warning if unset."""
    chain = (chain or os.environ.get("CHAIN") or "robinhood").strip().lower()
    if chain == "arc":
        arc_paper = (
            os.environ.get("DISCORD_ARC_PAPER")
            or os.environ.get("DISCORD_ARC_PAPER_WEBHOOK_URL")
            or ""
        ).strip()
        if arc_paper:
            return arc_paper
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


def resolve_signal_webhook(chain: str | None = None) -> str:
    """Prefer DISCORD_ARC_WEBHOOK_URL when CHAIN=arc; else DISCORD_WEBHOOK_URL."""
    chain = (chain or os.environ.get("CHAIN") or "robinhood").strip().lower()
    if chain == "arc":
        arc = (os.environ.get("DISCORD_ARC_WEBHOOK_URL") or "").strip()
        if arc:
            return arc
    return env("DISCORD_WEBHOOK_URL")


def resolve_live_webhook(chain: str | None = None) -> str | None:
    """Arc LIVE-channel webhook; falls back to Arc signal then paper with WARNING."""
    chain = (chain or os.environ.get("CHAIN") or "robinhood").strip().lower()
    live = (
        os.environ.get("DISCORD_ARC_LIVE_WEBHOOK_URL")
        or os.environ.get("DISCORD_ARC_LIVE")
        or ""
    ).strip()
    if chain == "arc" and live:
        return live
    if chain == "arc":
        arc = (os.environ.get("DISCORD_ARC_WEBHOOK_URL") or "").strip()
        if arc:
            print(
                "WARNING: DISCORD_ARC_LIVE_WEBHOOK_URL missing — live posts fall back to DISCORD_ARC_WEBHOOK_URL",
                file=sys.stderr,
            )
            return arc
    paper = resolve_paper_webhook(None, chain)
    if paper:
        print(
            "WARNING: DISCORD_ARC_LIVE_WEBHOOK_URL missing — live posts fall back to paper webhook",
            file=sys.stderr,
        )
        return paper
    return None


def resolve_discord_webhooks() -> tuple[str, str]:
    """Signal webhook (required) + paper webhook (optional, falls back with warning)."""
    chain = (os.environ.get("CHAIN") or "robinhood").strip().lower()
    signal = resolve_signal_webhook(chain)
    paper = resolve_paper_webhook(signal, chain)
    assert paper  # signal non-empty ⇒ fallback always yields a URL
    return signal, paper


def http_get_json(url: str, headers: dict | None = None, timeout: int = 25) -> dict | list | None:
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept": "application/json"}
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


def _wallet_realized(o: dict) -> float:
    for key in ("realized_pnl_usd", "pnlUsd", "pnl_usd", "gmgn_pnl_usd"):
        v = _num(o.get(key))
        if v is not None:
            return v
    return 0.0


def _is_fomo_only(o: dict) -> bool:
    srcs = [str(s) for s in (o.get("source_endpoints") or [])]
    others = [s for s in srcs if s != "fomo_leaderboard"]
    return (not others) and bool(srcs or o.get("fomo_handle"))



_BAD_WALLET_LABEL = re.compile(
    r"(?:\bteam\b|deployer|creator|dev\s*wallet|copy\s*trad|bundler|"
    r"sniper\s*bot|label\s*only|mev\s*bot|\bbot\b|sandwich|phish|"
    r"rat[_\s-]?trader|dex[_\s-]?bot|scam)",
    re.I,
)

_BOT_TAG_SUBSTR = (
    "bundler", "sniper", "rat_trader", "dex_bot", "sandwich", "mev",
    "phish", "scammer", "copy_bot", "bot_", "_bot",
)


def wallet_label_banned(o: dict) -> bool:
    lab = str(o.get("address_label") or o.get("label") or "")
    return bool(_BAD_WALLET_LABEL.search(lab))


def wallet_looks_bot(meta: dict | None) -> bool:
    """Heuristic: label/tags look like bot/sniper/copy infra — drop from signal overlap."""
    if not meta:
        return False
    if wallet_label_banned(meta):
        return True
    parts = []
    for key in ("tags", "sources", "source_endpoints", "labels", "gmgn_tags"):
        for x in meta.get(key) or []:
            parts.append(str(x).lower())
    for key in ("source", "address_label", "label", "quality_reason"):
        v = meta.get(key)
        if v:
            parts.append(str(v).lower())
    blob = " ".join(parts)
    if any(s in blob for s in _BOT_TAG_SUBSTR):
        return True
    # fresh_wallet alone with no early/nansen/profit track → bot-farm suspicion
    if "fresh_wallet" in blob and not any(
        s in blob for s in ("early", "nansen", "smart trader", "token_profit", "profit")
    ):
        if env_bool("EXCLUDE_FRESH_WALLET_ONLY", False):
            return True
    return False


def _wallet_winrate(o: dict) -> float | None:
    wr = o.get("win_rate")
    if wr is None:
        wr = o.get("gmgn_winrate")
    wrn = _num(wr)
    if wrn is None:
        return None
    if wrn > 1.0:
        wrn = wrn / 100.0
    return wrn


def _wallet_n_trades(o: dict) -> int:
    for key in ("n_trades", "trades", "trade_count"):
        try:
            v = int(o.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return 0


def _multi_hit_profit(o: dict) -> bool:
    """Repeat edge without win_rate: ≥2 early-2x tokens or ≥3 tokens seen."""
    try:
        min_tok = int(float(os.environ.get("WATCH_MIN_PROFIT_TOKENS", "3")))
    except (TypeError, ValueError):
        min_tok = 3
    try:
        min_e2 = int(float(os.environ.get("WATCH_MIN_EARLY2X_TOKENS", "2")))
    except (TypeError, ValueError):
        min_e2 = 2
    try:
        n_seen = int(o.get("n_tokens_seen") or o.get("n_tokens") or 0)
    except (TypeError, ValueError):
        n_seen = 0
    e2 = o.get("early_2x_tokens") or []
    if isinstance(e2, (str, int)):
        e2 = [e2]
    try:
        e2n = len(list(e2))
    except TypeError:
        e2n = 0
    if e2n >= min_e2:
        return True
    if n_seen >= min_tok:
        return True
    return False


def wallet_is_consistent(o: dict) -> bool:
    """Average/repeat winners only — lucky one-shot PnL is noise."""
    if not env_bool("REQUIRE_CONSISTENT_PNL", True):
        return True
    try:
        min_wr = float(os.environ.get("WATCH_MIN_WINRATE", str(WATCH_MIN_WINRATE)))
    except (TypeError, ValueError):
        min_wr = WATCH_MIN_WINRATE
    try:
        min_n = int(float(os.environ.get("WATCH_MIN_TRADES", str(WATCH_MIN_TRADES))))
    except (TypeError, ValueError):
        min_n = WATCH_MIN_TRADES
    try:
        min_n = max(min_n, int(float(os.environ.get("WATCH_MIN_TRADES_FOR_WR", "0") or 0)))
    except (TypeError, ValueError):
        pass
    try:
        min_avg = float(os.environ.get("WATCH_MIN_AVG_PNL_PER_TRADE", str(WATCH_MIN_AVG_PNL_PER_TRADE)))
    except (TypeError, ValueError):
        min_avg = WATCH_MIN_AVG_PNL_PER_TRADE
    wrn = _wallet_winrate(o)
    nt = _wallet_n_trades(o)
    rp = _wallet_realized(o)
    if env_bool("ALLOW_FOMO_WITHOUT_WR", False) and _is_fomo_only(o) and wrn is None:
        return rp > 0
    # Path A: win-rate track record (preferred)
    if wrn is not None and nt >= min_n:
        if wrn < min_wr:
            return False
        if min_avg > 0 and nt > 0 and (rp / nt) < min_avg:
            return False
        return True
    # Path B: multi-token repeat edge (Arc-style, no WR yet) — single-token profit = lucky noise
    if wrn is None and nt < min_n and _multi_hit_profit(o):
        return rp > 0
    return False


def wallet_passes_filter(o: dict, min_realized: float) -> bool:
    """Keep consistent average winners. Lucky one-shot PnL is noise."""
    rp = _wallet_realized(o)
    if rp <= 0:
        return False
    if min_realized > 0 and rp < min_realized:
        return False
    if wallet_label_banned(o):
        return False
    if not wallet_is_consistent(o):
        return False
    return True



def wallet_is_early_stage(meta: dict | None) -> bool:
    """True if wallet looks like early / 仕込み smart money (not late chase only)."""
    if not meta:
        return False
    parts: list[str] = []
    for key in ("tags", "gmgn_tags", "sources", "source_endpoints"):
        for x in meta.get(key) or []:
            parts.append(str(x))
    for key in ("source", "address_label", "label"):
        v = meta.get(key)
        if v:
            parts.append(str(v))
    blob = " ".join(parts).lower()
    if any(
        s in blob
        for s in (
            "early2x",
            "early_live",
            "early:",
            "early ",
            "xbtscout_pre_post",
            "xbtscout_pre",
            "xbtscout_early",
            "nansen",
            "smart trader",
            "30d smart",
            "90d smart",
            "180d smart",
        )
    ):
        return True
    # bare tag/source token starting with early
    for p in parts:
        pl = str(p).lower().strip()
        if pl.startswith("early") or pl == "nansen" or pl.startswith("nansen:"):
            return True
    # bare kol / gmgn_kol is often late chase — never early by itself
    return False



def wallet_quality_score(meta: dict | None) -> float:
    """0–3-ish score for smart-wallet quality (仕込み + PnL track record)."""
    if not meta:
        return 0.0
    score = 0.0
    if wallet_is_early_stage(meta):
        score += 1.0
    try:
        rp = float(_wallet_realized(meta))
    except Exception:
        try:
            rp = float(meta.get("realized_pnl_usd") or meta.get("total_pnl_usd") or 0)
        except (TypeError, ValueError):
            rp = 0.0
    if rp >= 10_000:
        score += 1.0
    elif rp >= 1_000:
        score += 0.7
    elif rp >= 100:
        score += 0.4
    elif rp > 0:
        score += 0.2
    try:
        wr = float(meta.get("win_rate") or 0)
        if wr > 1:
            wr = wr / 100.0
    except (TypeError, ValueError):
        wr = 0.0
    try:
        nt = int(meta.get("n_trades") or 0)
    except (TypeError, ValueError):
        nt = 0
    if wr is None or wr == 0.0:
        wr = float(_wallet_winrate(meta) or 0)
    if nt >= 15 and wr >= 0.45:
        score += 0.8
    elif nt >= 10 and wr >= 0.45:
        score += 0.5
    elif nt >= 5 and wr >= 0.40:
        score += 0.15
    # penalize missing track record (lucky / unknown)
    if nt < 10 or wr < 0.40:
        score = min(score, 0.4)
    blob = " ".join(
        str(x)
        for x in list(meta.get("tags") or [])
        + list(meta.get("sources") or [])
        + list(meta.get("source_endpoints") or [])
        + [meta.get("source") or "", meta.get("list_tier") or ""]
    ).lower()
    if "nansen" in blob:
        score += 0.5
    if "early2x" in blob or "early_live" in blob:
        score += 0.3
    if str(meta.get("list_tier") or "").lower() in ("quality", "keeper", "a"):
        score += 0.2
    # KOL / renowned bonus only when consistent average winner (KOL alone = noise)
    kolish = any(
        t in blob
        for t in ("kol", "gmgn_kol", "renowned")
    ) or any(
        str(x).lower() in ("kol", "gmgn_kol", "renowned")
        for x in list(meta.get("tags") or []) + list(meta.get("gmgn_tags") or []) + list(meta.get("sources") or [])
    )
    if kolish and wallet_is_consistent(meta):
        score += 0.3
    return round(score, 3)



def _pair_age_sec(safety: dict) -> float | None:
    for key in ("token_age_sec", "age_sec", "pair_age_sec"):
        if safety.get(key) is not None:
            try:
                return float(safety[key])
            except (TypeError, ValueError):
                pass
    if safety.get("pair_created_at_ms"):
        try:
            import time as _time
            return max(0.0, _time.time() - float(safety["pair_created_at_ms"]) / 1000.0)
        except (TypeError, ValueError):
            return None
    return None


def classify_playbook(safety: dict) -> tuple[str | None, list[str]]:
    """Return (set1|set2|None, reason bits). OR of two entry playbooks."""
    notes: list[str] = []
    age = _pair_age_sec(safety)
    if age is None:
        return None, ["age_na"]

    def _f(key):
        try:
            v = safety.get(key)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    pcm5 = _f("price_change_m5")
    pch1 = _f("price_change_h1")
    pch6 = _f("price_change_h6")
    pch24 = _f("price_change_h24")
    vol24 = _f("volume_h24")
    bm = safety.get("buys_m5")
    sm = safety.get("sells_m5")
    try:
        bm_i = int(bm) if bm is not None else None
        sm_i = int(sm) if sm is not None else None
    except (TypeError, ValueError):
        bm_i = sm_i = None

    try:
        s1_lo = float(os.environ.get("SET1_MIN_AGE_SEC", str(SET1_MIN_AGE_SEC)))
        s1_hi = float(os.environ.get("SET1_MAX_AGE_SEC", str(SET1_MAX_AGE_SEC)))
        s2_lo = float(os.environ.get("SET2_MIN_AGE_SEC", str(SET2_MIN_AGE_SEC)))
        s2_hi = float(os.environ.get("SET2_MAX_AGE_SEC", str(SET2_MAX_AGE_SEC)))
        min_pump = float(os.environ.get("SET1_MIN_PUMP_PCT", str(SET1_MIN_PUMP_PCT)))
        corr_m5 = float(os.environ.get("SET1_CORR_M5_MAX", str(SET1_CORR_M5_MAX)))
        dump_need = float(os.environ.get("SET2_MIN_DUMP_PCT", str(SET2_MIN_DUMP_PCT)))
        flat_m5 = float(os.environ.get("SET2_MAX_M5_ABS", str(SET2_MAX_M5_ABS)))
        flat_h1 = float(os.environ.get("SET2_MAX_H1_ABS", str(SET2_MAX_H1_ABS)))
        s2_vol = float(os.environ.get("SET2_MIN_VOLUME_H24", str(SET2_MIN_VOLUME_H24)))
    except (TypeError, ValueError):
        return None, ["playbook_env_bad"]

    # --- Set 1: fresh story coin after first pump + correction → reversal ---
    if s1_lo <= age <= s1_hi:
        pump_legs = [x for x in (pch1, pch6, pch24) if x is not None]
        had_pump = any(x >= min_pump for x in pump_legs) if pump_legs else False
        # correction: m5 cooled (not parabolic) OR h1 much cooler than h6/h24
        cooled = False
        if pcm5 is not None and pcm5 <= corr_m5:
            cooled = True
        if pch1 is not None and pch24 is not None and pch24 >= min_pump and pch1 < pch24 * 0.5:
            cooled = True
        # reversal: buys > sells on m5 and m5 turning up
        reversing = False
        if bm_i is not None and sm_i is not None and bm_i > sm_i and pcm5 is not None and pcm5 > 0:
            reversing = True
        if had_pump and cooled and reversing:
            notes.append(f"set1_age={int(age)}s pump/corr/rev")
            return "set1", notes
        notes.append(
            f"set1_miss pump={had_pump} cool={cooled} rev={reversing} age={int(age)}s"
        )

    # --- Set 2: 48h–7d survivor, deep dump, real vol, sideways base ---
    if s2_lo <= age <= s2_hi:
        dumped = pch24 is not None and pch24 <= dump_need
        vol_ok = vol24 is not None and vol24 >= s2_vol
        side = True
        if pcm5 is not None and abs(pcm5) > flat_m5:
            side = False
        if pch1 is not None and abs(pch1) > flat_h1:
            side = False
        # some two-way activity if we have tape
        twoway = True
        if bm_i is not None and sm_i is not None and (bm_i + sm_i) >= 6:
            twoway = sm_i >= 1 and bm_i >= 1
        if dumped and vol_ok and side and twoway:
            notes.append(f"set2_age={int(age)}s dump={pch24} vol={vol24}")
            return "set2", notes
        notes.append(
            f"set2_miss dump={dumped} vol={vol_ok} side={side} twoway={twoway} age={int(age)}s"
        )

    if age < s1_lo:
        notes.append(f"too_new_for_sets={int(age)}s")
    elif age > s2_hi:
        notes.append(f"too_old_for_sets={int(age)}s")
    else:
        notes.append(f"in_gap_or_miss age={int(age)}s")
    return None, notes


def notify_market_gate_reasons(safety: dict, total_usd: float, wallet_scores: list[float]) -> list[str]:
    """Notify gates: graduated + m5 vol + anti-spike + volume/holders/buys/quality."""
    fails: list[str] = []
    try:
        min_vol = float(os.environ.get("MIN_VOLUME_H24_USD", str(MIN_VOLUME_H24_USD)))
    except (TypeError, ValueError):
        min_vol = MIN_VOLUME_H24_USD
    try:
        min_vol_m5 = float(os.environ.get("MIN_VOLUME_M5_USD", str(MIN_VOLUME_M5_USD)))
    except (TypeError, ValueError):
        min_vol_m5 = MIN_VOLUME_M5_USD
    try:
        min_holders = int(float(os.environ.get("MIN_HOLDERS", str(MIN_HOLDERS))))
    except (TypeError, ValueError):
        min_holders = MIN_HOLDERS
    holders_required = env_bool("HOLDERS_REQUIRED", HOLDERS_REQUIRED)
    require_grad = env_bool("REQUIRE_GRADUATED", REQUIRE_GRADUATED)
    try:
        max_m5 = float(os.environ.get("MAX_PRICE_CHANGE_M5_PCT", str(MAX_PRICE_CHANGE_M5_PCT)))
    except (TypeError, ValueError):
        max_m5 = MAX_PRICE_CHANGE_M5_PCT
    try:
        max_h1 = float(os.environ.get("MAX_PRICE_CHANGE_H1_PCT", str(MAX_PRICE_CHANGE_H1_PCT)))
    except (TypeError, ValueError):
        max_h1 = MAX_PRICE_CHANGE_H1_PCT
    try:
        max_buy_ratio = float(os.environ.get("MAX_M5_BUY_RATIO", str(MAX_M5_BUY_RATIO)))
    except (TypeError, ValueError):
        max_buy_ratio = MAX_M5_BUY_RATIO
    try:
        min_cluster = float(os.environ.get("MIN_CLUSTER_USD", str(MIN_CLUSTER_USD)))
    except (TypeError, ValueError):
        min_cluster = MIN_CLUSTER_USD
    try:
        min_q = float(os.environ.get("MIN_WALLET_QUALITY", str(MIN_WALLET_QUALITY)))
    except (TypeError, ValueError):
        min_q = MIN_WALLET_QUALITY
    try:
        min_avg_q = float(os.environ.get("MIN_AVG_WALLET_QUALITY", str(MIN_AVG_WALLET_QUALITY)))
    except (TypeError, ValueError):
        min_avg_q = MIN_AVG_WALLET_QUALITY

    # 1) Launchpad: prefer graduated; pre-grad OK if ALLOW_PRE_GRAD + strong m5 heat
    allow_pre = env_bool("ALLOW_PRE_GRAD", ALLOW_PRE_GRAD)
    try:
        pre_m5 = float(os.environ.get("PRE_GRAD_MIN_VOLUME_M5", str(PRE_GRAD_MIN_VOLUME_M5)))
    except (TypeError, ValueError):
        pre_m5 = PRE_GRAD_MIN_VOLUME_M5
    is_grad = bool(safety.get("graduated"))
    is_bond = bool(safety.get("bondingish")) and not is_grad
    vol_m5_early = safety.get("volume_m5")
    try:
        vol_m5_f = float(vol_m5_early) if vol_m5_early is not None else None
    except (TypeError, ValueError):
        vol_m5_f = None
    if require_grad and not is_grad:
        if allow_pre and vol_m5_f is not None and vol_m5_f >= pre_m5:
            pass  # pre-grad candidate via volume
        elif is_bond:
            fails.append("not_graduated_bonding")
        else:
            fails.append("not_graduated")
    elif (not require_grad) and is_bond and allow_pre:
        # soft: bonding only if m5 volume strong
        if vol_m5_f is None or vol_m5_f < pre_m5:
            fails.append(f"pre_grad_volume_m5={vol_m5_f or 0:.0f}<{pre_m5:.0f}")


    # Dual playbook (set1 reversal / set2 survivor base) — primary age logic
    playbook, pb_notes = classify_playbook(safety)
    safety["_playbook"] = playbook
    safety["_playbook_notes"] = pb_notes
    if env_bool("PLAYBOOK_REQUIRED", PLAYBOOK_REQUIRED):
        if playbook is None:
            fails.append("no_playbook:" + (pb_notes[0] if pb_notes else "miss"))
    else:
        # legacy soft floor/ceiling only
        age_sec = _pair_age_sec(safety)
        try:
            min_age = float(os.environ.get("MIN_TOKEN_AGE_SEC", str(MIN_TOKEN_AGE_SEC)))
            max_age = float(os.environ.get("MAX_TOKEN_AGE_SEC", str(MAX_TOKEN_AGE_SEC)))
        except (TypeError, ValueError):
            min_age, max_age = float(MIN_TOKEN_AGE_SEC), float(MAX_TOKEN_AGE_SEC)
        if age_sec is not None:
            if min_age > 0 and age_sec < min_age:
                fails.append(f"launch_too_new={int(age_sec)}s<{int(min_age)}s")
            if max_age > 0 and age_sec > max_age:
                fails.append(f"launch_too_old={int(age_sec)}s>{int(max_age)}s")

    # Liquidity axis (hard): absolute liq + ratio already in heat_gate; reinforce here
    try:
        min_liq = float(os.environ.get("MIN_LIQ_USD", str(MIN_LIQ_USD)))
    except (TypeError, ValueError):
        min_liq = MIN_LIQ_USD
    try:
        liq_mcap_min = float(os.environ.get("LIQ_MCAP_MIN", str(LIQ_MCAP_MIN)))
    except (TypeError, ValueError):
        liq_mcap_min = LIQ_MCAP_MIN
    liq = safety.get("liq_usd")
    mcap = safety.get("mcap_usd") or safety.get("fdv")
    if liq is None:
        fails.append("liq_na")
    else:
        try:
            if float(liq) < min_liq:
                fails.append(f"liq_thin={float(liq):.0f}<{min_liq:.0f}")
        except (TypeError, ValueError):
            fails.append("liq_na")
    ratio = safety.get("ratio")
    if ratio is None and liq is not None and mcap:
        try:
            ratio = float(liq) / float(mcap)
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
    if ratio is not None:
        try:
            if float(ratio) < liq_mcap_min:
                fails.append(f"liq_ratio={float(ratio):.2f}<{liq_mcap_min:.2f}")
        except (TypeError, ValueError):
            pass

    # 2) 5m volume
    vol_m5 = safety.get("volume_m5")
    vol_m5_req = env_bool("VOLUME_M5_REQUIRED", True)
    if vol_m5 is None:
        if vol_m5_req:
            fails.append("volume_m5_na")
    else:
        try:
            if float(vol_m5) < min_vol_m5:
                fails.append(f"volume_m5_thin={float(vol_m5):.0f}<{min_vol_m5:.0f}")
        except (TypeError, ValueError):
            if vol_m5_req:
                fails.append("volume_m5_na")

    # 3) Anti-spike / natural tape
    pcm5 = safety.get("price_change_m5")
    pch1 = safety.get("price_change_h1")
    if pcm5 is not None:
        try:
            if float(pcm5) >= max_m5:
                fails.append(f"spike_m5={float(pcm5):.0f}>={max_m5:.0f}")
            if float(pcm5) <= -max_m5:  # dump candle also unnatural for entry
                fails.append(f"dump_m5={float(pcm5):.0f}")
        except (TypeError, ValueError):
            pass
    if pch1 is not None:
        try:
            if float(pch1) >= max_h1:
                fails.append(f"spike_h1={float(pch1):.0f}>={max_h1:.0f}")
        except (TypeError, ValueError):
            pass
    # one-sided 5m buys (no natural two-way flow)
    bm = safety.get("buys_m5")
    sm = safety.get("sells_m5")
    try:
        bm_i = int(bm) if bm is not None else None
        sm_i = int(sm) if sm is not None else None
    except (TypeError, ValueError):
        bm_i = sm_i = None
    if bm_i is not None and sm_i is not None and (bm_i + sm_i) >= 8:
        ratio = bm_i / max(1, bm_i + sm_i)
        if ratio >= max_buy_ratio:
            fails.append(f"onesided_m5={ratio:.2f}")
        try:
            min_sell_r = float(os.environ.get("MIN_M5_SELL_RATIO", str(MIN_M5_SELL_RATIO)))
        except (TypeError, ValueError):
            min_sell_r = MIN_M5_SELL_RATIO
        sell_r = sm_i / max(1, bm_i + sm_i)
        if sell_r < min_sell_r:
            fails.append(f"no_two_way_m5={sell_r:.2f}<{min_sell_r:.2f}")
    # buys increasing vs sells (候補: 買い優勢だが片側すぎない)
    if env_bool("REQUIRE_BUY_INCREASE", REQUIRE_BUY_INCREASE):
        if bm_i is not None and sm_i is not None:
            if bm_i <= sm_i:
                fails.append(f"buys_not_up_m5={bm_i}<={sm_i}")
        bh = safety.get("buys_h1")
        sh = safety.get("sells_h1")
        try:
            if bh is not None and sh is not None and int(bh) + int(sh) >= 20:
                if int(bh) < int(sh):
                    fails.append(f"buys_not_up_h1={int(bh)}<{int(sh)}")
        except (TypeError, ValueError):
            pass
    # Hard: reject 値動きない charts — at least one TF must move
    try:
        min_abs_m5 = float(os.environ.get("MIN_ABS_PRICE_CHANGE_M5", str(MIN_ABS_PRICE_CHANGE_M5)))
    except (TypeError, ValueError):
        min_abs_m5 = MIN_ABS_PRICE_CHANGE_M5
    try:
        min_abs_h1 = float(os.environ.get("MIN_ABS_PRICE_MOVE_H1", str(MIN_ABS_PRICE_MOVE_H1)))
    except (TypeError, ValueError):
        min_abs_h1 = MIN_ABS_PRICE_MOVE_H1
    pch6 = safety.get("price_change_h6")
    def _abs_or_none(v):
        try:
            return abs(float(v)) if v is not None else None
        except (TypeError, ValueError):
            return None
    abs_m5 = _abs_or_none(pcm5)
    abs_h1 = _abs_or_none(pch1)
    abs_h6 = _abs_or_none(pch6)
    # Completely flat across available TFs
    known = [x for x in (abs_m5, abs_h1, abs_h6) if x is not None]
    if known and all(x < 0.5 for x in known):
        fails.append(
            f"no_price_move m5={pcm5} h1={pch1} h6={pch6}"
        )
    elif env_bool("REQUIRE_PRICE_MOVE", REQUIRE_PRICE_MOVE):
        m5_ok = abs_m5 is not None and abs_m5 >= min_abs_m5
        h1_ok = abs_h1 is not None and abs_h1 >= min_abs_h1
        if abs_m5 is not None or abs_h1 is not None:
            if not (m5_ok or h1_ok):
                fails.append(
                    f"flat_price m5={pcm5} (need>={min_abs_m5}) "
                    f"h1={pch1} (need>={min_abs_h1})"
                )
        elif env_bool("PLAYBOOK_REQUIRED", PLAYBOOK_REQUIRED):
            # missing price series on set2/set1 path — reject dead/unknown
            fails.append("price_change_na")
    elif abs_m5 is not None and min_abs_m5 > 0 and abs_m5 < min_abs_m5:
        fails.append(f"flat_m5={float(pcm5):.1f}")

    # 4) 24h volume (existing)
    vol_required = env_bool("VOLUME_REQUIRED", True)
    vol = safety.get("volume_h24")
    if vol is None:
        if vol_required:
            fails.append("volume_na")
    else:
        try:
            if float(vol) < min_vol:
                fails.append(f"volume_thin={float(vol):.0f}<{min_vol:.0f}")
        except (TypeError, ValueError):
            if vol_required:
                fails.append("volume_na")

    # Buy volume is life: m5 buy-side USD proxy + min buy count
    try:
        min_buy_vol = float(os.environ.get("MIN_BUY_VOLUME_M5_USD", str(MIN_BUY_VOLUME_M5_USD)))
    except (TypeError, ValueError):
        min_buy_vol = MIN_BUY_VOLUME_M5_USD
    try:
        min_buys = int(float(os.environ.get("MIN_BUYS_M5", str(MIN_BUYS_M5))))
    except (TypeError, ValueError):
        min_buys = MIN_BUYS_M5
    vol_m5_v = safety.get("volume_m5")
    bm2 = safety.get("buys_m5")
    sm2 = safety.get("sells_m5")
    try:
        bm2i = int(bm2) if bm2 is not None else None
        sm2i = int(sm2) if sm2 is not None else None
    except (TypeError, ValueError):
        bm2i = sm2i = None
    if bm2i is not None and bm2i < min_buys:
        fails.append(f"buys_m5_thin={bm2i}<{min_buys}")
    if vol_m5_v is not None and bm2i is not None and sm2i is not None and (bm2i + sm2i) > 0:
        try:
            buy_share = bm2i / (bm2i + sm2i)
            buy_vol_usd = float(vol_m5_v) * buy_share
            if buy_vol_usd < min_buy_vol:
                fails.append(f"buy_vol_m5={buy_vol_usd:.0f}<{min_buy_vol:.0f}")
        except (TypeError, ValueError, ZeroDivisionError):
            fails.append("buy_vol_m5_na")
    elif env_bool("BUY_VOLUME_REQUIRED", True):
        fails.append("buy_vol_m5_na")

    holders = safety.get("holder_count")
    if holders is None:
        if holders_required:
            fails.append("holders_na")
    else:
        try:
            if int(float(holders)) < min_holders:
                fails.append(f"holders_thin={int(float(holders))}<{min_holders}")
        except (TypeError, ValueError):
            if holders_required:
                fails.append("holders_na")

    if total_usd < min_cluster:
        fails.append(f"weak_cluster={total_usd:.0f}<{min_cluster:.0f}")

    # Wallet quality is soft unless REQUIRE_WALLET_QUALITY=1 (avoid over-skipping)
    if env_bool("REQUIRE_WALLET_QUALITY", True):
        if not wallet_scores:
            fails.append("quality_na")
        else:
            best = max(wallet_scores)
            avg = sum(wallet_scores) / len(wallet_scores)
            if best < min_q:
                fails.append(f"quality_best={best:.2f}<{min_q:.2f}")
            if avg < min_avg_q:
                fails.append(f"quality_avg={avg:.2f}<{min_avg_q:.2f}")
    return fails



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
    dropped = len(raw) - len(filtered)
    if dropped:
        print(f"watchlist drop losers={dropped} keep={len(filtered)} raw={len(raw)}")
    early_only = env_bool("WATCH_EARLY_ONLY", False)
    if early_only and filtered:
        before = len(filtered)
        filtered = {a: o for a, o in filtered.items() if wallet_is_early_stage(o)}
        print(
            f"watchlist early_only keep={len(filtered)} dropped={before - len(filtered)} before={before}",
            flush=True,
        )
    if env_bool("DROP_BOT_WALLETS", True) and filtered:
        before = len(filtered)
        filtered = {a: o for a, o in filtered.items() if not wallet_looks_bot(o)}
        print(
            f"watchlist drop_bots keep={len(filtered)} dropped={before - len(filtered)} before={before}",
            flush=True,
        )
    if env_bool("DROP_WEAK_WALLETS", True) and filtered:
        before = len(filtered)
        try:
            min_rp = float(os.environ.get("WATCH_MIN_REALIZED_HARD", str(WATCH_MIN_REALIZED_HARD)))
        except (TypeError, ValueError):
            min_rp = WATCH_MIN_REALIZED_HARD
        try:
            weak_max = float(os.environ.get("WEAK_WALLET_MAX_SCORE", str(WEAK_WALLET_MAX_SCORE)))
        except (TypeError, ValueError):
            weak_max = WEAK_WALLET_MAX_SCORE
        kept = {}
        for a, o in filtered.items():
            rp = _wallet_realized(o)
            # Average winners only. Early-tag alone is not enough if PnL is lucky/one-shot.
            if rp < min_rp:
                continue
            if not wallet_is_consistent(o):
                continue
            kept[a] = o
        filtered = kept
        print(
            f"watchlist drop_weak keep={len(filtered)} dropped={before - len(filtered)} "
            f"min_rp={min_rp} weak_max_q={weak_max}",
            flush=True,
        )
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


def live_trading_enabled(chain: str | None = None) -> bool:
    """True only when LIVE_TRADING=1 and chain is Arc (or listed in LIVE_CHAINS). RH never."""
    if not env_bool("LIVE_TRADING", False):
        return False
    ch = (chain or os.environ.get("CHAIN") or "").strip().lower()
    if ch in ("robinhood", "rh"):
        return False
    allowed_raw = (os.environ.get("LIVE_CHAINS") or "arc").strip()
    allowed = {x.strip().lower() for x in allowed_raw.split(",") if x.strip()}
    return ch in allowed


def live_trading_blocked() -> None:
    """Startup note: LIVE_TRADING only applies to allowed chains (Arc). RH stays paper."""
    if not env_bool("LIVE_TRADING", False):
        return
    chain = (os.environ.get("CHAIN") or "").strip().lower()
    if live_trading_enabled(chain):
        print(f"LIVE_TRADING=1 enabled for chain={chain}", flush=True)
    else:
        print(
            f"LIVE_TRADING=1 set but live disabled for chain={chain or '?'} "
            "(Arc-only; RH paper/notify only).",
            file=sys.stderr,
        )


def live_danger_gate(ca: str, chain: str) -> tuple[bool, list[str]]:
    """Skip GMGN security. Price/liq already came from Dex. Box live_tick also skips.

    GMGN 🚫 (honeypot/tax) needs token.security and burns the IP; Arc already skipped
    open_source. Returns (ok_to_live_buy, fail_reasons).
    """
    return True, ["dex_skip_audit"]


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


def _state_gmgn_cool_until(state: dict | None) -> float:
    try:
        return float((state or {}).get("gmgn_cooldown_until") or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_gmgn_smartmoney(chain: str, limit: int, side: str = "buy", state: dict | None = None) -> tuple[list[dict], str | None]:
    """Run gmgn-cli track smartmoney. Returns (normalized_trades, error_kind|None)."""
    if (os.environ.get("GMGN_SMARTMONEY") or "1").strip().lower() in ("0", "false", "no", "off"):
        print("gmgn smartmoney skip: GMGN_SMARTMONEY=0 (GHA owns buys)", file=sys.stderr)
        return [], "disabled"
    meta = CHAIN_META.get(chain, {})
    gmgn_chain = meta.get("gmgn_chain") or chain
    write_gmgn_dotenv()
    try:
        import gmgn_token as _gt
        # Restore account ban from cached state (GHA runners are ephemeral)
        st_until = _state_gmgn_cool_until(state)
        if st_until > time.time():
            _gt.sync_cooldown_from_state(st_until)
        left = _gt.gmgn_cooldown_remaining()
        if left > 0 or _gt.gmgn_on_cooldown():
            print(f"gmgn smartmoney skip: cooldown {left:.0f}s", file=sys.stderr)
            return [], "rate"
    except Exception:
        pass
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
            try:
                import gmgn_token as _gt
                _gt.arm_gmgn_cooldown(err)
            except Exception:
                pass
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



def load_fomo_index(path: Path | None = None) -> dict[str, dict]:
    """handle_lower -> {handle, evm, solana, pnlUsd}. Empty if file missing."""
    path = path or Path(os.environ.get("FOMO_WATCH_PATH", str(ROOT / "fomo-wallets" / "wallets_evm.jsonl")))
    if not path.exists():
        alt = ROOT / "fomo-wallets" / "leaderboard.jsonl"
        path = alt if alt.exists() else path
    out: dict[str, dict] = {}
    files = [path]
    lb = ROOT / "fomo-wallets" / "leaderboard.jsonl"
    if lb.exists() and lb.resolve() != path.resolve():
        files.append(lb)
    for fp in files:
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            handle = (o.get("handle") or "").strip()
            evm = (o.get("evm") or o.get("address") or "").lower()
            if not handle:
                continue
            rec = {
                "handle": handle,
                "evm": evm if evm.startswith("0x") and len(evm) == 42 else "",
                "solana": o.get("solana") or "",
                "pnlUsd": _num(o.get("pnlUsd") or o.get("realized_pnl_usd")) or 0.0,
            }
            prev = out.get(handle.lower()) or {}
            if prev.get("evm") and not rec["evm"]:
                rec["evm"] = prev["evm"]
            out[handle.lower()] = rec
            if rec["evm"]:
                out[rec["evm"]] = rec
    return out


def fomo_watch_addresses(index: dict[str, dict]) -> dict[str, dict]:
    """EVM addresses from FOMO leaderboard for watchlist merge."""
    addrs: dict[str, dict] = {}
    for rec in index.values():
        evm = rec.get("evm") or ""
        if evm.startswith("0x") and len(evm) == 42:
            addrs[evm] = rec
    return addrs


def fetch_fomo_buys(chain: str, index: dict[str, dict], min_usd: float, limit: int = 80) -> tuple[list[dict], str | None]:
    """REST /v2/alerts buys for one chain. Watchlist-strict on FOMO handles. Never prints the key."""
    load_box_secrets(["FOMO_API_KEY"])
    key = (os.environ.get("FOMO_API_KEY") or "").strip()
    if not key or key.lower().startswith("install"):
        return [], "nokey"
    fomo_chain = {
        "robinhood": "robinhood",
        "solana": "solana",
        "base": "base",
        "eth": "eth",
        "ethereum": "eth",
        "bsc": "bsc",
    }.get(chain)
    if not fomo_chain:
        print(f"fomo skip: chain={chain} not on FOMO")
        return [], None
    if not index:
        print("fomo skip: empty leaderboard index")
        return [], None
    url = f"https://api.fomoapi.io/v2/alerts?type=buy&chain={urllib.parse.quote(fomo_chain)}&limit={max(1, min(150, limit))}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "meme-discord-bot/2.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode() or "{}")
            cost = resp.headers.get("x-credits-cost")
            remain = resp.headers.get("x-credits-remaining")
    except urllib.error.HTTPError as e:
        code = e.code
        print(f"fomo alerts HTTP {code}", file=sys.stderr)
        if code == 401:
            return [], "auth"
        if code == 402:
            return [], "credits"
        return [], "other"
    except Exception as e:
        print(f"fomo alerts fail: {type(e).__name__}", file=sys.stderr)
        return [], "other"

    alerts = body.get("alerts") if isinstance(body, dict) else None
    if not isinstance(alerts, list):
        print("fomo alerts unexpected shape", file=sys.stderr)
        return [], "other"

    handles = {k for k in index if not str(k).startswith("0x")}
    trades: list[dict] = []
    skipped_unknown = 0
    for a in alerts:
        if not isinstance(a, dict):
            continue
        if (a.get("type") or "").lower() != "buy":
            continue
        handle = (a.get("trader") or "").strip()
        if not handle or handle.lower() not in handles:
            skipped_unknown += 1
            continue
        rec = index[handle.lower()]
        evm = rec.get("evm") or ""
        ca = (a.get("tokenAddress") or "").lower()
        if not evm.startswith("0x") or not ca.startswith("0x") or ca in SKIP_CA:
            continue
        sym = (a.get("token") or "").strip()
        if sym.upper() in SKIP_SYMBOLS:
            continue
        usd = _num(a.get("usdValue")) or 0.0
        if min_usd > 0 and usd < min_usd:
            continue
        trades.append(
            {
                "trader_address": evm,
                "trader_address_label": handle,
                "trader_handle": handle,
                "token_bought_address": ca,
                "token_bought_symbol": sym,
                "trade_value_usd": usd,
                "block_timestamp": a.get("ts"),
                "source": "fomo",
                "tx": a.get("eventId") or a.get("id"),
            }
        )
    print(
        f"fomo buys={len(trades)} alerts={len(alerts)} unknown_skip={skipped_unknown} "
        f"chain={fomo_chain} cost={cost} remain={remain}"
    )
    return trades, None



def _fomo_handles(index: dict[str, dict]) -> set[str]:
    return {k for k in index if not str(k).startswith("0x")}


def fetch_fomo_holders(ca: str, chain: str, index: dict[str, dict], limit: int = 50) -> tuple[list[dict], str | None]:
    """Who on our FOMO board holds this token. 250 credits. Never prints the key."""
    load_box_secrets(["FOMO_API_KEY"])
    key = (os.environ.get("FOMO_API_KEY") or "").strip()
    if not key or key.lower().startswith("install"):
        return [], "nokey"
    meta = CHAIN_META.get(chain) or {}
    nid = meta.get("goplus_id") or ""
    q = f"limit={max(1, min(50, limit))}"
    if nid and str(nid).isdigit():
        q += f"&networkId={nid}"
    url = f"https://api.fomoapi.io/token/{ca}/holders?{q}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "meme-discord-bot/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode() or "{}")
            cost = resp.headers.get("x-credits-cost")
            remain = resp.headers.get("x-credits-remaining")
    except urllib.error.HTTPError as e:
        print(f"fomo holders HTTP {e.code}", file=sys.stderr)
        return [], "http"
    except Exception as e:
        print(f"fomo holders fail: {type(e).__name__}", file=sys.stderr)
        return [], "other"
    raw = body.get("holders") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        print("fomo holders unexpected shape", file=sys.stderr)
        return [], "other"
    handles = _fomo_handles(index)
    out: list[dict] = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        handle = (h.get("handle") or "").strip()
        if not handle or handle.lower() not in handles:
            continue
        rec = index.get(handle.lower()) or {}
        wallet = h.get("wallet") if isinstance(h.get("wallet"), dict) else {}
        evm = (wallet.get("evm") or rec.get("evm") or "").lower()
        if evm and not (evm.startswith("0x") and len(evm) == 42):
            evm = rec.get("evm") or ""
        out.append(
            {
                "handle": handle,
                "evm": evm,
                "valueUsd": _num(h.get("valueUsd")) or 0.0,
                "pnlUsd": _num(h.get("pnlUsd")) or 0.0,
            }
        )
    print(f"fomo holders ca={ca[:10]}… board={len(out)} raw={len(raw)} cost={cost} remain={remain}")
    return out, None


def maybe_fomo_holder_signal(
    trades: list[dict],
    watch_set: set[str],
    fomo_index: dict[str, dict],
    chain: str,
    state: dict,
    window: int,
    min_wallets: int,
    min_usd: float,
) -> dict | None:
    """If 1 watch buy in window, see if other board wallets already hold. Max 1 call / interval."""
    if not env_bool("FOMO_HOLDERS", True):
        return None
    if not fomo_index:
        return None
    interval = int(os.environ.get("FOMO_HOLDERS_INTERVAL", "14400"))
    now = time.time()
    last = state.get("last_fomo_holders_ts")
    try:
        last_f = float(last) if last is not None else 0.0
        if last_f > 1e12:
            last_f = last_f / 1000.0
        if last_f > 0 and (now - last_f) < interval:
            print(f"fomo holders skip: interval {int(now - last_f)}s < {interval}s")
            return None
    except (TypeError, ValueError):
        pass
    by_ca: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        trader = (t.get("trader_address") or "").lower()
        if trader not in watch_set:
            continue
        ca = (t.get("token_bought_address") or "").lower()
        if not ca.startswith("0x") or ca in SKIP_CA:
            continue
        usd = float(t.get("trade_value_usd") or 0)
        if min_usd > 0 and usd < min_usd:
            continue
        ts = parse_ts(t.get("block_timestamp"))
        if ts and now - ts > window:
            continue
        by_ca[ca].append(t)
    candidates: list[tuple[float, str, dict[str, dict], str]] = []
    for ca, rows in by_ca.items():
        wallets: dict[str, dict] = {}
        for r in rows:
            w = (r.get("trader_address") or "").lower()
            if w not in wallets:
                wallets[w] = r
        if 1 <= len(wallets) < min_wallets:
            latest = max(parse_ts(r.get("block_timestamp")) for r in wallets.values())
            sym = rows[0].get("token_bought_symbol") or ""
            candidates.append((latest, ca, wallets, sym))
    if not candidates:
        print("fomo holders skip: no 1-wallet near-miss")
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _latest, ca, wallets, sym = candidates[0]
    holders, err = fetch_fomo_holders(ca, chain, fomo_index, limit=50)
    state["last_fomo_holders_ts"] = now
    if err or not holders:
        return None
    identities: dict[str, dict] = dict(wallets)
    for h in holders:
        evm = (h.get("evm") or "").lower()
        rec = fomo_index.get((h.get("handle") or "").lower()) or {}
        if not evm:
            evm = (rec.get("evm") or "").lower()
        if not evm.startswith("0x"):
            continue
        if evm in identities:
            continue
        identities[evm] = {
            "trader_address": evm,
            "trader_address_label": h.get("handle") or "",
            "trade_value_usd": h.get("valueUsd") or 0,
            "token_bought_symbol": sym,
            "block_timestamp": now,
            "source": "fomo_holders",
        }
    if len(identities) < min_wallets:
        print(f"fomo holders near-miss still n={len(identities)} < {min_wallets}")
        return None
    t0 = min(parse_ts(r.get("block_timestamp")) for r in identities.values())
    return {
        "key": f"{ca}:holders:{','.join(sorted(identities.keys())[:8])}:{int(t0)}",
        "ca": ca,
        "n": len(identities),
        "t0": t0,
        "elapsed": 0,
        "symbol": sym,
        "source_mode": "fomo_holders",
        "wallets": [
            {
                "address": w,
                "label": (identities[w].get("trader_address_label") or "")[:80],
                "usd": float(identities[w].get("trade_value_usd") or 0),
                "symbol": identities[w].get("token_bought_symbol"),
                "ts": identities[w].get("block_timestamp"),
                "source": identities[w].get("source") or "fomo_holders",
            }
            for w in sorted(identities.keys())
        ],
    }


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



def refine_wallets(watch_path: Path, min_realized: float | None = None) -> int:
    """Rewrite watchlist: drop losers / banned labels / weak one-hit wallets."""
    if min_realized is None:
        min_realized = float(os.environ.get("WATCH_MIN_REALIZED_USD") or "0")
    # Extra Arc quality gates (実績が薄い財布を落とす)
    min_single_pnl = float(os.environ.get("WATCH_MIN_SINGLE_TOKEN_PNL") or "100")
    min_tokens = int(os.environ.get("WATCH_MIN_TOKENS_SEEN") or "1")
    drop_onchain_only = (os.environ.get("WATCH_DROP_ONCHAIN_ONLY") or "1").strip().lower() in (
        "1", "true", "yes",
    )
    if not watch_path.exists():
        print(f"refine-wallets skip: missing {watch_path}")
        return 0
    raw: dict[str, dict] = {}
    for line in watch_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        addr = (o.get("address") or "").lower()
        if addr.startswith("0x"):
            raw[addr] = o
    kept: dict[str, dict] = {}
    dropped = 0
    reasons: dict[str, int] = {}
    for addr, o in raw.items():
        if wallet_label_banned(o):
            dropped += 1
            reasons["label_ban"] = reasons.get("label_ban", 0) + 1
            continue
        if not wallet_passes_filter(o, min_realized):
            dropped += 1
            rp = _wallet_realized(o)
            if rp <= 0:
                reasons["pnl_le_0"] = reasons.get("pnl_le_0", 0) + 1
            else:
                reasons["winrate_or_floor"] = reasons.get("winrate_or_floor", 0) + 1
            continue
        rp = _wallet_realized(o)
        try:
            n_tok = int(o.get("n_tokens_seen") or len(o.get("symbols_seen") or []) or 0)
        except (TypeError, ValueError):
            n_tok = 0
        tags = {str(x) for x in (o.get("tags") or [])}
        srcs = {str(x) for x in (o.get("source_endpoints") or o.get("sources") or [])}
        early = any(str(x).startswith("early") or str(x).startswith("early_live") for x in tags | srcs)
        # One-hit weak: only 1 token and small realized
        if n_tok <= 1 and rp < min_single_pnl and not early:
            dropped += 1
            reasons["single_weak"] = reasons.get("single_weak", 0) + 1
            continue
        if min_tokens > 1 and n_tok < min_tokens and rp < min_single_pnl * 2 and not early:
            dropped += 1
            reasons["few_tokens"] = reasons.get("few_tokens", 0) + 1
            continue
        # On-chain scraper noise without profit tag
        if drop_onchain_only:
            profitish = bool(o.get("profit_tagged")) or any(
                "profit" in str(x).lower() or "smart" in str(x).lower() or "early" in str(x).lower()
                for x in tags | srcs
            )
            onchainish = any(str(x).startswith("onchain") for x in tags | srcs)
            if onchainish and not profitish and rp < min_single_pnl:
                dropped += 1
                reasons["onchain_weak"] = reasons.get("onchain_weak", 0) + 1
                continue
        o = dict(o)
        o["pass_pnl"] = True
        o["refined_at"] = datetime.now(timezone.utc).isoformat()
        kept[addr] = o
    with watch_path.open("w", encoding="utf-8") as f:
        for a in sorted(kept.keys()):
            f.write(json.dumps(kept[a], ensure_ascii=False) + "\n")
    print(
        f"refine-wallets raw={len(raw)} keep={len(kept)} dropped={dropped} "
        f"reasons={reasons} path={watch_path}",
        flush=True,
    )
    return 0


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
                        "chains": [chain],
                        "pagination": {"page": page, "per_page": 50},
                        "order_by": [{"field": "realized_pnl_usd", "direction": "DESC"}],
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



def refresh_fomo_wallets(watch_path: Path) -> int:
    """Weekly: 7d FOMO leaderboard → keep PnL+ EVM, drop fallen FOMO-only wallets."""
    load_box_secrets(["FOMO_API_KEY"])
    key = (os.environ.get("FOMO_API_KEY") or "").strip()
    if not key:
        print("refresh-fomo skip: no FOMO_API_KEY")
        return 0
    req = urllib.request.Request(
        "https://api.fomoapi.io/v2/leaderboard/7d?limit=150",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "meme-discord-bot/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode() or "{}")
            remain = resp.headers.get("x-credits-remaining")
            cost = resp.headers.get("x-credits-cost")
    except Exception as e:
        print(f"refresh-fomo fail: {type(e).__name__}", file=sys.stderr)
        return 1
    traders = body.get("traders") or []
    print(f"refresh-fomo traders={len(traders)} cost={cost} remain={remain}")
    fomo_dir = ROOT / "fomo-wallets"
    fomo_dir.mkdir(parents=True, exist_ok=True)
    live: dict[str, dict] = {}
    for tr in traders:
        if not isinstance(tr, dict):
            continue
        handle = (tr.get("handle") or "").strip()
        wallets = tr.get("wallets") or {}
        evm = ((wallets.get("evm") if isinstance(wallets, dict) else "") or "").lower()
        pnl = _num(tr.get("pnlUsd")) or 0.0
        if not handle or pnl <= 0:
            continue
        rec = {
            "handle": handle,
            "displayName": tr.get("displayName"),
            "window": "7d",
            "pnlUsd": pnl,
            "solana": wallets.get("solana") if isinstance(wallets, dict) else None,
            "evm": evm if evm.startswith("0x") and len(evm) == 42 else "",
            "source": "fomoapi_leaderboard",
            "verified": tr.get("verified"),
        }
        live[handle.lower()] = rec
    evm_path = fomo_dir / "wallets_evm.jsonl"
    with evm_path.open("w", encoding="utf-8") as f:
        for rec in live.values():
            if rec.get("evm"):
                f.write(json.dumps({"address": rec["evm"], "handle": rec["handle"], "displayName": rec.get("displayName"), "pnlUsd": rec["pnlUsd"], "solana": rec.get("solana"), "source": "fomo_leaderboard"}, ensure_ascii=False) + "\n")
    lb_path = fomo_dir / "leaderboard.jsonl"
    with lb_path.open("w", encoding="utf-8") as f:
        for rec in live.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    existing: dict[str, dict] = {}
    if watch_path.exists():
        for line in watch_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            a = (o.get("address") or "").lower()
            if a.startswith("0x"):
                existing[a] = o
    live_evm = {rec["evm"]: rec for rec in live.values() if rec.get("evm")}
    now = datetime.now(timezone.utc).isoformat()
    added = dropped = 0
    # drop FOMO-only no longer on board / pnl+
    for addr, o in list(existing.items()):
        if not _is_fomo_only(o):
            continue
        if addr not in live_evm:
            del existing[addr]
            dropped += 1
    for addr, rec in live_evm.items():
        if addr in existing:
            o = existing[addr]
            srcs = list(o.get("source_endpoints") or [])
            if "fomo_leaderboard" not in srcs:
                srcs.append("fomo_leaderboard")
            o["source_endpoints"] = srcs
            o["fomo_handle"] = rec["handle"]
            o["realized_pnl_usd"] = max(float(o.get("realized_pnl_usd") or 0), rec["pnlUsd"])
            o["pass_pnl"] = True
            continue
        existing[addr] = {
            "address": addr,
            "address_label": rec["handle"],
            "fomo_handle": rec["handle"],
            "realized_pnl_usd": rec["pnlUsd"],
            "pass_pnl": True,
            "source_endpoints": ["fomo_leaderboard"],
            "collected_at": now,
        }
        added += 1
    watch_path.parent.mkdir(parents=True, exist_ok=True)
    with watch_path.open("w", encoding="utf-8") as f:
        for a in sorted(existing):
            f.write(json.dumps(existing[a], ensure_ascii=False) + "\n")
    print(f"refresh-fomo done watch={len(existing)} added={added} dropped_fomo={dropped}")
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
                                "source": wallets[w].get("source") or "",
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



def lp_from_goplus(info: dict) -> dict:
    """LP lock/burn vs unlocked whale. percents from GoPlus are 0–1 fractions."""
    holders = info.get("lp_holders") or []
    if not isinstance(holders, list) or not holders:
        return {"status": "fail", "reason": "lp_unknown", "locked_pct": None, "top_unlocked": None}
    locked = 0.0
    top_unlocked = 0.0
    for h in holders:
        if not isinstance(h, dict):
            continue
        pct = _num(h.get("percent")) or 0.0
        addr = (h.get("address") or "").lower()
        tag = (h.get("tag") or "").lower()
        locked_flag = str(h.get("is_locked") or "0") in ("1", "true", "True")
        is_burn = addr in BURN_LP_ADDRS or addr.endswith("dead") or "burn" in tag
        is_lock = locked_flag or any(x in tag for x in ("lock", "uncx", "pink", "team.finance"))
        if is_burn or is_lock:
            locked += pct
        elif pct > top_unlocked:
            top_unlocked = pct
    lock_min = float(os.environ.get("LP_LOCK_MIN", str(LP_LOCK_MIN)))
    dom_max = float(os.environ.get("LP_DOMINATE_MAX", str(LP_DOMINATE_MAX)))
    if locked < lock_min:
        return {"status": "fail", "reason": "lp_unlocked", "locked_pct": locked, "top_unlocked": top_unlocked}
    if top_unlocked >= dom_max:
        return {"status": "fail", "reason": "lp_dominate", "locked_pct": locked, "top_unlocked": top_unlocked}
    return {"status": "pass", "reason": None, "locked_pct": locked, "top_unlocked": top_unlocked}


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

    reasons = []
    if is_hp:
        reasons.append("honeypot")
    if cannot_sell:
        reasons.append("cannot_sell")
    if high_tax:
        reasons.append(f"high_tax(b={buy_tax:.0%}/s={sell_tax:.0%})")
    lp = lp_from_goplus(info)
    if lp.get("status") == "fail" and lp.get("reason"):
        reasons.append(str(lp["reason"]))
    if reasons:
        return {
            "status": "fail",
            "reason": ",".join(reasons),
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "lp": lp,
        }
    return {"status": "pass", "reason": None, "buy_tax": buy_tax, "sell_tax": sell_tax, "lp": lp}


def heat_gate_reasons(safety: dict) -> list[str]:
    """Skip thin / low-heat markets (2x+ winners were thicker)."""
    fails: list[str] = []
    liq = safety.get("liq_usd")
    mcap = safety.get("mcap_usd") or safety.get("fdv")
    ratio = safety.get("ratio")
    try:
        min_liq = float(os.environ.get("MIN_LIQ_USD", str(MIN_LIQ_USD)))
    except (TypeError, ValueError):
        min_liq = MIN_LIQ_USD
    try:
        min_mcap = float(os.environ.get("MIN_MCAP_USD", str(MIN_MCAP_USD)))
    except (TypeError, ValueError):
        min_mcap = MIN_MCAP_USD
    try:
        liq_mcap_min = float(os.environ.get("LIQ_MCAP_MIN", str(LIQ_MCAP_MIN)))
    except (TypeError, ValueError):
        liq_mcap_min = LIQ_MCAP_MIN
    if liq is None:
        fails.append("liq_na")
    else:
        try:
            if float(liq) < min_liq:
                fails.append(f"liq_thin={float(liq):.0f}<{min_liq:.0f}")
        except (TypeError, ValueError):
            fails.append("liq_na")
    if mcap is None:
        fails.append("no_mcap")
    else:
        try:
            if float(mcap) < min_mcap:
                fails.append(f"mcap_thin={float(mcap):.0f}<{min_mcap:.0f}")
        except (TypeError, ValueError):
            fails.append("no_mcap")
    if ratio is None:
        if liq is not None and mcap:
            try:
                ratio = float(liq) / float(mcap)
            except (TypeError, ValueError, ZeroDivisionError):
                ratio = None
    if ratio is None:
        fails.append("liq_ratio=na")
    elif float(ratio) < liq_mcap_min:
        fails.append(f"liq_ratio={float(ratio):.2f}")
    return fails


def safety_check(ca: str, chain: str) -> dict:
    """GMGN info + security. DexScreener/GoPlus are not the source of truth."""
    meta = CHAIN_META.get(chain, {})
    gmgn_chain = meta.get("gmgn_chain") or chain
    # Arc: ignore security-audit gates while collecting launch data (env overrideable)
    arc_skip = (chain or "").lower() == "arc" and env_bool("ARC_SKIP_SECURITY_AUDIT", True)
    if arc_skip:
        snap = gmgn_tok.market_snapshot(gmgn_chain, ca)
        price = snap.get("price_usd")
        mcap = snap.get("mcap_usd") or snap.get("fdv")
        liq = snap.get("liq_usd")
        ratio = None
        if liq is not None and mcap and mcap > 0:
            try:
                ratio = float(liq) / float(mcap)
            except (TypeError, ValueError):
                ratio = None
        fetch_failed = bool(snap.get("fetch_failed")) or not snap.get("ok")
        # Still require a usable market snapshot when available; never block on audit fields
        ok = not fetch_failed
        jp = "通過（Arc・監査スキップ）" if ok else "見送り（GMGN取得失敗）"
        print(
            f"safety ca={ca[:10]}… ok={ok} src=gmgn-arc-skip ratio={ratio} "
            f"mcap={mcap} liq={liq} fetch_failed={fetch_failed} reasons={['ok'] if ok else ['gmgn_info:fail']}",
            flush=True,
        )
        reasons = ["ok"] if ok else ["gmgn_info:fail"]
        if ok:
            heat = heat_gate_reasons(
                {"liq_usd": liq, "mcap_usd": mcap, "fdv": snap.get("fdv") or mcap, "ratio": ratio}
            )
            if heat:
                ok = False
                reasons = heat
                jp = "見送り（薄い盛り上がり）"
        return {
            "ok": ok,
            "reasons": reasons,
            "ratio": ratio,
            "liq_usd": liq,
            "mcap_usd": mcap,
            "fdv": snap.get("fdv") or mcap,
            "price_usd": price,
            "dex_url": snap.get("url"),
            "gmgn_url": snap.get("url"),
            "goplus": "unused",
            "jp": jp,
            "audit_jp": "（Arc・セキュリティ監査スキップ）",
            "symbol_hint": snap.get("symbol"),
            "volume_h24": snap.get("volume_h24"),
            "volume_h1": snap.get("volume_h1"),
            "volume_m5": snap.get("volume_m5"),
            "buys_h24": snap.get("buys_h24"),
            "buys_m5": snap.get("buys_m5"),
            "sells_m5": snap.get("sells_m5"),
            "buys_h1": snap.get("buys_h1"),
            "sells_h1": snap.get("sells_h1"),
            "price_change_m5": snap.get("price_change_m5"),
            "price_change_h1": snap.get("price_change_h1"),
            "price_change_h6": snap.get("price_change_h6"),
            "price_change_h24": snap.get("price_change_h24"),
            "graduated": snap.get("graduated"),
            "bondingish": snap.get("bondingish"),
            "dex_id": snap.get("dex_id"),
            "labels": snap.get("labels"),
            "pair_created_at_ms": snap.get("pair_created_at_ms"),
            "holder_count": snap.get("holder_count"),
            "source": "gmgn",
            "fetch_failed": fetch_failed,
            "checklist": [],
        }
    out = gmgn_tok.evaluate(
        gmgn_chain,
        ca,
        liq_mcap_min=LIQ_MCAP_MIN,
        min_age_sec=float(MIN_TOKEN_AGE_SEC),
        lp_lock_min=float(os.environ.get("LP_LOCK_MIN", str(LP_LOCK_MIN))),
    )
    # Overlay DexScreener m5 volume / tape / graduation for notify gates (RH+)
    try:
        dex = gmgn_tok.market_snapshot(gmgn_chain, ca)
    except Exception:
        dex = {}
    if isinstance(dex, dict) and dex.get("ok"):
        out = dict(out)
        for k in (
            "volume_h24", "volume_h1", "volume_m5", "buys_h24", "buys_m5", "sells_m5",
            "buys_h1", "sells_h1", "price_change_m5", "price_change_h1",
            "price_change_h6", "price_change_h24", "graduated", "bondingish",
            "dex_id", "labels",
        ):
            if out.get(k) is None and dex.get(k) is not None:
                out[k] = dex.get(k)
        if out.get("holder_count") is None and dex.get("holder_count") is not None:
            out["holder_count"] = dex.get("holder_count")
    if out.get("ok") and not out.get("fetch_failed"):
        heat = heat_gate_reasons(out)
        # evaluate already checks liq_ratio; still enforce absolute liq/mcap floors
        heat = [h for h in heat if not str(h).startswith("liq_ratio")]
        if heat:
            out = dict(out)
            out["ok"] = False
            prev = [r for r in (out.get("reasons") or []) if r != "ok"]
            out["reasons"] = prev + heat
            out["jp"] = "見送り（薄い盛り上がり）"
    return out


def strength_label(n: int, total_usd: float) -> tuple[str, int]:
    if n >= 3 and total_usd >= 500:
        return "かなり強い", 0xE74C3C
    if n >= 3:
        return "やや強い", 0xF39C12
    return "買いが重なった", 0x3498DB


def _wallet_tag(w: dict, watch: dict[str, dict] | None) -> str:
    """fomo | sm | both — for mixed Discord layout."""
    src = (w.get("source") or "").lower()
    addr = (w.get("address") or "").lower()
    meta = (watch or {}).get(addr) or {}
    srcs = [str(x) for x in (meta.get("source_endpoints") or [])]
    has_fomo = src in ("fomo", "fomo_holders") or "fomo_leaderboard" in srcs or bool(meta.get("fomo_handle"))
    has_sm = src in ("gmgn", "onchain", "nansen") or any(
        x in srcs for x in ("pnl-leaderboard", "gmgn_cli", "gmgn_browser", "dex-trades")
    )
    if has_fomo and has_sm:
        return "both"
    if has_fomo:
        return "fomo"
    return "sm"


def build_embed(
    s: dict,
    chain: str,
    safety: dict,
    source_mode: str,
    watch: dict[str, dict] | None = None,
) -> dict:
    meta = CHAIN_META.get(chain, {})
    chain_jp = meta.get("jp") or chain
    sym = s.get("symbol") or safety.get("symbol_hint") or "不明"
    wallets = list(s.get("wallets") or [])
    n = int(s.get("n") or len(wallets))
    total_usd = sum(float(w.get("usd") or 0) for w in wallets)
    tagged = [(w, _wallet_tag(w, watch)) for w in wallets]
    n_fomo = sum(1 for _, k in tagged if k == "fomo")
    n_sm = sum(1 for _, k in tagged if k == "sm")
    n_both = sum(1 for _, k in tagged if k == "both")
    mixed = (n_fomo + n_both) > 0 and (n_sm + n_both) > 0

    strength, color = strength_label(n, total_usd)
    if mixed:
        color = 0x9B59B6
        mix = f"混合 FOMO{n_fomo + n_both} / スマート{n_sm + n_both}"
    elif n_fomo and not n_sm:
        color = 0xE67E22
        mix = f"FOMO {n_fomo}人"
    else:
        mix = f"スマートウォレット {n_sm + n_both}人"

    title = f"${sym} · {mix}"
    if n >= 3:
        title = f"{strength} · {title}"

    elapsed = int(s.get("elapsed") or 0)
    if elapsed <= 0:
        when = "いま"
    elif elapsed < 60:
        when = f"約{elapsed}秒"
    else:
        when = f"約{elapsed // 60}分"

    ratio = safety.get("ratio")
    ratio_txt = f"{ratio:.0%}" if isinstance(ratio, (int, float)) else "—"
    description = (
        f"{chain_jp} · {when}\n"
        f"FOMO **{n_fomo}** · スマートウォレット **{n_sm}**"
        + (f" · 両方 **{n_both}**" if n_both else "")
        + f"\n安全: {safety.get('jp') or '未実施'}"
        + (f"\n監査: {safety.get('audit_jp')}" if safety.get("audit_jp") else "")
    )

    fields = [
        {"name": "内訳", "value": f"FOMO {n_fomo}人\nスマート {n_sm}人" + (f"\n両方 {n_both}人" if n_both else ""), "inline": True},
        {"name": "時価総額", "value": fmt_usd(safety.get("mcap_usd") or safety.get("fdv")), "inline": True},
        {"name": "流動性", "value": fmt_usd(safety.get("liq_usd")), "inline": True},
        {"name": "価格", "value": fmt_usd(safety.get("price_usd")), "inline": True},
        {"name": "liq/mcap", "value": ratio_txt, "inline": True},
    ]

    gmgn_chain = meta.get("gmgn_chain") or chain
    gmgn_url = gmgn_tok.token_app_url(gmgn_chain, s["ca"], safety.get("gmgn_url"))
    fields.append({"name": "コントラクト", "value": f"[`{s['ca']}`]({gmgn_url})", "inline": False})
    link_lines = [f"[GMGNアプリで開く]({gmgn_url})"]
    explorer_base = meta.get("explorer")
    if explorer_base:
        link_lines.append(f"[エクスプローラー]({explorer_base}{s['ca']})")
    fields.append({"name": "リンク", "value": " · ".join(link_lines), "inline": False})

    def line(w: dict) -> str:
        addr = w.get("address") or ""
        short = (addr[:6] + "…" + addr[-4:]) if len(addr) >= 10 else addr
        lab = (w.get("label") or "").strip()
        name = lab if lab and not lab.startswith("0x") else short
        return f"• {name} · 約 ${float(w.get('usd') or 0):,.0f}"

    fomo_lines = [line(w) for w, k in tagged if k == "fomo"]
    sm_lines = [line(w) for w, k in tagged if k == "sm"]
    both_lines = [line(w) for w, k in tagged if k == "both"]
    if fomo_lines:
        fields.append({"name": "FOMO", "value": "\n".join(fomo_lines)[:1000], "inline": False})
    if sm_lines:
        fields.append({"name": "スマートウォレット", "value": "\n".join(sm_lines)[:1000], "inline": False})
    if both_lines:
        fields.append({"name": "両方（FOMOかつ監視）", "value": "\n".join(both_lines)[:1000], "inline": False})
    if not (fomo_lines or sm_lines or both_lines):
        fields.append({"name": "誰が買ったか", "value": "—", "inline": False})

    if safety.get("volume_h24") is not None:
        fields.append({"name": "出来高24h", "value": fmt_usd(safety.get("volume_h24")), "inline": True})
    if safety.get("holder_count") is not None:
        fields.append({"name": "ホルダー", "value": str(int(float(safety["holder_count"]))), "inline": True})
    qscores = [
        wallet_quality_score((watch or {}).get((w.get("address") or "").lower()) or {})
        for w in (s.get("wallets") or [])
    ]
    if qscores:
        fields.append({
            "name": "財布質",
            "value": f"best {max(qscores):.1f} / avg {sum(qscores)/len(qscores):.1f}",
            "inline": True,
        })
    pb = safety.get("_playbook")
    if pb == "set1":
        fields.append({"name": "セット", "value": "①初動調整→反転", "inline": True})
    elif pb == "set2":
        fields.append({"name": "セット", "value": "②サバイバル整理", "inline": True})

    return {
        "title": title[:256],
        "url": gmgn_url,
        "description": description[:4000],
        "color": color,
        "fields": fields,
        "footer": {"text": "数値はGMGN取得時点 · お知らせのみ・自動では買いません"},
    }


def build_multiplier_embed(alert: dict, mult: float, dex: dict, milestone: float | None) -> dict:
    sym = alert.get("symbol") or dex.get("symbol") or "不明"
    ca = alert.get("ca")
    mcap = dex.get("mcap_usd") or dex.get("fdv")
    liq = dex.get("liq_usd")
    title = f"さっきの通知から {mult:.1f}倍 · ${sym}"
    if milestone:
        title = f"さっきの通知から {milestone:g}倍到達 · ${sym}"
    gmgn_url = gmgn_tok.token_app_url("robinhood", ca or "", dex.get("url"))
    return {
        "title": title[:256],
        "url": gmgn_url,
        "description": (
            f"通知時の価格から約 **{mult:.2f}倍** です。\n"
            f"現在 時価総額 {fmt_usd(mcap)} / 流動性 {fmt_usd(liq)}（GMGN）"
        )[:4000],
        "color": 0x9B59B6,
        "fields": [
            {"name": "コントラクト", "value": f"[`{ca}`]({gmgn_url})", "inline": False},
            {"name": "時価総額", "value": fmt_usd(mcap), "inline": True},
            {"name": "流動性", "value": fmt_usd(liq), "inline": True},
            {"name": "リンク", "value": f"[GMGNアプリで開く]({gmgn_url})", "inline": False},
        ],
        "footer": {"text": "数値はGMGN · 倍率フォローアップ・自動売買なし"},
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
        elif rs.startswith("launch_age"):
            bits.append("ローンチ直後")
        elif rs.startswith("liq_ratio") or rs.startswith("liq_thin") or rs == "liq_na":
            bits.append("薄い板")
        elif rs.startswith("mcap_thin"):
            bits.append("時価薄い")
        elif rs.startswith("weak_cluster"):
            bits.append("買い合計が小さい")
        elif rs.startswith("volume"):
            bits.append("出来高薄い")
        elif rs.startswith("holders"):
            bits.append("ホルダー少ない")
        elif rs.startswith("quality"):
            bits.append("財布の質不足")
        elif "graduated" in rs or rs.startswith("not_graduated"):
            bits.append("未卒業")
        elif rs.startswith("volume_m5"):
            bits.append("5分出来高薄い")
        elif rs.startswith("buy_vol") or rs.startswith("buys_m5"):
            bits.append("買いボリューム不足")
        elif rs.startswith("spike_") or rs.startswith("dump_m5") or rs.startswith("onesided"):
            bits.append("急騰/不自然")
        elif rs.startswith("bots_left"):
            bits.append("bot疑惑除外後不足")
        elif "honeypot" in rs:
            bits.append("honeypot")
        elif "cannot_sell" in rs:
            bits.append("売却制限")
        elif "high_tax" in rs:
            bits.append("手数料高")
        elif "lp_unlocked" in rs:
            bits.append("未ロック")
        elif "lp_dominate" in rs:
            bits.append("LP偏り")
        elif "lp_unknown" in rs:
            bits.append("LP不明")
        else:
            bits.append("検査NG")
    reason_jp = "・".join(bits) if bits else (safety.get("jp") or "見送り")
    meta = CHAIN_META.get(chain, {})
    gmgn_url = gmgn_tok.token_app_url(meta.get("gmgn_chain") or chain, str(s.get("ca") or ""), safety.get("gmgn_url"))
    return {
        "title": f"見送り · ${sym}"[:256],
        "url": gmgn_url,
        "description": (
            f"{reason_jp}\n"
            + (f"監査: {safety.get('audit_jp')}\n" if safety.get("audit_jp") else "")
            + f"監視交差 {s.get('n', '?')}人 · 自動では買いません"
        )[:4000],
        "color": 0x95A5A6,
        "fields": [
            {"name": "コントラクト", "value": f"[`{s.get('ca')}`]({gmgn_url})", "inline": False},
            {"name": "理由", "value": (safety.get("jp") or reason_jp)[:500], "inline": False},
            {"name": "時価総額", "value": fmt_usd(safety.get("mcap_usd") or safety.get("fdv")), "inline": True},
            {"name": "流動性", "value": fmt_usd(safety.get("liq_usd")), "inline": True},
            {"name": "リンク", "value": f"[GMGNアプリで開く]({gmgn_url})", "inline": False},
        ],
        "footer": {"text": f"数値はGMGN · スキップ通知 · {meta.get('jp') or chain}"},
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
        dex = gmgn_tok.market_snapshot(CHAIN_META.get(chain, {}).get("gmgn_chain") or chain, ca)
        price_now = _num(dex.get("price_usd"))
        if not price_now or price_now <= 0:
            continue
        mult = price_now / alert_price
        alert["last_followup_at"] = now
        alert["last_followup_mult"] = mult
        alert["last_price_usd"] = price_now
        alert["last_mcap"] = dex.get("mcap_usd") or dex.get("fdv")
        alert["last_liq"] = dex.get("liq_usd")
        hit = list(alert.get("milestones_hit") or [])
        next_ms = None
        for ms in MULTIPLIER_MILESTONES:
            if mult >= ms and ms not in hit:
                next_ms = ms
                break
        # Milestone crossings only (each once): 1.5x / 2x / 3x / 5x
        # Still refresh last_* above so price is always tracked in state
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


def collect_trades(
    args: argparse.Namespace,
    chain: str,
    min_usd: float,
    watch_set: set[str],
    state: dict | None = None,
    fomo_index: dict[str, dict] | None = None,
) -> tuple[list[dict], str, str | None]:
    """Returns (trades, source_name, gmgn_error_kind). FOMO buys merge when due."""
    nansen_for_trades = env_bool("NANSEN_FOR_TRADES", False)
    trades: list[dict] = []
    gmgn_err: str | None = None
    source_name = "none"
    state = state if isinstance(state, dict) else {}

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
        source_name = "nansen"
    else:
        limit = int(os.environ.get("GMGN_LIMIT", str(args.per_page or 100)))
        trades, gmgn_err = fetch_gmgn_smartmoney(chain, limit=limit, side="buy", state=state)
        try:
            import gmgn_token as _gt
            left = _gt.gmgn_cooldown_remaining()
            if left > 0:
                state["gmgn_cooldown_until"] = time.time() + left
                state["gmgn_cooldown_err"] = gmgn_err
        except Exception:
            pass
        if trades:
            source_name = "gmgn"
            state.pop("gmgn_cooldown_until", None)
        else:
            print(f"GMGN unavailable (err={gmgn_err}); trying on-chain fallback", file=sys.stderr)
            oc = fetch_onchain_fallback(watch_set, chain, min_usd=0)
            if oc:
                trades = oc
                source_name = "onchain"

    # FOMO: 125 credits/call. Default ≥25 min so free 250k/mo lasts (~216k + leaderboard).
    fomo_on = env_bool("FOMO_ENABLED", True)
    interval = int(os.environ.get("FOMO_POLL_SECONDS", "1500"))
    now = time.time()
    last = state.get("last_fomo_poll_ts")
    due = True
    try:
        last_f = float(last) if last is not None else 0.0
        if last_f > 1e12:
            last_f = last_f / 1000.0
        if last_f > 0 and (now - last_f) < interval:
            due = False
            print(f"fomo skip: interval {int(now - last_f)}s < {interval}s")
    except (TypeError, ValueError):
        due = True
    if fomo_on and due and fomo_index:
        fomo_trades, fomo_err = fetch_fomo_buys(
            chain,
            fomo_index,
            min_usd=min_usd,
            limit=int(os.environ.get("FOMO_ALERT_LIMIT", "80")),
        )
        state["last_fomo_poll_ts"] = now
        state["last_fomo_err"] = fomo_err
        if fomo_trades:
            trades.extend(fomo_trades)
            if source_name in ("none", ""):
                source_name = "fomo"
            elif "fomo" not in source_name:
                source_name = f"{source_name}+fomo"
        elif fomo_err:
            print(f"fomo err={fomo_err}", file=sys.stderr)
    return trades, source_name, gmgn_err


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
    live_state_path = live_mod.state_path(ROOT)
    live_book_path = live_mod.book_path(ROOT)
    live_on = live_trading_enabled(chain)
    live_webhook = resolve_live_webhook(chain) if live_on else None

    watch, raw_count, fallback = load_watchlist(watch_path, min_realized)
    fomo_index = load_fomo_index()
    fomo_addrs = fomo_watch_addresses(fomo_index)
    fomo_merged = 0
    for addr, rec in fomo_addrs.items():
        if addr not in watch:
            watch[addr] = {
                "address": addr,
                "address_label": rec.get("handle") or "",
                "fomo_handle": rec.get("handle") or "",
                "realized_pnl_usd": rec.get("pnlUsd") or 0,
                "pass_pnl": True,
                "source_endpoints": ["fomo_leaderboard"],
            }
            fomo_merged += 1
    watch_set = set(watch.keys())
    print(
        f"watchlist raw={raw_count} filtered={len(watch_set)} "
        f"fomo_handles={sum(1 for k in fomo_index if not str(k).startswith('0x'))} "
        f"fomo_merged_runtime={fomo_merged} "
        f"min_realized={min_realized} fallback={fallback} "
        f"NANSEN_FOR_TRADES={int(env_bool('NANSEN_FOR_TRADES', False))} "
        f"ALLOW_GMGN_CLUSTER={int(allow_cluster)} chain={chain} "
        f"PAPER_BANKROLL_USD={paper_mod.bankroll_usd()}"
    )

    state = load_state(state_path)
    try:
        import gmgn_token as _gt
        _gt.sync_cooldown_from_state(_state_gmgn_cool_until(state))
        if _gt.gmgn_on_cooldown():
            print(f"gmgn state cooldown {_gt.gmgn_cooldown_remaining():.0f}s", flush=True)
    except Exception:
        pass
    paper_mod.ensure_paper_state(state)

    if chain == "robinhood" and env_bool("XBTSCOUT_ENABLED", True):
        interval = int(os.environ.get("XBTSCOUT_SCRAPE_SECONDS", "1800"))
        now_x = time.time()
        last_x = state.get("last_xbtscout_scrape_ts")
        due = True
        try:
            last_xf = float(last_x) if last_x is not None else 0.0
            if last_xf > 1e12:
                last_xf = last_xf / 1000.0
            if last_xf > 0 and (now_x - last_xf) < interval:
                due = False
                print(f"xbtscout scrape skip: {int(now_x - last_xf)}s < {interval}s")
        except (TypeError, ValueError):
            due = True
        if due:
            try:
                new_recs = [r for r in scrape_xbtscout_posts() if r.get("is_new")]
                new_cas = [r["ca"] for r in new_recs]
            except Exception as e:
                print(f"xbtscout scrape err {type(e).__name__}", file=sys.stderr)
                new_recs = []
                new_cas = []
            state["last_xbtscout_scrape_ts"] = now_x
            if new_recs:
                xhook = resolve_xbtscout_webhook(chain)
                n_post = notify_xbtscout_new_cas(new_recs, xhook, chain, state=state)
                print(f"xbtscout discord notified={n_post}/{len(new_recs)}", flush=True)
            if new_cas:
                # Wallet harvest needs GMGN — leave to GHA when box IP is banned / disabled
                if (os.environ.get("GMGN_DISABLED") or "0").strip().lower() in ("1", "true", "yes"):
                    print("xbtscout harvest skip: GMGN_DISABLED (GHA owns harvest)", flush=True)
                else:
                    harvest_xbtscout_wallets(watch_path, max_tokens=1, only_cas=new_cas)
                watch, raw_count, fallback = load_watchlist(watch_path, min_realized)
                fomo_addrs = fomo_watch_addresses(fomo_index)
                for addr, rec in fomo_addrs.items():
                    if addr not in watch:
                        watch[addr] = {
                            "address": addr,
                            "address_label": rec.get("handle") or "",
                            "fomo_handle": rec.get("handle") or "",
                            "realized_pnl_usd": rec.get("pnlUsd") or 0,
                            "pass_pnl": True,
                            "source_endpoints": ["fomo_leaderboard"],
                        }
                watch_set = set(watch.keys())
                print(f"xbtscout new_cas={len(new_cas)} watch={len(watch_set)}")
            save_state(state_path, state)

    # Multiplier milestones + paper marks (works even if GMGN auth fails)
    fu = process_multiplier_followups(state, webhook, chain, paper_path)
    paper_stats = {"marked": 0, "half": 0, "stop": 0, "open": 0}
    if env_bool("PAPER_TRADING", True):
        paper_stats = paper_mod.process_paper_positions(
            state,
            book_path,
            chain,
            lambda ca, ch: gmgn_tok.market_snapshot(CHAIN_META.get(ch, {}).get("gmgn_chain") or ch, ca),
            webhook=paper_webhook,
            discord_post=discord_webhook,
        )
    else:
        print("PAPER_TRADING=0 — GHA skips paper marks/exits (box paper_tick owns book)", flush=True)
    # Always persist so marks/open positions survive the next Actions cache restore
    save_state(state_path, state)

    live_stats = {"marked": 0, "half": 0, "stop": 0, "open": 0, "fail": 0}
    live_state = {}
    if live_on:
        live_state = live_mod.load_live_state(live_state_path)
        live_mod.ensure_live_state(live_state)
        try:
            live_stats = live_mod.process_live_positions(
                live_state,
                live_book_path,
                chain,
                lambda ca, ch: gmgn_tok.market_snapshot(CHAIN_META.get(ch, {}).get("gmgn_chain") or ch, ca),
                webhook=live_webhook,
                discord_post=discord_webhook,
            )
        except Exception as e:
            print(f"live process soft-fail: {type(e).__name__}", file=sys.stderr)
        live_mod.save_live_state(live_state_path, live_state)
    elif env_bool("LIVE_TRADING", False):
        print("live process skipped (chain not allowed)", flush=True)

    trades, source_name, gmgn_err = collect_trades(args, chain, min_usd, watch_set, state=state, fomo_index=fomo_index)

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
            f"done (followups={fu} paper={paper_stats} live={live_stats})"
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
    wl_hits = [
        t for t in trades
        if (t.get("trader_address") or "").lower() in watch_set
    ]
    print(
        f"watchlist hits in feed={len(wl_hits)}/{len(trades)} "
        f"uniq_makers={len({(t.get('trader_address') or '').lower() for t in wl_hits})}",
        flush=True,
    )
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

    if not signals:
        hs = maybe_fomo_holder_signal(
            trades, watch_set, fomo_index, chain, state, window, min_wallets, min_usd
        )
        if hs:
            signals = [hs]
            source_mode = "fomo_holders"
            print(f"fomo holders signal n={hs['n']} ca={hs['ca'][:10]}…")

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

        # Drop bot-suspicious wallets from overlap (recompute n / total)
        if env_bool("DROP_BOT_WALLETS", DROP_BOT_WALLETS) or env_bool("DROP_WEAK_WALLETS", True):
            kept_w = []
            dropped_bots = []
            try:
                weak_max = float(os.environ.get("WEAK_WALLET_MAX_SCORE", str(WEAK_WALLET_MAX_SCORE)))
            except (TypeError, ValueError):
                weak_max = WEAK_WALLET_MAX_SCORE
            try:
                min_rp = float(os.environ.get("WATCH_MIN_REALIZED_HARD", str(WATCH_MIN_REALIZED_HARD)))
            except (TypeError, ValueError):
                min_rp = WATCH_MIN_REALIZED_HARD
            for w in list(s.get("wallets") or []):
                addr = (w.get("address") or "").lower()
                meta = watch.get(addr) or {}
                if env_bool("DROP_BOT_WALLETS", DROP_BOT_WALLETS) and wallet_looks_bot(meta):
                    dropped_bots.append(addr[:10] + ":bot")
                    continue
                if env_bool("DROP_WEAK_WALLETS", True):
                    q = wallet_quality_score(meta)
                    rp = _wallet_realized(meta)
                    if (not wallet_is_consistent(meta)) or rp < min_rp or q <= weak_max:
                        dropped_bots.append(addr[:10] + ":weak")
                        continue
                kept_w.append(w)
            if dropped_bots:
                print(
                    f"drop_bot_wallets ca={ca[:10]}… dropped={dropped_bots} keep={len(kept_w)}",
                    flush=True,
                )
            s["wallets"] = kept_w
            s["n"] = len(kept_w)
            total_usd = sum(float(w.get("usd") or 0) for w in kept_w)
            base_row["n"] = s["n"]
            base_row["total_usd"] = total_usd
            base_row["bots_dropped"] = len(dropped_bots)
            min_wallets = int(os.environ.get("MIN_WALLETS", "2"))
            if s["n"] < min_wallets:
                append_paper_log(
                    paper_path,
                    {
                        **base_row,
                        "posted": False,
                        "reason": f"bots_left_n={s['n']}<{min_wallets}",
                    },
                )
                skipped += 1
                continue

        # Prefer 仕込み smart wallets in the overlap (Arc default on)
        require_early = env_bool(
            "REQUIRE_EARLY_HIT",
            False,
        )
        if require_early:
            early_addrs = [
                (w.get("address") or "").lower()
                for w in (s.get("wallets") or [])
                if wallet_is_early_stage(watch.get((w.get("address") or "").lower()) or {})
            ]
            print(
                f"early_hit ca={ca[:10]}… early={len(early_addrs)}/{s['n']} "
                f"cluster_usd={total_usd:.0f}",
                flush=True,
            )
            if not early_addrs:
                append_paper_log(
                    paper_path,
                    {**base_row, "posted": False, "reason": "no_early_wallet"},
                )
                skipped += 1
                continue

        wallet_scores = [
            wallet_quality_score(watch.get((w.get("address") or "").lower()) or {})
            for w in (s.get("wallets") or [])
        ]
        # four-axis notify gates need market fields — run after we have safety below
        # (cluster/quality checked here; volume/holders after safety_check)

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
                    "fetch_failed": bool(safety.get("fetch_failed")),
                    "mcap": safety.get("mcap_usd"),
                    "liq": safety.get("liq_usd"),
                },
            )
            # Don't Discord-notify empty "GMGN取得失敗" — looks out of sync with the app
            if safety.get("fetch_failed"):
                print(f"skip notice suppressed (gmgn fetch failed) {ca[:10]}…")
            elif skip_notices < MAX_SKIP_NOTICES_PER_RUN:
                try:
                    discord_webhook(webhook, embeds=[build_skip_embed(s, safety, chain)])
                    skip_notices += 1
                except Exception as e:
                    print(f"skip notice failed: {type(e).__name__}", file=sys.stderr)
            seen.add(s["key"])
            skipped += 1
            continue

        # 出来高 / ホルダー / 買い金額 / 財布質
        wallet_scores = [
            wallet_quality_score(watch.get((w.get("address") or "").lower()) or {})
            for w in (s.get("wallets") or [])
        ]
        market_fails = notify_market_gate_reasons(safety, total_usd, wallet_scores)
        print(
            f"notify_gates ca={ca[:10]}… fails={market_fails or ['ok']} "
            f"vol={safety.get('volume_h24')} holders={safety.get('holder_count')} "
            f"cluster={total_usd:.0f} q={wallet_scores}",
            flush=True,
        )
        if market_fails:
            reason = "notify_gate:" + ",".join(market_fails)
            print(f"skip {ca} {reason}", flush=True)
            append_paper_log(
                paper_path,
                {
                    **base_row,
                    "posted": False,
                    "reason": reason,
                    "volume_h24": safety.get("volume_h24"),
                    "holders": safety.get("holder_count"),
                    "wallet_scores": wallet_scores,
                    "mcap": safety.get("mcap_usd"),
                    "liq": safety.get("liq_usd"),
                },
            )
            if skip_notices < MAX_SKIP_NOTICES_PER_RUN:
                try:
                    safety_skip = dict(safety)
                    safety_skip["reasons"] = market_fails
                    safety_skip["jp"] = "見送り（" + "・".join(
                        (
                            "出来高薄い" if str(r).startswith("volume") else
                            "ホルダー少ない" if str(r).startswith("holders") else
                            "買い合計小さい" if str(r).startswith("weak_cluster") else
                            "財布の質不足" if str(r).startswith("quality") else
                            "検査NG"
                        )
                        for r in market_fails
                    ) + "）"
                    discord_webhook(webhook, embeds=[build_skip_embed(s, safety_skip, chain)])
                    skip_notices += 1
                except Exception as e:
                    print(f"skip notice failed: {type(e).__name__}", file=sys.stderr)
            seen.add(s["key"])
            skipped += 1
            continue

        embed = build_embed(s, chain, safety, source_mode, watch=watch)
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
                "alert_price_usd": safety.get("price_usd"),
                "symbol": s.get("symbol") or safety.get("symbol_hint"),
            },
        )
        # Virtual paper entry — optional on GHA; box paper_tick may own the book
        if env_bool("PAPER_TRADING", True) and safety.get("price_usd"):
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
        # Arc LIVE entry (soft-fail); RH never reaches live_on
        if live_on and safety.get("price_usd"):
            try:
                danger_ok, danger_reasons = live_danger_gate(ca, chain)
                if not live_state:
                    live_state = live_mod.load_live_state(live_state_path)
                    live_mod.ensure_live_state(live_state)
                live_mod.open_live_position(
                    live_state,
                    live_book_path,
                    ca=ca,
                    symbol=s.get("symbol") or safety.get("symbol_hint"),
                    entry_price=float(safety["price_usd"]),
                    n=int(s["n"]),
                    chain=chain,
                    mcap=safety.get("mcap_usd") or safety.get("fdv"),
                    liq=safety.get("liq_usd"),
                    webhook=live_webhook,
                    discord_post=discord_webhook,
                    danger_ok=danger_ok,
                    danger_reasons=danger_reasons,
                )
                live_mod.save_live_state(live_state_path, live_state)
            except Exception as e:
                print(f"live open soft-fail: {type(e).__name__}", file=sys.stderr)
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

    # Remount after any same-run opens so price tracking starts immediately
    if env_bool("PAPER_TRADING", True):
        paper_stats = paper_mod.process_paper_positions(
            state,
            book_path,
            chain,
            lambda ca, ch: gmgn_tok.market_snapshot(CHAIN_META.get(ch, {}).get("gmgn_chain") or ch, ca),
            webhook=paper_webhook,
            discord_post=discord_webhook,
        )
    if live_on:
        try:
            if not live_state:
                live_state = live_mod.load_live_state(live_state_path)
                live_mod.ensure_live_state(live_state)
            live_stats = live_mod.process_live_positions(
                live_state,
                live_book_path,
                chain,
                lambda ca, ch: gmgn_tok.market_snapshot(CHAIN_META.get(ch, {}).get("gmgn_chain") or ch, ca),
                webhook=live_webhook,
                discord_post=discord_webhook,
            )
            live_mod.save_live_state(live_state_path, live_state)
        except Exception as e:
            print(f"live remount soft-fail: {type(e).__name__}", file=sys.stderr)
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
        f"followups={fu} paper={paper_stats} live={live_stats} watch={len(watch_set)}"
    )
    return 0



def test_fomo_holders_post() -> int:
    """One Discord 仮投稿 from live FOMO holders. No paper, no live trade."""
    load_box_secrets(["FOMO_API_KEY", "DISCORD_WEBHOOK_URL"])
    chain = os.environ.get("CHAIN", "robinhood").strip().lower()
    index = load_fomo_index()
    webhook = resolve_signal_webhook(chain)
    # latest RH buy CA
    load_box_secrets(["FOMO_API_KEY"])
    key = (os.environ.get("FOMO_API_KEY") or "").strip()
    url = "https://api.fomoapi.io/v2/alerts?type=buy&chain=robinhood&limit=15"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "meme-discord-bot/2.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode() or "{}")
    ca = ""
    sym = "不明"
    for a in body.get("alerts") or []:
        tok = (a.get("tokenAddress") or "")
        if tok.startswith("0x"):
            ca = tok.lower()
            sym = a.get("token") or "不明"
            break
    if not ca:
        print("test-fomo-holders: no RH CA in alerts")
        return 1
    holders, err = fetch_fomo_holders(ca, chain, index, limit=50)
    if err:
        print(f"test-fomo-holders fetch err={err}")
        return 1
    wallets = []
    seen = set()
    for h in holders:
        evm = (h.get("evm") or "").lower()
        rec = index.get((h.get("handle") or "").lower()) or {}
        if not evm:
            evm = (rec.get("evm") or "").lower()
        ident = evm if evm.startswith("0x") else f"handle:{(h.get('handle') or '').lower()}"
        if ident in seen:
            continue
        seen.add(ident)
        wallets.append(
            {
                "address": evm if evm.startswith("0x") else ident,
                "label": h.get("handle") or "",
                "usd": float(h.get("valueUsd") or 0),
                "symbol": sym,
                "source": "fomo_holders",
            }
        )
    s = {
        "ca": ca,
        "n": len(wallets),
        "elapsed": 0,
        "symbol": sym,
        "wallets": wallets[:12],
    }
    safety = safety_check(ca, chain)
    embed = build_embed(s, chain, safety, "fomo_holders", watch=load_watchlist(default_watchlist_path(chain), 0)[0])
    embed["title"] = ("【仮投稿】" + (embed.get("title") or ""))[:256]
    embed["footer"] = {"text": "仮投稿・紙も実弾もなし・ホルダー重なりの見た目確認"}
    discord_webhook(webhook, content="", embeds=[embed])
    print(f"test-fomo-holders posted ca={ca[:10]}… n={len(wallets)} safety_ok={safety.get('ok')}")
    return 0




def resolve_xbtscout_webhook(chain: str | None = None) -> str:
    """Optional dedicated channel; else RH/signal webhook."""
    u = (os.environ.get("DISCORD_XBTSCOUT_WEBHOOK_URL") or "").strip()
    if u:
        return u
    return resolve_signal_webhook(chain or "robinhood")


def _xbtscout_x_url(raw: str | None) -> str | None:
    """Normalize nitter/x status URL → https://x.com/xbtscout/status/<id>."""
    if not raw:
        return None
    s = str(raw).strip()
    if " | " in s and "/status/" in s.split(" | ", 1)[0]:
        s = s.split(" | ", 1)[0].strip()
    m = re.search(
        r"https?://(?:www\.)?(?:nitter\.[^\s/]+|(?:mobile\.)?(?:twitter|x)\.com)/([^\s/]+)/status/(\d+)",
        s,
        flags=re.I,
    )
    if not m:
        m = re.search(r"/([^\s/]+)/status/(\d+)", s)
        if not m:
            return None
        user, sid = m.group(1), m.group(2)
    else:
        user, sid = m.group(1), m.group(2)
    user = (user or "xbtscout").strip()
    return f"https://x.com/{user}/status/{sid}"


def _xbtscout_chain_guess(text: str | None, default: str = "robinhood") -> str:
    """Infer chain from post text. Default robinhood."""
    s = (text or "").lower()
    if re.search(r"#\s*bsc\b|\bbsc\b|binance\s*smart", s):
        return "bsc"
    if re.search(r"#\s*base\b|\bon\s*base\b", s):
        return "base"
    if re.search(r"#\s*eth\b|\bethereum\b", s):
        return "eth"
    if re.search(r"robinhood|\brh\b|#\s*rh\b", s):
        return "robinhood"
    return default


def _xbtscout_post_url_from_rec(rec: dict) -> str | None:
    for key in ("post_url", "tweet_url", "x_url", "status_url"):
        u = _xbtscout_x_url(rec.get(key))
        if u:
            return u
    return _xbtscout_x_url(rec.get("source_url_or_text_snip"))


def build_xbtscout_embed(rec: dict, chain: str) -> dict:
    """Discord embed for a new @xbtscout CA with GMGN app link + X post URL."""
    ca = (rec.get("ca") or "").strip()
    guess = (rec.get("chain_guess") or chain or "robinhood").lower()
    if guess in ("rh", "robinhoodchain"):
        guess = "robinhood"
    link = gmgn_tok.token_app_url(guess, ca)
    post_url = _xbtscout_post_url_from_rec(rec)
    snip = (rec.get("source_url_or_text_snip") or rec.get("snip") or "").strip()
    if " | " in snip:
        snip = snip.split(" | ", 1)[-1]
    snip = snip[:280]
    posted = rec.get("posted_at") or rec.get("date") or ""
    title = f"🐦 xbtscout 新CA · {guess}"
    desc_parts = [
        f"**CA** `{ca}`",
        f"**[GMGNで開く]({link})**",
    ]
    if post_url:
        desc_parts.append(f"**[Xの投稿]({post_url})**")
        desc_parts.append(post_url)
    if posted:
        desc_parts.append(f"時刻: {posted}")
    if snip:
        desc_parts.append(snip)
    fields = [
        {"name": "chain", "value": guess, "inline": True},
        {"name": "GMGN", "value": f"[app]({link})", "inline": True},
    ]
    if post_url:
        fields.append({"name": "X post", "value": f"[open]({post_url})", "inline": True})
    return {
        "title": title[:250],
        "description": "\n".join(desc_parts)[:4000],
        "url": post_url or link,
        "color": 0x1DA1F2,
        "fields": fields,
        "footer": {"text": "@xbtscout · GMGN + X post"},
    }


def notify_xbtscout_new_cas(
    new_recs: list[dict],
    webhook: str,
    chain: str = "robinhood",
    state: dict | None = None,
) -> int:
    """Post each new CA to Discord once. Returns number posted."""
    if not env_bool("XBTSCOUT_NOTIFY", True):
        return 0
    if not webhook or not new_recs:
        return 0
    notified = set()
    if state is not None:
        raw = state.get("xbtscout_notified_cas") or []
        if isinstance(raw, list):
            notified = {str(x).lower() for x in raw}
    cas_path = ROOT / "xbtscout" / "cas.jsonl"
    have = _xbtscout_load_cas(cas_path)
    posted_n = 0
    # Only Robinhood unless XBTSCOUT_NOTIFY_CHAINS overrides (comma list)
    allow_chains = {
        x.strip().lower()
        for x in (os.environ.get("XBTSCOUT_NOTIFY_CHAINS") or "robinhood,rh").split(",")
        if x.strip()
    }
    if "rh" in allow_chains:
        allow_chains.add("robinhood")
    notify_after = (os.environ.get("XBTSCOUT_NOTIFY_AFTER") or "").strip()

    for rec in new_recs:
        ca = (rec.get("ca") or "").lower().strip()
        if not ca.startswith("0x"):
            continue
        # skip if already notified (state or cas.jsonl flag)
        row = have.get(ca) or rec
        if row.get("discord_notified_at") or ca in notified:
            continue
        blob = " ".join(
            str(x or "")
            for x in (
                row.get("source_url_or_text_snip"),
                rec.get("source_url_or_text_snip"),
                row.get("snip"),
                rec.get("snip"),
            )
        )
        guess = (
            row.get("chain_guess")
            or rec.get("chain_guess")
            or _xbtscout_chain_guess(blob, "robinhood")
        )
        guess = str(guess).lower()
        if guess in ("rh", "robinhoodchain"):
            guess = "robinhood"
        if guess not in allow_chains:
            # mark skipped non-RH so we don't retry forever
            if ca in have:
                have[ca]["discord_skipped_chain"] = guess
                have[ca]["discord_notified_at"] = have[ca].get("discord_notified_at") or f"skipped:{guess}"
            else:
                have[ca] = {**row, **rec, "ca": ca, "chain_guess": guess,
                            "discord_notified_at": f"skipped:{guess}",
                            "discord_skipped_chain": guess}
            print(f"xbtscout notify skip chain={guess} {ca[:10]}…", flush=True)
            continue
        # optional: only posts after baseline (next-post mode)
        if notify_after:
            posted = str(row.get("posted_at") or rec.get("posted_at") or "")
            if posted and posted < notify_after:
                continue
        embed = build_xbtscout_embed({**row, **rec, "ca": ca, "chain_guess": guess}, chain)
        try:
            gurl = gmgn_tok.token_app_url(guess, ca)
            purl = _xbtscout_post_url_from_rec({**row, **rec, "ca": ca})
            bits = [f"🆕 xbtscout `{ca[:10]}…`", f"GMGN: {gurl}"]
            if purl:
                bits.append(f"X: {purl}")
            discord_webhook(webhook, content="\n".join(bits), embeds=[embed])
        except Exception as e:
            print(f"xbtscout notify fail {ca[:10]}… {type(e).__name__}", file=sys.stderr)
            continue
        posted_n += 1
        notified.add(ca)
        from datetime import datetime, timezone as _tz
        ts = datetime.now(_tz.utc).isoformat()
        if ca in have:
            have[ca]["discord_notified_at"] = ts
        else:
            have[ca] = {**row, "discord_notified_at": ts}
        print(f"xbtscout notify ok {ca[:10]}…", flush=True)
    # persist notifies + chain-skips
    _xbtscout_write_cas(cas_path, have)
    if state is not None:
        # keep last 500
        state["xbtscout_notified_cas"] = list(notified)[-500:]
    return posted_n


def scrape_xbtscout_cas() -> list[str]:
    """Pull new 0x CAs from nitter @xbtscout. Fail soft. Returns new CA strings.
    Also stores posted_at when RSS items can be parsed (for pre-post buyer harvest).
    """
    recs = scrape_xbtscout_posts()
    return [r["ca"] for r in recs if r.get("is_new")]


def _xbtscout_load_cas(cas_path: Path) -> dict[str, dict]:
    have: dict[str, dict] = {}
    if not cas_path.exists():
        return have
    for line in cas_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ca = (o.get("ca") or "").lower()
        if ca.startswith("0x"):
            have[ca] = o
    return have


def _xbtscout_write_cas(cas_path: Path, have: dict[str, dict]) -> None:
    cas_path.parent.mkdir(parents=True, exist_ok=True)
    # newest posted_at first, then ca
    def sort_key(r: dict):
        return (str(r.get("posted_at") or r.get("date") or ""), r.get("ca") or "")
    rows = sorted(have.values(), key=sort_key, reverse=True)
    cas_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def scrape_xbtscout_posts() -> list[dict]:
    """Return [{ca, posted_at, is_new}, ...] newest first. Persist to xbtscout/cas.jsonl."""
    cas_path = ROOT / "xbtscout" / "cas.jsonl"
    have = _xbtscout_load_cas(cas_path)
    ca_re = re.compile(r"0x[a-fA-F0-9]{40}")
    urls = [
        "https://nitter.jaydenha.uk/xbtscout/rss",
        "https://nitter.privacydev.net/xbtscout/rss",
        "https://nitter.jaydenha.uk/xbtscout",
    ]
    html = ""
    for url in urls:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "meme-discord-bot/2.0", "Accept": "text/html,application/rss+xml"},
        )
        try:
            with urllib.request.urlopen(req, timeout=18) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            if html.strip():
                print(f"xbtscout scrape ok {url.split('/')[2]} bytes={len(html)}")
                break
        except Exception as e:
            print(f"xbtscout scrape fail {url.split('/')[2]} {type(e).__name__}", file=sys.stderr)
            continue
    if not html:
        print("xbtscout scrape: no page")
        return []

    # Prefer RSS <item> blocks with pubDate
    items = re.findall(r"<item>(.*?)</item>", html, flags=re.I | re.S)
    # (ca, posted_at, post_url, snip)
    found_pairs: list[tuple[str, str | None, str | None, str | None]] = []
    if items:
        for block in items:
            pub = None
            mpub = re.search(r"<pubDate>(.*?)</pubDate>", block, flags=re.I | re.S)
            if mpub:
                raw_pub = re.sub(r"<[^>]+>", "", mpub.group(1)).strip()
                try:
                    from email.utils import parsedate_to_datetime
                    pub = parsedate_to_datetime(raw_pub).astimezone(timezone.utc).isoformat()
                except Exception:
                    pub = raw_pub or None
            link_raw = None
            mlink = re.search(r"<link>(.*?)</link>", block, flags=re.I | re.S)
            if mlink:
                link_raw = re.sub(r"<[^>]+>", "", mlink.group(1)).strip()
            if not link_raw:
                mguid = re.search(r"<guid[^>]*>(.*?)</guid>", block, flags=re.I | re.S)
                if mguid:
                    link_raw = re.sub(r"<[^>]+>", "", mguid.group(1)).strip()
            post_url = _xbtscout_x_url(link_raw)
            title = ""
            mtitle = re.search(r"<title>(.*?)</title>", block, flags=re.I | re.S)
            if mtitle:
                title = re.sub(r"<[^>]+>", "", mtitle.group(1)).strip()
            desc = ""
            mdesc = re.search(r"<description>(.*?)</description>", block, flags=re.I | re.S)
            if mdesc:
                desc = re.sub(r"<[^>]+>", " ", mdesc.group(1))
                desc = re.sub(r"\s+", " ", desc).strip()[:240]
            snip = " | ".join(x for x in [link_raw or post_url or "", title or desc] if x)
            for m in ca_re.finditer(block):
                ca = m.group(0).lower()
                if ca in SKIP_CA:
                    continue
                found_pairs.append((ca, pub, post_url, snip or None))
    else:
        for m in ca_re.finditer(html):
            ca = m.group(0).lower()
            if ca in SKIP_CA:
                continue
            window = html[max(0, m.start() - 500) : m.end() + 200]
            post_url = _xbtscout_x_url(window)
            found_pairs.append((ca, None, post_url, None))

    # dedupe keep first (newest in RSS)
    seen = set()
    ordered: list[tuple[str, str | None, str | None, str | None]] = []
    for ca, pub, post_url, snip in found_pairs:
        if ca in seen:
            continue
        seen.add(ca)
        ordered.append((ca, pub, post_url, snip))

    now = datetime.now(timezone.utc).isoformat()
    out_recs: list[dict] = []
    new_count = 0
    dirty = False
    for ca, pub, post_url, snip in ordered:
        prev = have.get(ca) or {}
        is_new = ca not in have
        # Prefer RSS pubDate; never treat legacy "Scout"/page labels as posted_at
        prev_posted = prev.get("posted_at")
        if isinstance(prev_posted, str) and prev_posted and prev_posted.lower() not in ("scout", "nitter"):
            # keep ISO-looking prior
            posted_at = pub or prev_posted
        else:
            posted_at = pub or now
        # backfill posted_at on existing rows when RSS gives a real pubDate
        if pub and prev.get("posted_at") != pub:
            dirty = True
        if is_new:
            new_count += 1
            dirty = True
        # Prefer fresh snip/post_url from RSS; keep prior if scrape lacked link
        snip_store = snip or prev.get("source_url_or_text_snip") or "nitter_xbtscout"
        post_store = post_url or prev.get("post_url") or _xbtscout_x_url(snip_store)
        store = {
            "ca": ca,
            "source_url_or_text_snip": snip_store,
            "post_url": post_store,
            "chain_guess": prev.get("chain_guess") or _xbtscout_chain_guess(snip_store, "robinhood"),
            "date": prev.get("date") if prev.get("date") and str(prev.get("date")).lower() != "scout" else now,
            "posted_at": posted_at,
            "page": prev.get("page", 0),
        }
        have[ca] = store
        out_recs.append({**store, "is_new": is_new})

    if dirty:
        _xbtscout_write_cas(cas_path, have)
    print(f"xbtscout scrape new={new_count} page_cas={len(ordered)} total={len(have)} posted_at_backfill={dirty}")
    return out_recs


def harvest_xbtscout_wallets(
    watch_path: Path,
    max_tokens: int | None = None,
    only_cas: list[str] | None = None,
) -> int:
    """GMGN early/pre-post buyers on xbtscout CAs (RH). Prefer wallets active before post time."""
    if (os.environ.get("GMGN_DISABLED") or "0").strip().lower() in ("1", "true", "yes"):
        print("harvest-xbtscout skip: GMGN_DISABLED (run on GHA)", flush=True)
        return 0
    write_gmgn_dotenv()
    max_n = int(max_tokens if max_tokens is not None else os.environ.get("XBTSCOUT_MAX_TOKENS", "3"))
    min_buy_usd = float(os.environ.get("XBTSCOUT_MIN_BUY_USD", "80"))
    pre_only = (os.environ.get("XBTSCOUT_PRE_POST_ONLY") or "1").strip().lower() in ("1", "true", "yes")
    cas_path = ROOT / "xbtscout" / "cas.jsonl"
    cas_have = _xbtscout_load_cas(cas_path)

    posts: list[dict] = []
    if only_cas:
        for c in only_cas:
            ca = (c or "").lower()
            if not ca.startswith("0x"):
                continue
            prev = cas_have.get(ca) or {}
            posts.append({
                "ca": ca,
                "posted_at": prev.get("posted_at"),
                "is_new": True,
            })
    else:
        try:
            posts = scrape_xbtscout_posts()
            cas_have = _xbtscout_load_cas(cas_path)
        except Exception as e:
            print(f"xbtscout scrape err {type(e).__name__}", file=sys.stderr)
            posts = []
    # Enrich posted_at from cas.jsonl for every post
    for p in posts:
        ca = p["ca"]
        if not p.get("posted_at") and ca in cas_have:
            p["posted_at"] = cas_have[ca].get("posted_at")

    # prefer new posts with posted_at; fall back to recent known
    batch = [p for p in posts if p.get("is_new")][:max_n]
    if not batch:
        batch = posts[:max_n]
    if not batch and cas_have:
        # offline harvest from persisted CAs (newest posted_at)
        def _cas_sort(r):
            return str(r.get("posted_at") or r.get("date") or "")
        for r in sorted(cas_have.values(), key=_cas_sort, reverse=True)[:max_n]:
            batch.append({"ca": r["ca"], "posted_at": r.get("posted_at"), "is_new": False})
    if not batch:
        print("harvest-xbtscout: no CAs")
        return 0
    cli = shutil.which("gmgn-cli")
    if not cli:
        print("harvest-xbtscout skip: gmgn-cli missing", file=sys.stderr)
        return 1

    def gmgn(args: list[str]):
        try:
            proc = subprocess.run(
                [cli, *args],
                capture_output=True,
                text=True,
                timeout=90,
                env={**os.environ},
            )
        except Exception as e:
            print(f"gmgn-cli {args[0:2]} fail {type(e).__name__}", file=sys.stderr)
            return None
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "")
        if "RATE_LIMIT" in (out + err).upper() or "429" in (out + err):
            print("harvest-xbtscout rate-limit; stopping batch", file=sys.stderr)
            return "rate"
        i = min([x for x in (out.find("{"), out.find("[")) if x >= 0], default=-1)
        if i < 0:
            return None
        try:
            return json.loads(out[i:])
        except json.JSONDecodeError:
            return None

    found: dict[str, dict] = {}
    wanted = {"smart_degen", "renowned", "smart_money"}
    queried = 0
    for rec in batch:
        ca = rec["ca"]
        data = gmgn([
            "token", "traders", "--chain", "robinhood", "--address", ca,
            "--tag", "smart_degen", "--limit", "50", "--order-by", "profit", "--raw",
        ])
        if data == "rate":
            break
        queried += 1
        rows = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("list") or data.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or []
        if not isinstance(rows, list):
            rows = []
        n_keep = 0
        n_skip_late = 0
        n_skip_size = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            addr = (r.get("address") or r.get("wallet_address") or r.get("maker") or "").lower()
            if not addr.startswith("0x") or len(addr) != 42:
                continue
            tags = r.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            mi = r.get("maker_info") or {}
            if isinstance(mi, dict):
                tags = list(tags) + list(mi.get("tags") or [])
                label = mi.get("name") or ""
            else:
                label = r.get("name") or ""
            tagset = {str(t).lower() for t in tags if t}
            if not (tagset & wanted) and "smart_degen" not in tagset:
                tagset.add("smart_degen")  # endpoint already filtered
            posted_at = rec.get("posted_at")
            buy_ts = _num(
                r.get("buy_timestamp")
                or r.get("first_buy_time")
                or r.get("start_holding_at")
                or r.get("last_active_timestamp")
                or r.get("timestamp")
            )
            buy_usd = _num(
                r.get("buy_volume_cur")
                or r.get("buy_volume")
                or r.get("amount_usd")
                or r.get("cost")
                or r.get("total_cost")
                or r.get("volume")
            )
            pre_post = False
            if posted_at and buy_ts:
                try:
                    from email.utils import parsedate_to_datetime
                    try:
                        post_dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
                    except Exception:
                        post_dt = parsedate_to_datetime(str(posted_at))
                    post_ts = post_dt.timestamp()
                    bt = float(buy_ts)
                    if bt > 1e12:
                        bt /= 1000.0
                    # bought within 6h before post, or up to 2m after (same candle)
                    if (post_ts - 6 * 3600) <= bt <= (post_ts + 120):
                        pre_post = True
                        tagset.add("xbtscout_pre_post")
                        tagset.add("xbtscout_early")
                    # early: within 30m after post
                    elif post_ts < bt <= (post_ts + 30 * 60):
                        tagset.add("xbtscout_early")
                        pre_post = True  # treat early window as keepable
                except Exception:
                    pass
            if pre_only and not pre_post:
                n_skip_late += 1
                continue
            if not pre_post:
                tagset.add("xbtscout_gmgn")
            if buy_usd is not None and buy_usd < min_buy_usd:
                n_skip_size += 1
                continue
            # Prefer meaningful size; if size unknown keep only pre_post
            if buy_usd is None and not pre_post:
                n_skip_size += 1
                continue
            pnl = _num(r.get("profit") or r.get("realized_profit") or r.get("total_profit"))
            # Skip obvious losers; allow unknown pnl for pre-post with size
            if pnl is not None and pnl < 0:
                continue
            prev = found.get(addr)
            if prev and not pre_post:
                continue
            found[addr] = {
                "address": addr,
                "address_label": label or "",
                "gmgn_tags": list(tagset),
                "gmgn_pnl_usd": pnl,
                "buy_usd": buy_usd,
                "source_ca": ca,
                "pre_post": pre_post,
            }
            n_keep += 1
        print(
            f"xbtscout {ca[:10]}… traders keep={n_keep} rows={len(rows)} "
            f"skip_late={n_skip_late} skip_size={n_skip_size} posted_at={bool(rec.get('posted_at'))}"
        )
        time.sleep(1.2)

    existing: dict[str, dict] = {}
    if watch_path.exists():
        for line in watch_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            a = (o.get("address") or "").lower()
            if a.startswith("0x"):
                existing[a] = o
    added = tagged = 0
    now = datetime.now(timezone.utc).isoformat()
    hard = float(os.environ.get("WATCH_MIN_REALIZED_HARD", str(WATCH_MIN_REALIZED_HARD)))
    for addr, w in found.items():
        pnl = w.get("gmgn_pnl_usd")
        gtags = [str(x) for x in (w.get("gmgn_tags") or [])]
        is_pre = "xbtscout_pre_post" in gtags or "xbtscout_early" in gtags
        src_add = ["xbtscout_gmgn"]
        if is_pre:
            src_add.extend(["xbtscout_pre_post", "xbtscout_early"])
        tags_add = list(dict.fromkeys(gtags + (["xbtscout_early"] if is_pre else [])))
        if addr in existing:
            o = existing[addr]
            srcs = list(o.get("source_endpoints") or [])
            changed = False
            for s in src_add:
                if s not in srcs:
                    srcs.append(s)
                    changed = True
            if changed:
                o["source_endpoints"] = srcs
                tagged += 1
            tags = list(o.get("tags") or [])
            for s in tags_add:
                if s not in tags:
                    tags.append(s)
                    changed = True
            o["tags"] = tags
            o["gmgn_tags"] = list(dict.fromkeys([*(o.get("gmgn_tags") or []), *gtags]))
            if pnl and float(pnl) > 0:
                try:
                    cur = float(o.get("realized_pnl_usd") or 0)
                except (TypeError, ValueError):
                    cur = 0.0
                if cur < 1 or cur < float(pnl):
                    o["realized_pnl_usd"] = float(pnl)
                    o["pass_pnl"] = True
            continue
        # New watch row: only if pre/early AND (real pnl >= soft floor OR meaningful buy)
        buy_usd = w.get("buy_usd")
        if pre_only and not is_pre:
            continue
        rp = float(pnl) if pnl and float(pnl) > 0 else 0.0
        if rp < 1 and (buy_usd is None or float(buy_usd) < min_buy_usd):
            continue
        # Do NOT invent $0.01 — leave low until refine/vet; DROP_WEAK will hide until PnL known
        # But for pre-post with size, stamp a soft pass so harvest isn't useless: use max(buy,1) only as note
        existing[addr] = {
            "address": addr,
            "address_label": w.get("address_label") or "",
            "realized_pnl_usd": rp if rp >= 1 else 0.0,
            "pass_pnl": bool(rp >= hard) or is_pre,
            "gmgn_pnl_usd": pnl,
            "gmgn_tags": gtags or ["smart_degen"],
            "tags": tags_add,
            "source_endpoints": src_add,
            "source_ca": w.get("source_ca"),
            "xbtscout_buy_usd": buy_usd,
            "collected_at": now,
            "chain": "robinhood",
        }
        added += 1
    watch_path.parent.mkdir(parents=True, exist_ok=True)
    with watch_path.open("w", encoding="utf-8") as f:
        for a in sorted(existing):
            f.write(json.dumps(existing[a], ensure_ascii=False) + "\n")
    pre_n = sum(
        1
        for o in existing.values()
        if "xbtscout_pre_post" in (o.get("tags") or [])
        or "xbtscout_early" in (o.get("tags") or [])
        or "xbtscout_pre_post" in (o.get("gmgn_tags") or [])
        or "xbtscout_early" in (o.get("gmgn_tags") or [])
        or "xbtscout_pre_post" in (o.get("source_endpoints") or [])
    )
    print(
        f"harvest-xbtscout queried={queried} found={len(found)} added={added} "
        f"tagged={tagged} watch={len(existing)} xbtscout_pre_or_early={pre_n}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="poll forever")
    p.add_argument("--test-webhook", action="store_true")
    p.add_argument(
        "--test-live-webhook",
        action="store_true",
        help="Post a sample Arc LIVE PnL embed (no swap)",
    )
    p.add_argument("--test-fomo-holders", action="store_true", help="仮投稿: live FOMO holder overlap, no paper")
    p.add_argument("--refresh-wallets", action="store_true", help="Nansen pnl-leaderboard → wallets.jsonl (infrequent)")
    p.add_argument("--refine-wallets", action="store_true", help="Drop losers / banned labels from wallets.jsonl")
    p.add_argument("--refresh-fomo", action="store_true", help="FOMO 7d leaderboard → drop fallen FOMO-only wallets")
    p.add_argument("--harvest-xbtscout", action="store_true", help="Scrape @xbtscout new CAs and GMGN-tag 1")
    p.add_argument("--notify-xbtscout", action="store_true", help="Scrape @xbtscout and Discord-notify new CAs with GMGN links")
    p.add_argument("--paper-summary", action="store_true", help="Write paper_summary.md from logs/state")
    p.add_argument("--pages", type=int, default=2, help="legacy Nansen pages if NANSEN_FOR_TRADES=1")
    p.add_argument("--per-page", type=int, default=100, help="GMGN --limit / Nansen per_page")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    load_box_secrets()
    live_trading_blocked()

    if args.test_webhook:
        url = resolve_signal_webhook()
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

    if args.test_live_webhook:
        url = resolve_live_webhook("arc")
        if not url:
            print("test-live-webhook: no webhook resolved", file=sys.stderr)
            return 1
        discord_webhook(
            url,
            content="",
            embeds=[
                {
                    "title": "🟢 実弾エントリー · $TEST",
                    "description": (
                        "**買った** · サイズ 20% · **$16.00**\n"
                        "監視財布 n=2 · 入口 $0.00012345\n"
                        "[GMGN](https://gmgn.ai/arc/token/0xtest) · `0xtest…`\n\n"
                        "実現PnL **+$0.00** · オープン **1/5** · 拘束中 ~$16"
                    ),
                    "color": 0x2ECC71,
                    "footer": {"text": "⚡ Arc 実弾 · 自動売買"},
                }
            ],
        )
        print("test-live-webhook ok")
        return 0

    if args.test_fomo_holders:
        return test_fomo_holders_post()

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

    if args.refine_wallets:
        chain = os.environ.get("CHAIN", "robinhood").strip().lower()
        watch_path = Path(
            os.environ.get("WATCHLIST_PATH", str(default_watchlist_path(chain)))
        ).resolve()
        return refine_wallets(watch_path)

    if args.refresh_wallets:
        chain = os.environ.get("CHAIN", "robinhood").strip().lower()
        watch_path = Path(
            os.environ.get("WATCHLIST_PATH", str(default_watchlist_path(chain)))
        ).resolve()
        return refresh_wallets_nansen(watch_path, pages=max(1, args.pages))

    if args.refresh_fomo:
        chain = os.environ.get("CHAIN", "robinhood").strip().lower()
        watch_path = Path(
            os.environ.get("WATCHLIST_PATH", str(default_watchlist_path(chain)))
        ).resolve()
        return refresh_fomo_wallets(watch_path)

    if args.notify_xbtscout:
        chain = (os.environ.get("CHAIN") or "robinhood").strip().lower()
        recs = [r for r in scrape_xbtscout_posts() if r.get("is_new")]
        # also allow force-notify of latest N unnotified
        if not recs and env_bool("XBTSCOUT_NOTIFY_BACKFILL", False):
            cas_path = ROOT / "xbtscout" / "cas.jsonl"
            have = _xbtscout_load_cas(cas_path)
            recs = [o for o in have.values() if not o.get("discord_notified_at")]
            recs = sorted(recs, key=lambda o: str(o.get("posted_at") or ""), reverse=True)[: int(os.environ.get("XBTSCOUT_NOTIFY_BACKFILL_N", "5"))]
        hook = resolve_xbtscout_webhook(chain)
        n = notify_xbtscout_new_cas(recs, hook, chain, state=None)
        print(f"notify-xbtscout posted={n}")
        return 0 if n >= 0 else 1
    if args.harvest_xbtscout:
        chain = os.environ.get("CHAIN", "robinhood").strip().lower()
        watch_path = Path(
            os.environ.get("WATCHLIST_PATH", str(default_watchlist_path(chain)))
        ).resolve()
        return harvest_xbtscout_wallets(watch_path)

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

#!/usr/bin/env python3
"""On-chain / Gecko trade-history PnL estimates for weak RH wallets (no box GMGN).

Targets wallets with tags `pnl_pending` / `unknown_trend` or missing `realized_pnl_usd`.

Pipeline:
  1) Unique CAs from scout_token_cas (and optional RPC Transfer supplement)
  2) Gecko pool trades via jina relay (box Dex/Gecko often 429) — buy vs sell USD
  3) Optional mark-to-market of remaining inventory from last trade / token_price
  4) Write `realized_pnl_usd_est` + `pnl_source=onchain_est`; promote to
     `realized_pnl_usd` + clear `pnl_pending` + set `pass_pnl` when quality gate passes
  5) Auto-fill weak gaps: address_label, n_trades, win_rate proxy, last_active

Outputs:
  rh-wallets/summary_onchain_pnl.md
  rh-wallets/pnl_fill_log.jsonl
  rh-wallets/wallets.jsonl (updated in place; backup first)

Env:
  LIVE_TRADING=0  GMGN_DISABLED=1
  PNL_EST_CAP=95              max wallets this run
  PNL_EST_CA_CONC=3           concurrent CA fetches
  PNL_EST_MIN_TRADES=2        quality gate
  PNL_EST_MIN_USD=1.0         min |buy|+|sell| coverage
  PNL_EST_PROMOTE_FLOOR=0     promote when est > floor (default any >0)
  PNL_EST_PROMOTE=1           write realized_pnl_usd when gate passes
  PNL_EST_MTM=1               include remaining inventory mark
  PNL_EST_ONLY_PENDING=1      prefer addrs file / pnl_pending first
  PNL_EST_DRY_RUN=0
  RH_RPC_URL                  optional Transfer supplement (off by default)
  PNL_EST_USE_RPC=0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

JST = timezone(timedelta(hours=9))
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)
RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()
JINA = "https://r.jina.ai/http://"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

WATCH = Path(os.environ.get("WATCHLIST_PATH") or (ROOT / "rh-wallets" / "wallets.jsonl"))
ADDRS_FILE = ROOT / "rh-wallets" / "raw" / "unknown_trend_pnl_pending_addrs.txt"
SUMMARY = ROOT / "rh-wallets" / "summary_onchain_pnl.md"
LOG_PATH = ROOT / "rh-wallets" / "pnl_fill_log.jsonl"
BACKUP = ROOT / "rh-wallets" / "raw" / "wallets_pre_onchain_pnl.jsonl"
STATE_PATH = ROOT / "rh-wallets" / "raw" / "onchain_pnl_state.json"

SKIP_TOKENS = {
    "0x0000000000000000000000000000000000000000",
    "0x4200000000000000000000000000000000000006",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
}

_http_lock = threading.Lock()
_jina_backoff = 0.35


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} onchain-pnl {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def norm_addr(a: str | None) -> str | None:
    if not a or not isinstance(a, str):
        return None
    a = a.strip().lower()
    if not a.startswith("0x") or len(a) != 42:
        return None
    return a


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def http_get(url: str, timeout: float = 45.0) -> tuple[str | None, str | None]:
    last = None
    for attempt in range(5):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            wait = min(18.0, 0.6 * (2**attempt))
            if e.code in (429, 403, 502, 503, 504):
                time.sleep(wait)
                continue
            time.sleep(wait * 0.4)
        except Exception as e:
            last = type(e).__name__
            time.sleep(min(12.0, 0.8 * (2**attempt)))
    return None, last


def extract_json(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    content = raw
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict):
            c = outer.get("data")
            if isinstance(c, dict):
                c = c.get("content") or c.get("text") or ""
            if isinstance(c, str) and c.strip():
                content = c
    except json.JSONDecodeError:
        content = raw
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[-1]
    content = re.sub(r"^```\w*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content)
    prefer = None
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, content)
        if not m:
            continue
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed
        if prefer is None and isinstance(parsed, (dict, list)):
            prefer = parsed
    return prefer


def jina_get_json(api_url: str, timeout: float = 45.0):
    global _jina_backoff
    url = api_url
    if not url.startswith("http"):
        url = "https://" + url
    if url.startswith("https://"):
        relay = JINA + url[len("https://") :]
    else:
        relay = JINA + url[len("http://") :]
    with _http_lock:
        time.sleep(_jina_backoff)
    raw, err = http_get(relay, timeout=timeout)
    if raw is None:
        with _http_lock:
            _jina_backoff = min(4.0, _jina_backoff * 1.4)
        return None, err or "empty"
    data = extract_json(raw)
    if data is None:
        with _http_lock:
            _jina_backoff = min(4.0, _jina_backoff * 1.2)
        return None, "parse_fail"
    with _http_lock:
        _jina_backoff = max(0.2, _jina_backoff * 0.92)
    return data, None


class RpcClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._backoff = 0.4
        self.calls = 0
        self.errors_429 = 0

    def _post(self, payload: bytes, label: str) -> object:
        last_err: Exception | None = None
        for attempt in range(6):
            req = urllib.request.Request(
                self.url,
                data=payload,
                headers={
                    "content-type": "application/json",
                    "User-Agent": UA,
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                self.calls += 1
                self._backoff = max(0.25, self._backoff * 0.9)
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                wait = min(20.0, self._backoff * (2**attempt))
                if e.code in (429, 403, 502, 503, 504):
                    self.errors_429 += 1
                    log(f"rpc HTTP {e.code} {label} sleep={wait:.1f}s")
                    time.sleep(wait)
                    self._backoff = min(8.0, self._backoff * 1.5)
                    continue
                time.sleep(wait)
            except Exception as e:
                last_err = e
                time.sleep(min(12.0, self._backoff * (2**attempt)))
        raise RuntimeError(f"rpc fail {label}: {last_err}")

    def call(self, method: str, params: list) -> object:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode()
        body = self._post(payload, method)
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            msg = str(err.get("message") or err)
            if "rate" in msg.lower() or "429" in msg:
                self.errors_429 += 1
                time.sleep(min(12.0, self._backoff * 2))
                self._backoff = min(8.0, self._backoff * 1.4)
                return self.call(method, params)
            raise RuntimeError(msg)
        return body.get("result") if isinstance(body, dict) else None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def resolve_pool_for_ca(ca: str) -> tuple[str | None, float]:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/{ca}/pools?page=1"
    )
    if err or not isinstance(data, dict):
        return None, 0.0
    rows = data.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None, 0.0
    ca_l = ca.lower()
    scored: list[tuple[int, float, str]] = []
    for row in rows:
        attrs = row.get("attributes") or {}
        rel = row.get("relationships") or {}
        base_id = (((rel.get("base_token") or {}).get("data") or {}).get("id") or "").lower()
        is_base = 1 if ca_l in base_id else 0
        reserve = _f(attrs.get("reserve_in_usd")) or 0.0
        addr = (attrs.get("address") or "").lower()
        if not addr and isinstance(row.get("id"), str) and "_" in row["id"]:
            addr = row["id"].split("_", 1)[-1].lower()
        if addr:
            scored.append((is_base, reserve, addr))
    if not scored:
        return None, 0.0
    scored.sort(reverse=True)
    return scored[0][2], scored[0][1]


def fetch_pool_trades(pool: str) -> list[dict]:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/robinhood/pools/{pool}/trades"
    )
    if err or not isinstance(data, dict):
        log(f"trades pool={pool[:12]}… err={err}")
        return []
    rows = data.get("data") or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        attrs = row.get("attributes") or {}
        if isinstance(attrs, dict):
            out.append(attrs)
    return out


def fetch_token_price(ca: str) -> float | None:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/simple/networks/robinhood/token_price/{ca}"
    )
    if err or not isinstance(data, dict):
        return None
    prices = (data.get("data") or {}).get("attributes") or {}
    # shape: {"token_prices": {"0x..": "0.01"}}
    tp = prices.get("token_prices") if isinstance(prices, dict) else None
    if isinstance(tp, dict):
        for k, v in tp.items():
            if k.lower() == ca.lower():
                return _f(v)
        # any single
        if len(tp) == 1:
            return _f(next(iter(tp.values())))
    return _f(prices.get("price_usd") or prices.get(ca))


def index_trades_for_ca(ca: str) -> dict:
    """Fetch pool trades for CA; return {wallet: [legs...], price, pool, n_trades}."""
    ca = ca.lower()
    pool, reserve = resolve_pool_for_ca(ca)
    if not pool:
        return {"ca": ca, "pool": None, "by_wallet": {}, "price": None, "n_pool_trades": 0, "reserve": 0.0}
    trades = fetch_pool_trades(pool)
    by_wallet: dict[str, list[dict]] = defaultdict(list)
    last_px: float | None = None
    for a in trades:
        w = norm_addr(a.get("tx_from_address") or a.get("from_address") or a.get("maker"))
        if not w:
            continue
        to_tok = (a.get("to_token_address") or "").lower()
        from_tok = (a.get("from_token_address") or "").lower()
        vol = _f(a.get("volume_in_usd")) or 0.0
        to_amt = _f(a.get("to_token_amount")) or 0.0
        from_amt = _f(a.get("from_token_amount")) or 0.0
        kind = (a.get("kind") or "").lower()
        px_to = _f(a.get("price_to_in_usd"))
        px_from = _f(a.get("price_from_in_usd"))
        side = None
        tok_amt = 0.0
        px = None
        if to_tok == ca:
            side = "buy"
            tok_amt = to_amt
            px = px_to
        elif from_tok == ca:
            side = "sell"
            tok_amt = from_amt
            px = px_from
        elif kind == "buy":
            side = "buy"
            px = px_to or px_from
        elif kind == "sell":
            side = "sell"
            px = px_from or px_to
        else:
            continue
        if px and px > 0:
            last_px = px
        by_wallet[w].append(
            {
                "side": side,
                "vol_usd": vol,
                "tok_amt": tok_amt,
                "px": px,
                "ts": a.get("block_timestamp"),
                "tx": a.get("tx_hash"),
                "ca": ca,
            }
        )
    return {
        "ca": ca,
        "pool": pool,
        "by_wallet": dict(by_wallet),
        "price": last_px,
        "n_pool_trades": len(trades),
        "reserve": reserve,
    }


def estimate_wallet(
    addr: str,
    cas: list[str],
    ca_index: dict[str, dict],
    *,
    use_mtm: bool,
) -> dict:
    buy_usd = 0.0
    sell_usd = 0.0
    n_buy = 0
    n_sell = 0
    inv: dict[str, float] = defaultdict(float)
    prices: dict[str, float] = {}
    last_ts: str | None = None
    cas_hit: set[str] = set()
    legs: list[dict] = []

    for ca in cas:
        ca = ca.lower()
        if ca in SKIP_TOKENS:
            continue
        info = ca_index.get(ca) or {}
        if info.get("price"):
            prices[ca] = float(info["price"])
        for leg in (info.get("by_wallet") or {}).get(addr) or []:
            legs.append(leg)
            cas_hit.add(ca)
            vol = float(leg.get("vol_usd") or 0)
            amt = float(leg.get("tok_amt") or 0)
            px = leg.get("px")
            if px:
                prices[ca] = float(px)
            ts = leg.get("ts")
            if isinstance(ts, str) and (last_ts is None or ts > last_ts):
                last_ts = ts
            if leg.get("side") == "buy":
                buy_usd += vol
                n_buy += 1
                inv[ca] += amt
            elif leg.get("side") == "sell":
                sell_usd += vol
                n_sell += 1
                inv[ca] -= amt

    mtm = 0.0
    if use_mtm:
        for ca, amt in inv.items():
            if amt <= 1e-12:
                continue
            px = prices.get(ca)
            if not px or px <= 0:
                continue
            mtm += amt * px

    realized_est = sell_usd - buy_usd
    total_est = realized_est + mtm
    n_trades = n_buy + n_sell

    # win_rate proxy: per-CA closed rounds where sell_usd > buy_usd
    per_ca: dict[str, dict] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for leg in legs:
        ca = leg["ca"]
        if leg["side"] == "buy":
            per_ca[ca]["buy"] += float(leg["vol_usd"] or 0)
        else:
            per_ca[ca]["sell"] += float(leg["vol_usd"] or 0)
    wins = 0
    closed = 0
    for v in per_ca.values():
        if v["buy"] <= 0 and v["sell"] <= 0:
            continue
        if v["sell"] > 0 or v["buy"] > 0:
            closed += 1
            # profitable if sells exceed buys (realized on that CA), or MTM+sells
            ca_pnl = v["sell"] - v["buy"]
            if ca_pnl > 0:
                wins += 1
    win_rate = (wins / closed) if closed else None

    coverage = len(cas_hit) / max(1, len([c for c in cas if c.lower() not in SKIP_TOKENS]))
    quality = "null"
    if n_trades <= 0:
        quality = "null"
    elif n_trades < env_int("PNL_EST_MIN_TRADES", 2) or (buy_usd + sell_usd) < env_float(
        "PNL_EST_MIN_USD", 1.0
    ):
        quality = "low"
    elif coverage < 0.34 and len(cas) >= 3:
        quality = "partial"
    else:
        quality = "ok"

    return {
        "realized_pnl_usd_est": round(total_est, 6),
        "realized_only_usd_est": round(realized_est, 6),
        "mtm_usd_est": round(mtm, 6),
        "buy_usd_est": round(buy_usd, 6),
        "sell_usd_est": round(sell_usd, 6),
        "n_buys_est": n_buy,
        "n_sells_est": n_sell,
        "n_trades_est": n_trades,
        "win_rate_est": round(win_rate, 6) if win_rate is not None else None,
        "cas_hit": sorted(cas_hit),
        "cas_coverage": round(coverage, 4),
        "last_active_est": last_ts,
        "est_quality": quality,
        "legs_n": len(legs),
    }


def select_targets(rows: list[dict], cap: int, only_pending: bool) -> list[dict]:
    prefer_addrs: set[str] = set()
    if ADDRS_FILE.exists():
        for line in ADDRS_FILE.read_text(encoding="utf-8").splitlines():
            a = norm_addr(line.strip())
            if a:
                prefer_addrs.add(a)

    scored: list[tuple[int, float, dict]] = []
    for r in rows:
        addr = norm_addr(r.get("address"))
        if not addr:
            continue
        tags = r.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags_l = [str(t) for t in tags]
        pnl = r.get("realized_pnl_usd")
        pending = "pnl_pending" in tags_l or "unknown_trend" in tags_l or pnl is None
        if only_pending and not pending:
            continue
        if pnl is not None and "pnl_pending" not in tags_l and r.get("pnl_source") != "onchain_est":
            # already confident fill — skip unless explicitly pending
            if "pnl_pending" not in tags_l:
                continue
        # priority: addrs file > pnl_pending > unknown_trend > missing pnl
        pri = 0
        if addr in prefer_addrs:
            pri += 100
        if "pnl_pending" in tags_l:
            pri += 40
        if "unknown_trend" in tags_l:
            pri += 20
        if pnl is None:
            pri += 10
        if r.get("realized_pnl_usd_est") is None:
            pri += 5
        qs = _f(r.get("quality_score") or r.get("trend_score")) or 0.0
        scored.append((pri, qs, r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    # de-dupe by address keeping first
    seen: set[str] = set()
    out: list[dict] = []
    for _, __, r in scored:
        a = norm_addr(r.get("address"))
        if not a or a in seen:
            continue
        seen.add(a)
        out.append(r)
        if len(out) >= cap:
            break
    return out


def fill_weak_fields(row: dict, est: dict) -> list[str]:
    """Mutate row with weak-field fills; return list of filled keys."""
    filled: list[str] = []
    addr = norm_addr(row.get("address")) or ""
    short = f"{addr[:6]}…{addr[-4:]}" if len(addr) >= 10 else addr

    # label
    lab = row.get("address_label") or row.get("label")
    syms = row.get("symbols_seen") or []
    if isinstance(syms, str):
        syms = [syms]
    weak_lab = (
        not lab
        or str(lab).startswith("unknown_trend")
        or str(lab).startswith("0x")
        or "[0x" in str(lab)
    )
    if weak_lab and syms:
        top = ",".join(str(s) for s in syms[:3] if s)
        if top:
            row["address_label"] = f"trend:{top} [{short}]"
            filled.append("address_label")
    elif weak_lab and est.get("cas_hit"):
        row["address_label"] = f"onchain_est [{short}]"
        filled.append("address_label")

    # n_trades
    if row.get("n_trades") is None and est.get("n_trades_est"):
        row["n_trades"] = int(est["n_trades_est"])
        filled.append("n_trades")
    elif row.get("n_trades") is None and row.get("n_buys"):
        # proxy from promote fields
        nb = int(row.get("n_buys") or 0)
        if nb > 0:
            row["n_trades"] = nb
            filled.append("n_trades")

    # win_rate proxy
    if row.get("win_rate") is None and est.get("win_rate_est") is not None:
        row["win_rate"] = est["win_rate_est"]
        row["win_rate_source"] = "onchain_est_proxy"
        filled.append("win_rate")

    # last_active
    la = est.get("last_active_est") or row.get("last_seen")
    if la and not row.get("last_active"):
        row["last_active"] = la
        filled.append("last_active")

    # always stamp est fields when we have trades or attempted
    if est.get("est_quality") != "null" or est.get("n_trades_est", 0) > 0:
        row["realized_pnl_usd_est"] = est["realized_pnl_usd_est"]
        row["pnl_source"] = "onchain_est"
        row["pnl_est_meta"] = {
            "realized_only_usd_est": est["realized_only_usd_est"],
            "mtm_usd_est": est["mtm_usd_est"],
            "buy_usd_est": est["buy_usd_est"],
            "sell_usd_est": est["sell_usd_est"],
            "n_buys_est": est["n_buys_est"],
            "n_sells_est": est["n_sells_est"],
            "cas_coverage": est["cas_coverage"],
            "est_quality": est["est_quality"],
            "estimated_at": now_iso(),
        }
        filled.append("realized_pnl_usd_est")

    return filled


def quality_gate_pass(est: dict, promote_floor: float) -> bool:
    if est.get("est_quality") not in ("ok", "partial"):
        return False
    if (est.get("n_trades_est") or 0) < env_int("PNL_EST_MIN_TRADES", 2):
        return False
    total = _f(est.get("realized_pnl_usd_est"))
    if total is None:
        return False
    return total > promote_floor


def apply_promote(row: dict, est: dict, promote_floor: float) -> bool:
    if not quality_gate_pass(est, promote_floor):
        return False
    total = float(est["realized_pnl_usd_est"])
    # Only overwrite realized when missing or previous was onchain_est
    prev_src = row.get("pnl_source")
    prev = row.get("realized_pnl_usd")
    if prev is not None and prev_src not in (None, "onchain_est"):
        return False
    row["realized_pnl_usd"] = total
    row["realized_pnl_usd_est"] = total
    row["pnl_source"] = "onchain_est"
    if total > promote_floor:
        row["pass_pnl"] = True
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = [t for t in tags if t != "pnl_pending"]
    if "onchain_pnl_est" not in tags:
        tags.append("onchain_pnl_est")
    row["tags"] = tags
    return True


def write_summary(stats: dict) -> None:
    lines = [
        "# On-chain PnL estimates (Gecko trades via jina; no box GMGN)",
        "",
        f"- Updated: **{now_jst_str()}**",
        f"- Targets attempted: **{stats.get('attempted')}**",
        f"- With trade hits: **{stats.get('with_hits')}**",
        f"- Estimates (non-null): **{stats.get('est_n')}**",
        "",
        "## Estimate buckets (`realized_pnl_usd_est` — honest estimate)",
        "",
        "| bucket | count |",
        "|--------|------:|",
        f"| positive (>) | **{stats.get('pos')}** |",
        f"| negative (<) | **{stats.get('neg')}** |",
        f"| zero (~0) | **{stats.get('zero')}** |",
        f"| still null / no trades | **{stats.get('null')}** |",
        "",
        f"- Promoted to `realized_pnl_usd` (quality gate): **{stats.get('promoted')}**",
        f"- `pass_pnl` set: **{stats.get('pass_pnl')}**",
        f"- `pnl_pending` cleared: **{stats.get('cleared_pending')}**",
        f"- Weak fields filled (label/n_trades/wr/last_active): **{stats.get('weak_fills')}**",
        f"- Unique CAs fetched: **{stats.get('cas_n')}** (rpc_429={stats.get('rpc_429', 0)})",
        "",
        "## Notes",
        "",
        "- Source: GeckoTerminal pool trades (`tx_from_address`) via jina relay.",
        "- Remaining inventory optionally marked at last observed trade price (`PNL_EST_MTM`).",
        "- **Estimates only** — not GMGN-vetted truth. Fields: `realized_pnl_usd_est`, `pnl_source=onchain_est`.",
        "- Quality gate promote → `realized_pnl_usd` + remove `pnl_pending` when est>floor and coverage ok.",
        "",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap", type=int, default=None, help="max wallets")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all-pending", action="store_true", help="all pnl_pending not just addrs file")
    ap.add_argument("--addrs-file", type=str, default="", help="override priority addrs file")
    args = ap.parse_args()

    cap = args.cap if args.cap is not None else env_int("PNL_EST_CAP", 95)
    dry = args.dry_run or env_bool("PNL_EST_DRY_RUN", False)
    only_pending = env_bool("PNL_EST_ONLY_PENDING", True)
    use_mtm = env_bool("PNL_EST_MTM", True)
    do_promote = env_bool("PNL_EST_PROMOTE", True)
    promote_floor = env_float("PNL_EST_PROMOTE_FLOOR", 0.0)
    ca_conc = max(1, env_int("PNL_EST_CA_CONC", 3))
    use_rpc = env_bool("PNL_EST_USE_RPC", False)

    global ADDRS_FILE
    if args.addrs_file:
        ADDRS_FILE = Path(args.addrs_file)

    rows = load_jsonl(WATCH)
    if not rows:
        log(f"empty watchlist {WATCH}")
        return 1

    targets = select_targets(rows, cap=cap, only_pending=only_pending)
    if args.all_pending:
        # expand: already selected by tag; just note
        pass
    log(f"targets={len(targets)} cap={cap} dry={dry} mtm={use_mtm} promote={do_promote} floor={promote_floor}")

    # gather CAs
    ca_set: set[str] = set()
    target_by_addr: dict[str, dict] = {}
    for r in targets:
        a = norm_addr(r.get("address"))
        if not a:
            continue
        target_by_addr[a] = r
        for c in r.get("scout_token_cas") or []:
            ca = norm_addr(c) if isinstance(c, str) and c.startswith("0x") else None
            # scout_token_cas are token addrs — may be 42 chars
            if isinstance(c, str):
                cl = c.strip().lower()
                if cl.startswith("0x") and len(cl) == 42 and cl not in SKIP_TOKENS:
                    ca_set.add(cl)

    log(f"unique_cas={len(ca_set)} conc={ca_conc}")

    ca_index: dict[str, dict] = {}
    if ca_set:
        with ThreadPoolExecutor(max_workers=ca_conc) as ex:
            futs = {ex.submit(index_trades_for_ca, ca): ca for ca in sorted(ca_set)}
            done = 0
            for fut in as_completed(futs):
                ca = futs[fut]
                try:
                    ca_index[ca] = fut.result()
                except Exception as e:
                    log(f"ca fail {ca[:10]}… {type(e).__name__}: {e}")
                    ca_index[ca] = {
                        "ca": ca,
                        "pool": None,
                        "by_wallet": {},
                        "price": None,
                        "n_pool_trades": 0,
                        "reserve": 0.0,
                    }
                done += 1
                if done % 5 == 0 or done == len(futs):
                    log(f"ca progress {done}/{len(futs)}")

    # optional price backfill for MTM
    if use_mtm:
        need_px = [ca for ca, info in ca_index.items() if not info.get("price") and info.get("pool")]
        for ca in need_px[:20]:
            px = fetch_token_price(ca)
            if px:
                ca_index[ca]["price"] = px

    rpc_429 = 0
    if use_rpc:
        try:
            rpc = RpcClient(RPC_URL)
            # light no-op probe
            rpc.call("eth_blockNumber", [])
            rpc_429 = rpc.errors_429
        except Exception as e:
            log(f"rpc probe fail: {e}")

    # estimate each target
    by_addr_row = {norm_addr(r.get("address")): r for r in rows}
    stats = {
        "attempted": 0,
        "with_hits": 0,
        "est_n": 0,
        "pos": 0,
        "neg": 0,
        "zero": 0,
        "null": 0,
        "promoted": 0,
        "pass_pnl": 0,
        "cleared_pending": 0,
        "weak_fills": 0,
        "cas_n": len(ca_index),
        "rpc_429": rpc_429,
    }
    log_recs: list[dict] = []

    for addr, src in target_by_addr.items():
        stats["attempted"] += 1
        cas = []
        for c in src.get("scout_token_cas") or []:
            if isinstance(c, str):
                cl = c.strip().lower()
                if cl.startswith("0x") and len(cl) == 42:
                    cas.append(cl)
        est = estimate_wallet(addr, cas, ca_index, use_mtm=use_mtm)
        row = by_addr_row.get(addr) or src

        if est["n_trades_est"] > 0:
            stats["with_hits"] += 1

        filled = fill_weak_fields(row, est)
        stats["weak_fills"] += len([k for k in filled if k != "realized_pnl_usd_est"])

        promoted = False
        had_pending = "pnl_pending" in (row.get("tags") or [])
        if do_promote and est["est_quality"] != "null":
            promoted = apply_promote(row, est, promote_floor)
            if promoted:
                stats["promoted"] += 1
                if row.get("pass_pnl"):
                    stats["pass_pnl"] += 1
                if had_pending and "pnl_pending" not in (row.get("tags") or []):
                    stats["cleared_pending"] += 1

        val = est.get("realized_pnl_usd_est") if est["est_quality"] != "null" else None
        if val is None or est["n_trades_est"] <= 0:
            stats["null"] += 1
            bucket = "null"
        else:
            stats["est_n"] += 1
            if val > 1e-9:
                stats["pos"] += 1
                bucket = "pos"
            elif val < -1e-9:
                stats["neg"] += 1
                bucket = "neg"
            else:
                stats["zero"] += 1
                bucket = "zero"

        rec = {
            "ts": now_iso(),
            "address": addr,
            "bucket": bucket,
            "est": est,
            "promoted": promoted,
            "filled": filled,
            "pass_pnl": bool(row.get("pass_pnl")),
        }
        log_recs.append(rec)
        if stats["attempted"] <= 5 or promoted:
            log(
                f"{addr[:10]}… q={est['est_quality']} est={val} "
                f"trades={est['n_trades_est']} promoted={promoted}"
            )

    write_summary(stats)
    for rec in log_recs:
        if not dry:
            append_jsonl(LOG_PATH, rec)

    if not dry:
        # backup + write
        try:
            shutil.copy2(WATCH, BACKUP)
        except Exception as e:
            log(f"backup warn: {e}")
        write_jsonl(WATCH, rows)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "updated_at": now_iso(),
                    "updated_jst": now_jst_str(),
                    "stats": stats,
                    "cap": cap,
                    "cas_n": len(ca_index),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        log("dry-run: skipped wallets.jsonl write")

    log(
        f"done pos={stats['pos']} neg={stats['neg']} zero={stats['zero']} "
        f"null={stats['null']} promoted={stats['promoted']} summary={SUMMARY}"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "pos": stats["pos"],
                "neg": stats["neg"],
                "zero": stats["zero"],
                "null": stats["null"],
                "promoted": stats["promoted"],
                "pass_pnl": stats["pass_pnl"],
                "attempted": stats["attempted"],
                "with_hits": stats["with_hits"],
                "estimate": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

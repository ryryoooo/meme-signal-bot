#!/usr/bin/env python3
"""Discover UNKNOWN active smart wallets from trending / proven-mover tokens on RH.

Pipeline (no GMGN / LIVE_TRADING=0 / prefer free RPC + Gecko/Dex via jina):
  1) Trending CAs — GeckoTerminal trending pools + DexScreener boosts/profiles
     (robinhood only) via jina relay; plus artifacts/notify_peak_2x.json mult_peak≥2
  2) Buyers per CA — Gecko pool trades (kind=buy → tx_from_address) via jina;
     optional eth_getLogs Transfer `to` on free RH RPC for recent window
  3) Exclude all known seed/watch addresses aggressively
  4) Score multi-CA / recent / non-dust buyers → write candidates (no auto-merge)

Outputs:
  rh-wallets/unknown_trend_smart.jsonl
  rh-wallets/summary_unknown_trend_smart.md
  rh-wallets/watch_candidates_unknown.jsonl  (top N high-score)

Env:
  RH_RPC_URL, TREND_CA_CAP (default 30), TREND_TOP_WATCH (default 25),
  TREND_MIN_SCORE (default 8), TREND_LOG_BLOCKS (default 800),
  TREND_USE_RPC_LOGS=1, TREND_DAEMON=1 / TREND_INTERVAL_SEC=900, TREND_ONCE=1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
JST = timezone(timedelta(hours=9))
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)
RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()
JINA = "https://r.jina.ai/http://"

# Stable / wrapped / quote tokens — not meme CAs, and not "buyers"
SKIP_TOKENS = {
    ZERO,
    "0x4200000000000000000000000000000000000006",  # WETH (op-style)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG (RH stable)
}

# Common infra / burn / zero — never candidates
SKIP_ADDRS = {
    ZERO,
    "0x000000000000000000000000000000000000dead",
    "0x00000000000000000000000000000000000a4b05",
    "0xffffffffffffffffffffffffffffffffffffffff",
}

OUT_JSONL = ROOT / "rh-wallets" / "unknown_trend_smart.jsonl"
OUT_MD = ROOT / "rh-wallets" / "summary_unknown_trend_smart.md"
OUT_CAND = ROOT / "rh-wallets" / "watch_candidates_unknown.jsonl"
STATE_PATH = ROOT / "rh-wallets" / "raw" / "unknown_trend_hunt_state.json"

KNOWN_SEED_GLOBS = [
    ROOT / "rh-wallets" / "wallets.jsonl",
    ROOT / "rh-wallets" / "wallets_onchain.jsonl",
    ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl",
    ROOT / "rh-wallets" / "wallets_ranked_all.jsonl",
    ROOT / "rh-wallets" / "onchain_hot_active.jsonl",
    ROOT / "rh-wallets" / "audit_active.jsonl",
    ROOT / "rh-wallets" / "audit_active_quality_plus.jsonl",
    ROOT / "rh-wallets" / "audit_quality.jsonl",
    ROOT / "fomo-wallets" / "wallets_evm.jsonl",
    ROOT / "fomo-wallets" / "degentape_wallets.jsonl",
    ROOT / "fomo-wallets" / "leaderboard.jsonl",
    ROOT / "arc-wallets" / "wallets.jsonl",
    ROOT / "arc-wallets" / "wallets_early.jsonl",
    ROOT / "arc-wallets" / "wallets_quality.jsonl",
]


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


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} trend-hunt {msg}", flush=True)


def _f(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def norm_addr(a: str | None) -> str | None:
    if not isinstance(a, str):
        return None
    al = a.strip().lower()
    if al.startswith("0x") and len(al) == 42:
        return al
    return None


def topic_addr(topic: str) -> str:
    t = (topic or "").lower()
    if t.startswith("0x"):
        t = t[2:]
    return "0x" + t[-40:]


def http_get(url: str, timeout: float = 35.0, retries: int = 4) -> tuple[str | None, str | None]:
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            wait = min(20.0, 1.2 * (2**attempt))
            if e.code in (429, 403, 502, 503, 504):
                time.sleep(wait)
                continue
            time.sleep(wait * 0.5)
        except Exception as e:
            last = type(e).__name__
            time.sleep(min(12.0, 1.0 * (2**attempt)))
    return None, last


def extract_json(raw: str):
    """Parse JSON from raw HTTP or jina markdown wrappers.

    Prefer objects (Gecko `{data:[...]}`) over bare arrays so we do not
    strip the envelope when both patterns match.
    """
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    content = raw
    # jina sometimes wraps as {"data":{"content":"..."}}
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
    # Drop markdown fences / Title headers; keep JSON body
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[-1]
    content = re.sub(r"^```\w*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content)
    # Prefer object-with-data, then any object, then array
    candidates = []
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, content)
        if m:
            candidates.append(m.group(0))
    for blob in candidates:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed
        if isinstance(parsed, (dict, list)):
            # keep looking for better; remember first
            if "prefer" not in locals():
                prefer = parsed
    return locals().get("prefer")


def jina_get_json(api_url: str, timeout: float = 40.0):
    """Fetch via r.jina.ai relay (box Dex/Gecko often 429)."""
    url = api_url
    if not url.startswith("http"):
        url = "http://" + url
    # strip scheme for jina http:// form used elsewhere in repo
    if url.startswith("https://"):
        relay = JINA + url[len("https://") :]
    elif url.startswith("http://"):
        relay = JINA + url[len("http://") :]
    else:
        relay = JINA + url
    raw, err = http_get(relay, timeout=timeout)
    if raw is None:
        return None, err or "empty"
    data = extract_json(raw)
    if data is None:
        return None, "parse_fail"
    return data, None


class RpcClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._backoff = 0.4
        self.calls = 0
        self.errors_429 = 0

    def _post(self, payload: bytes, label: str) -> object:
        last_err: Exception | None = None
        for attempt in range(7):
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
                wait = min(22.0, self._backoff * (2**attempt))
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
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
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


def load_jsonl_addrs(path: Path, key: str = "address") -> set[str]:
    out: set[str] = set()
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            a = norm_addr(o.get(key) if isinstance(o, dict) else None)
            if a:
                out.add(a)
            # also absorb nested wallets lists
            if isinstance(o, dict):
                for fld in ("wallets", "addresses", "addrs"):
                    for w in o.get(fld) or []:
                        if isinstance(w, str):
                            aw = norm_addr(w)
                            if aw:
                                out.add(aw)
                        elif isinstance(w, dict):
                            aw = norm_addr(w.get("address"))
                            if aw:
                                out.add(aw)
    return out


def load_known_set() -> set[str]:
    known: set[str] = set(SKIP_ADDRS)
    for p in KNOWN_SEED_GLOBS:
        known |= load_jsonl_addrs(p)
    # scout resolve / posts may contain wallet fields
    raw_dir = ROOT / "rh-wallets" / "raw"
    if raw_dir.exists():
        for name in (
            "scout_tg_hits.jsonl",
            "scout_tg_resolve_cache.jsonl",
            "pruned_wallets.jsonl",
        ):
            known |= load_jsonl_addrs(raw_dir / name)
    # arc early/profit dumps
    arc_raw = ROOT / "arc-wallets" / "raw"
    if arc_raw.exists():
        for p in arc_raw.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = data if isinstance(data, list) else (data.get("wallets") or data.get("addresses") or [])
            if isinstance(data, dict) and not rows:
                # dict of addr->meta
                for k, v in data.items():
                    a = norm_addr(k)
                    if a:
                        known.add(a)
                    if isinstance(v, dict):
                        a2 = norm_addr(v.get("address"))
                        if a2:
                            known.add(a2)
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, str):
                    a = norm_addr(row)
                    if a:
                        known.add(a)
                elif isinstance(row, dict):
                    a = norm_addr(row.get("address") or row.get("wallet"))
                    if a:
                        known.add(a)
    return known


def _token_id_to_ca(token_id: str) -> str | None:
    """gecko id like robinhood_0xabc... → ca."""
    if not isinstance(token_id, str):
        return None
    tid = token_id.lower()
    if "_" in tid:
        tid = tid.split("_", 1)[-1]
    return norm_addr(tid)


def fetch_gecko_trending() -> list[dict]:
    out: list[dict] = []
    for page in (1, 2):
        data, err = jina_get_json(
            f"https://api.geckoterminal.com/api/v2/networks/robinhood/trending_pools?page={page}"
        )
        if err or not isinstance(data, dict):
            log(f"gecko trending page={page} err={err}")
            break
        rows = data.get("data") or []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            attrs = row.get("attributes") or {}
            rel = row.get("relationships") or {}
            ca = _token_id_to_ca(((rel.get("base_token") or {}).get("data") or {}).get("id"))
            if not ca or ca in SKIP_TOKENS:
                continue
            pool = (attrs.get("address") or "").lower() or None
            if not pool and isinstance(row.get("id"), str) and "_" in row["id"]:
                pool = row["id"].split("_", 1)[-1].lower()
            name = attrs.get("name") or ""
            sym = name.split("/")[0].strip() if "/" in name else name
            vol = _f((attrs.get("volume_usd") or {}).get("h24")) or 0.0
            chg = _f((attrs.get("price_change_percentage") or {}).get("h24")) or 0.0
            out.append(
                {
                    "ca": ca,
                    "symbol": sym,
                    "pool": pool,
                    "vol_h24": vol,
                    "chg_h24": chg,
                    "created_at": attrs.get("pool_created_at"),
                    "sources": ["gecko_trending"],
                    "hot": vol / 1e5 + max(chg, 0) / 10.0,
                }
            )
        time.sleep(0.6)
    return out


def fetch_dex_rh_tokens() -> list[dict]:
    out: list[dict] = []
    for endpoint, src in (
        ("https://api.dexscreener.com/token-boosts/top/v1", "dex_boosts"),
        ("https://api.dexscreener.com/token-profiles/latest/v1", "dex_profiles"),
    ):
        data, err = jina_get_json(endpoint)
        if err:
            log(f"{src} err={err}")
            continue
        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            chain = (row.get("chainId") or "").lower()
            if chain not in ("robinhood", "rh"):
                continue
            ca = norm_addr(row.get("tokenAddress") or row.get("token_address"))
            if not ca or ca in SKIP_TOKENS:
                continue
            out.append(
                {
                    "ca": ca,
                    "symbol": row.get("description") or row.get("url") or ca[:10],
                    "pool": None,
                    "vol_h24": 0.0,
                    "chg_h24": 0.0,
                    "created_at": None,
                    "sources": [src],
                    "hot": 5.0 if src == "dex_boosts" else 3.0,
                }
            )
        time.sleep(0.5)
    return out


def fetch_peak_seeds() -> list[dict]:
    path = ROOT / "artifacts" / "notify_peak_2x.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"peak seed load fail: {e}")
        return []
    toks = data.get("tokens_ge2x_peak") or []
    out: list[dict] = []
    for t in toks:
        if not isinstance(t, dict):
            continue
        ca = norm_addr(t.get("ca"))
        if not ca or ca in SKIP_TOKENS:
            continue
        mp = _f(t.get("mult_peak")) or 0.0
        if mp < 2.0:
            continue
        out.append(
            {
                "ca": ca,
                "symbol": t.get("symbol") or ca[:10],
                "pool": None,
                "vol_h24": 0.0,
                "chg_h24": 0.0,
                "created_at": None,
                "sources": ["notify_peak_2x"],
                "hot": 8.0 + min(mp, 20.0),
                "mult_peak": mp,
            }
        )
    return out


def merge_cas(rows: list[dict], cap: int) -> list[dict]:
    by: dict[str, dict] = {}
    for r in rows:
        ca = r["ca"]
        if ca not in by:
            by[ca] = dict(r)
            by[ca]["sources"] = list(r.get("sources") or [])
            continue
        cur = by[ca]
        cur["hot"] = max(float(cur.get("hot") or 0), float(r.get("hot") or 0))
        cur["vol_h24"] = max(float(cur.get("vol_h24") or 0), float(r.get("vol_h24") or 0))
        a, b = float(cur.get("chg_h24") or 0), float(r.get("chg_h24") or 0)
        cur["chg_h24"] = b if abs(b) > abs(a) else a
        for s in r.get("sources") or []:
            if s not in cur["sources"]:
                cur["sources"].append(s)
        if not cur.get("pool") and r.get("pool"):
            cur["pool"] = r["pool"]
        if r.get("mult_peak"):
            cur["mult_peak"] = max(float(cur.get("mult_peak") or 0), float(r["mult_peak"]))
        if r.get("symbol") and (not cur.get("symbol") or len(str(r["symbol"])) < 40):
            if not cur.get("symbol") or str(cur["symbol"]).startswith("0x"):
                cur["symbol"] = r["symbol"]
    merged = sorted(by.values(), key=lambda x: float(x.get("hot") or 0), reverse=True)
    # Diversity: keep live trend/boost CAs from being crowded out by peak seeds
    live, peak, other = [], [], []
    for r in merged:
        srcs = set(r.get("sources") or [])
        if srcs & {"gecko_trending", "dex_boosts", "dex_profiles"}:
            live.append(r)
        elif "notify_peak_2x" in srcs:
            peak.append(r)
        else:
            other.append(r)
    live_slots = max(cap // 2, min(len(live), cap))
    out = live[:live_slots]
    for bucket in (peak, other, live[live_slots:]):
        for r in bucket:
            if len(out) >= cap:
                break
            if r not in out:
                out.append(r)
        if len(out) >= cap:
            break
    return out[: max(5, cap)]


def resolve_pool_for_ca(ca: str) -> str | None:
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/{ca}/pools?page=1"
    )
    if err or not isinstance(data, dict):
        log(f"pools for {ca[:10]}… err={err}")
        return None
    rows = data.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None
    ca_l = ca.lower()
    scored = []
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
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def fetch_pool_buyers(pool: str, ca: str) -> list[dict]:
    """Recent buyers from gecko pool trades (kind=buy)."""
    data, err = jina_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/robinhood/pools/{pool}/trades"
    )
    if err or not isinstance(data, dict):
        log(f"trades pool={pool[:12]}… err={err}")
        return []
    rows = data.get("data") or []
    buyers: list[dict] = []
    ca_l = ca.lower()
    for row in rows if isinstance(rows, list) else []:
        attrs = row.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        kind = (attrs.get("kind") or "").lower()
        # buy = acquiring base; also accept when to_token is our CA
        to_tok = (attrs.get("to_token_address") or "").lower()
        from_tok = (attrs.get("from_token_address") or "").lower()
        is_buy = kind == "buy" or to_tok == ca_l
        if not is_buy:
            # if selling our token, skip
            if from_tok == ca_l:
                continue
            if kind and kind != "buy":
                continue
        addr = norm_addr(attrs.get("tx_from_address") or attrs.get("from_address") or attrs.get("maker"))
        if not addr or addr in SKIP_ADDRS:
            continue
        vol = _f(attrs.get("volume_in_usd")) or 0.0
        buyers.append(
            {
                "address": addr,
                "vol_usd": vol,
                "ts": attrs.get("block_timestamp"),
                "block": attrs.get("block_number"),
                "tx": attrs.get("tx_hash"),
                "source": "gecko_pool_trades",
            }
        )
    return buyers


def fetch_rpc_transfer_buyers(rpc: RpcClient, ca: str, blocks: int) -> list[dict]:
    """Transfer `to` receivers over recent window (chunked getLogs)."""
    try:
        head_hex = rpc.call("eth_blockNumber", [])
        head = int(head_hex, 16)
    except Exception as e:
        log(f"rpc head fail: {e}")
        return []
    start = max(0, head - max(100, blocks) + 1)
    chunk = 400
    buyers: list[dict] = []
    bn = start
    while bn <= head:
        end = min(head, bn + chunk - 1)
        try:
            logs = rpc.call(
                "eth_getLogs",
                [
                    {
                        "address": ca,
                        "fromBlock": hex(bn),
                        "toBlock": hex(end),
                        "topics": [TRANSFER_TOPIC],
                    }
                ],
            )
        except Exception as e:
            log(f"getLogs {ca[:10]}… {bn}-{end} err={type(e).__name__}:{e}")
            time.sleep(1.5)
            # shrink chunk on failure
            if chunk > 100:
                chunk = max(100, chunk // 2)
                continue
            bn = end + 1
            continue
        time.sleep(0.15)
        for lg in logs or []:
            if not isinstance(lg, dict):
                continue
            topics = lg.get("topics") or []
            if len(topics) < 3:
                continue
            to = topic_addr(topics[2])
            frm = topic_addr(topics[1])
            if to in SKIP_ADDRS or to == ca or to == frm:
                continue
            # skip obvious contract hubs later via frequency
            buyers.append(
                {
                    "address": to,
                    "vol_usd": 0.0,
                    "ts": None,
                    "block": int(lg.get("blockNumber") or "0x0", 16),
                    "tx": lg.get("transactionHash"),
                    "source": "rpc_transfer",
                    "from": frm,
                }
            )
        bn = end + 1
    return buyers


def score_candidate(row: dict, now: datetime) -> float:
    n_cas = int(row.get("n_cas") or 0)
    n_buys = int(row.get("n_buys") or 0)
    vol = float(row.get("sum_vol_usd") or 0)
    # recency: last_ts within hours
    recent = 0.0
    last_ts = row.get("last_seen")
    if isinstance(last_ts, str) and last_ts:
        try:
            t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            age_h = (now - t).total_seconds() / 3600.0
            if age_h <= 6:
                recent = 12.0
            elif age_h <= 24:
                recent = 8.0
            elif age_h <= 72:
                recent = 4.0
            else:
                recent = 1.0
        except Exception:
            recent = 2.0
    elif row.get("last_block"):
        recent = 5.0
    # multi-CA is the strongest "smart active" signal
    multi = n_cas * 14.0 if n_cas >= 2 else (6.0 if n_cas == 1 else 0.0)
    # early-rank style: many buys on one strong CA
    depth = min(n_buys, 20) * 0.6
    # non-dust
    dust_pen = 4.0 if vol > 0 and vol < 5 and n_cas < 2 else 0.0
    vol_term = min(vol, 5000) / 200.0
    peak_boost = 3.0 if row.get("has_peak_ca") else 0.0
    trend_boost = 2.0 * min(int(row.get("n_trend_cas") or 0), 5)
    return multi + depth + recent + vol_term + peak_boost + trend_boost - dust_pen


def hunt_once() -> dict:
    t0 = time.time()
    ca_cap = max(10, min(50, env_int("TREND_CA_CAP", 30)))
    top_watch = max(5, min(100, env_int("TREND_TOP_WATCH", 25)))
    min_score = env_float("TREND_MIN_SCORE", 8.0)
    log_blocks = max(100, min(5000, env_int("TREND_LOG_BLOCKS", 800)))
    use_rpc = os.environ.get("TREND_USE_RPC_LOGS", "1").strip() not in ("0", "false", "no")

    log("loading known seeds…")
    known = load_known_set()
    log(f"known_addrs={len(known)}")

    log("fetching trending CAs (jina)…")
    rows: list[dict] = []
    rows.extend(fetch_gecko_trending())
    rows.extend(fetch_dex_rh_tokens())
    rows.extend(fetch_peak_seeds())
    cas = merge_cas(rows, ca_cap)
    log(f"trending_cas={len(cas)} (cap={ca_cap})")
    for i, c in enumerate(cas[:12]):
        log(
            f"  CA#{i+1} {c.get('symbol')} {c['ca'][:12]}… "
            f"hot={c.get('hot'):.1f} src={','.join(c.get('sources') or [])} "
            f"pool={'yes' if c.get('pool') else 'no'}"
        )

    # wallet accumulators
    by_wallet: dict[str, dict] = {}
    ca_buyer_count: dict[str, int] = defaultdict(int)
    rpc = RpcClient(RPC_URL) if use_rpc else None
    hub_counts: dict[str, int] = defaultdict(int)  # addresses that receive too often ≈ LP/router

    for i, meta in enumerate(cas):
        ca = meta["ca"]
        pool = meta.get("pool")
        log(f"[{i+1}/{len(cas)}] buyers for {meta.get('symbol')} {ca[:12]}…")
        buyers: list[dict] = []
        if not pool:
            pool = resolve_pool_for_ca(ca)
            meta["pool"] = pool
            time.sleep(0.5)
        if pool:
            buyers.extend(fetch_pool_buyers(pool, ca))
            time.sleep(0.55)
        # RPC supplement when gecko thin or always a light recent window
        if rpc is not None and len(buyers) < 8:
            try:
                buyers.extend(fetch_rpc_transfer_buyers(rpc, ca, min(log_blocks, 500)))
            except Exception as e:
                log(f"rpc buyers fail: {e}")

        # rank early: first appearances in list order (gecko is recent-desc usually)
        # reverse so earliest in this batch get better early_rank if from rpc ascending
        seen_local: dict[str, int] = {}
        for b in buyers:
            addr = b["address"]
            hub_counts[addr] += 1
            if addr in known or addr in SKIP_ADDRS:
                continue
            if addr not in seen_local:
                seen_local[addr] = len(seen_local) + 1
            w = by_wallet.get(addr)
            if not w:
                w = {
                    "address": addr,
                    "cas": [],
                    "ca_meta": {},
                    "n_buys": 0,
                    "sum_vol_usd": 0.0,
                    "sources": set(),
                    "first_seen": None,
                    "last_seen": None,
                    "last_block": 0,
                    "first_seen_cas": None,
                    "early_ranks": [],
                }
                by_wallet[addr] = w
            w["n_buys"] += 1
            w["sum_vol_usd"] += float(b.get("vol_usd") or 0)
            w["sources"].add(b.get("source") or "unknown")
            if ca not in w["cas"]:
                w["cas"].append(ca)
                if w["first_seen_cas"] is None:
                    w["first_seen_cas"] = ca
                w["ca_meta"][ca] = {
                    "symbol": meta.get("symbol"),
                    "sources": list(meta.get("sources") or []),
                    "mult_peak": meta.get("mult_peak"),
                }
                ca_buyer_count[ca] += 1
                w["early_ranks"].append(seen_local[addr])
            ts = b.get("ts")
            if isinstance(ts, str):
                if w["first_seen"] is None or ts < w["first_seen"]:
                    w["first_seen"] = ts
                if w["last_seen"] is None or ts > w["last_seen"]:
                    w["last_seen"] = ts
            blk = b.get("block")
            if isinstance(blk, int) and blk > (w["last_block"] or 0):
                w["last_block"] = blk

        log(f"  buyers_raw={len(buyers)} unique_new_on_ca={len(seen_local)} wallet_pool={len(by_wallet)}")

    # Drop LP/router hubs: appear as Transfer `to` extremely often across scan
    hub_thresh = max(40, len(cas) * 8)
    hubs = {a for a, n in hub_counts.items() if n >= hub_thresh}
    if hubs:
        log(f"dropping hub/router-like addrs={len(hubs)} thresh={hub_thresh}")

    now = datetime.now(timezone.utc)
    candidates: list[dict] = []
    for addr, w in by_wallet.items():
        if addr in hubs or addr in known:
            continue
        # require at least some signal
        n_cas = len(w["cas"])
        if n_cas < 1:
            continue
        # prefer ≥2 CAs OR strong single (many buys / vol / early rank)
        best_early = min(w["early_ranks"]) if w["early_ranks"] else 999
        strong_single = (
            n_cas == 1
            and (
                w["n_buys"] >= 3
                or w["sum_vol_usd"] >= 50
                or best_early <= 15
            )
        )
        if n_cas < 2 and not strong_single:
            continue
        # Aggregator / router-like: hits too many of this run's CAs
        if n_cas >= max(8, int(len(cas) * 0.4)):
            continue
        peak_cas = [c for c, m in w["ca_meta"].items() if (m or {}).get("mult_peak")]
        trend_cas = [
            c
            for c, m in w["ca_meta"].items()
            if any(s.startswith("gecko_") or s.startswith("dex_") for s in (m or {}).get("sources") or [])
        ]
        row = {
            "address": addr,
            "score": 0.0,
            "n_cas": n_cas,
            "cas": w["cas"],
            "first_seen_cas": w["first_seen_cas"],
            "symbols": [((w["ca_meta"].get(c) or {}).get("symbol") or c[:8]) for c in w["cas"]],
            "n_buys": w["n_buys"],
            "sum_vol_usd": round(w["sum_vol_usd"], 4),
            "sources": sorted(w["sources"]),
            "first_seen": w["first_seen"],
            "last_seen": w["last_seen"],
            "last_block": w["last_block"] or None,
            "best_early_rank": best_early if best_early < 999 else None,
            "has_peak_ca": bool(peak_cas),
            "n_trend_cas": len(trend_cas),
            "discovered_at": now.isoformat().replace("+00:00", "Z"),
            "chain": "robinhood",
            "tags": ["unknown_trend_smart"],
        }
        row["score"] = round(score_candidate(row, now), 3)
        if row["score"] < min_score and n_cas < 2:
            continue
        candidates.append(row)

    candidates.sort(key=lambda r: (r["score"], r["n_cas"], r["n_buys"]), reverse=True)

    # Full dump for audit; published list = multi-CA or high single score
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw_all = STATE_PATH.parent / "unknown_trend_smart_all.jsonl"
    with raw_all.open("w", encoding="utf-8") as f:
        for r in candidates:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    published = []
    for r in candidates:
        n_cas = int(r.get("n_cas") or 0)
        if n_cas >= 2:
            published.append(r)
        elif (
            (r.get("best_early_rank") or 999) <= 8
            and float(r.get("sum_vol_usd") or 0) >= 25
            and int(r.get("n_buys") or 0) >= 3
        ):
            published.append(r)

    # write outputs
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in published:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    candidates_all = candidates
    candidates = published

    watch = [r for r in candidates if r["n_cas"] >= 2 and r["score"] >= max(min_score, 12.0)][:top_watch]
    with OUT_CAND.open("w", encoding="utf-8") as f:
        for r in watch:
            f.write(
                json.dumps(
                    {
                        "address": r["address"],
                        "score": r["score"],
                        "n_cas": r["n_cas"],
                        "cas": r["cas"],
                        "symbols": r["symbols"],
                        "sources": r["sources"],
                        "discovered_at": r["discovered_at"],
                        "tags": ["watch_candidate_unknown", "unknown_trend_smart"],
                        "note": "NOT auto-merged into wallets.jsonl — quality gate required",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    jst_now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        f"# Unknown trend smart wallets — {jst_now}",
        "",
        f"- Trending/seed CAs scanned: **{len(cas)}**",
        f"- Known excluded: **{len(known)}**",
        f"- Unknown candidates (published): **{len(candidates)}** (full dump {len(candidates_all)} in raw/)",
        f"- Watch candidates (top gate): **{len(watch)}** (not auto-merged)",
        f"- RPC calls: **{rpc.calls if rpc else 0}** (429s={rpc.errors_429 if rpc else 0})",
        f"- Elapsed: **{time.time() - t0:.1f}s**",
        "",
        "## CA sources (top)",
        "",
    ]
    for c in cas[:20]:
        lines.append(
            f"- `{c.get('symbol')}` `{c['ca']}` hot={float(c.get('hot') or 0):.1f} "
            f"src={','.join(c.get('sources') or [])}"
        )
    lines += ["", "## Top 10 unknown addresses", ""]
    for i, r in enumerate(candidates[:10]):
        lines.append(
            f"{i+1}. `{r['address']}` score={r['score']} n_cas={r['n_cas']} "
            f"buys={r['n_buys']} vol=${r['sum_vol_usd']:.1f} "
            f"syms={','.join(r.get('symbols') or [])[:60]}"
        )
    if not candidates:
        lines.append("_No unknown candidates passed filters this run._")
    lines += [
        "",
        "## Notes",
        "",
        "- Buyers from Gecko pool trades (`kind=buy`) via jina; optional RH RPC Transfer logs.",
        "- Excludes all rh-wallets / fomo / arc / scout seed addresses.",
        "- `watch_candidates_unknown.jsonl` is a staging list — do **not** dump into `wallets.jsonl` without a quality gate.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = {
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "n_cas": len(cas),
        "n_known": len(known),
        "n_candidates": len(candidates),
        "n_watch": len(watch),
        "top": [{"address": r["address"], "score": r["score"], "n_cas": r["n_cas"]} for r in candidates[:10]],
        "cas": [{"ca": c["ca"], "symbol": c.get("symbol"), "sources": c.get("sources")} for c in cas],
        "elapsed_s": round(time.time() - t0, 2),
        "rpc_calls": rpc.calls if rpc else 0,
        "rpc_429": rpc.errors_429 if rpc else 0,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(
        f"done candidates={len(candidates)} watch={len(watch)} "
        f"cas={len(cas)} elapsed={time.time()-t0:.1f}s"
    )
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt unknown smart wallets from trending RH tokens")
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    ap.add_argument("--daemon", action="store_true", help="loop")
    ap.add_argument("--interval", type=int, default=0, help="daemon interval sec")
    args = ap.parse_args()
    daemon = args.daemon or os.environ.get("TREND_DAEMON", "").strip() in ("1", "true", "yes")
    if os.environ.get("TREND_ONCE", "").strip() in ("1", "true", "yes"):
        daemon = False
    interval = args.interval or env_int("TREND_INTERVAL_SEC", 900)

    if not daemon:
        hunt_once()
        return 0
    log(f"daemon interval={interval}s")
    while True:
        try:
            hunt_once()
        except Exception as e:
            log(f"hunt_once FAIL {type(e).__name__}: {e}")
        time.sleep(max(60, interval))


if __name__ == "__main__":
    sys.exit(main())

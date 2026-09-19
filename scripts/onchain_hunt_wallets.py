#!/usr/bin/env python3
"""RH on-chain hot-wallet hunter — tip-follow recent blocks (no GMGN / no LIVE).

Walks eth_getBlockByNumber(full txs) over a recent window, scores wallets that
are actively trading NOW (frequent contract calls + ERC-20 Transfer receives),
boosts overlap with known-good CAs / existing watch, and writes:

  rh-wallets/onchain_hot_active.jsonl
  rh-wallets/summary_onchain_hot.md

Env knobs:
  RH_RPC_URL              default https://rpc.mainnet.chain.robinhood.com
  HUNT_BLOCKS             window size (default 1000; clamp 100–3000)
  HUNT_MIN_TXS            min txs in window to consider (default 2)
  HUNT_MIN_TOKENS         min unique recv tokens for "hot" (default 1; prefer ≥2)
  HUNT_RECEIPT_TOP        top candidates to receipt-check (default 120)
  HUNT_RECEIPT_PER_ADDR   max receipts per addr (default 12)
  HUNT_RECEIPT_BUDGET     global receipt cap (default 900)
  HUNT_CONCURRENCY        1–2 block fetch workers (default 1)
  HUNT_SLEEP_MS           base pause between RPC after success (default 25)
  HUNT_DAEMON=1           loop; HUNT_INTERVAL_SEC (default 600)
  HUNT_TICK_ONCE=1        one pass then exit (default when not daemon)
  LIVE_TRADING stays off — never touched for trading.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
JST = timezone(timedelta(hours=9))

SKIP_TOKENS = {
    ZERO,
    "0x4200000000000000000000000000000000000006",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
}

# Known system / infrastructure senders on RH (expand as needed)
SKIP_FROM = {
    ZERO,
    "0x00000000000000000000000000000000000a4b05",
}

RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)

OUT_JSONL = ROOT / "rh-wallets" / "onchain_hot_active.jsonl"
OUT_MD = ROOT / "rh-wallets" / "summary_onchain_hot.md"
STATE_PATH = ROOT / "rh-wallets" / "raw" / "onchain_hunt_state.json"


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
    print(f"{ts} hunt {msg}", flush=True)


class RpcClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._backoff = 0.3
        self.calls = 0
        self.errors_429 = 0

    def _post(self, payload: bytes, label: str) -> object:
        last_err: Exception | None = None
        for attempt in range(8):
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
                self._backoff = max(0.2, self._backoff * 0.88)
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                wait = min(25.0, self._backoff * (2 ** attempt))
                if e.code in (429, 403, 502, 503, 504):
                    self.errors_429 += 1
                    ra = e.headers.get("Retry-After")
                    try:
                        wait = max(wait, float(ra))
                    except (TypeError, ValueError):
                        pass
                    log(f"rpc HTTP {e.code} {label} sleep={wait:.1f}s")
                    time.sleep(wait)
                    self._backoff = min(8.0, self._backoff * 1.5)
                    continue
                time.sleep(wait)
            except Exception as e:
                last_err = e
                wait = min(15.0, self._backoff * (2 ** attempt))
                log(f"rpc err {label} {type(e).__name__} sleep={wait:.1f}s")
                time.sleep(wait)
        raise RuntimeError(f"rpc fail {label}: {last_err}")

    def call(self, method: str, params: list) -> object:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        body = self._post(payload, method)
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            msg = str(err.get("message") or err)
            if "rate" in msg.lower() or "429" in msg:
                self.errors_429 += 1
                time.sleep(min(10.0, self._backoff * 2))
                self._backoff = min(8.0, self._backoff * 1.4)
                return self.call(method, params)
            raise RuntimeError(msg)
        return body.get("result") if isinstance(body, dict) else None


def topic_addr(topic: str) -> str:
    t = (topic or "").lower()
    if t.startswith("0x"):
        t = t[2:]
    return "0x" + t[-40:]


def decode_uint(data: str) -> int:
    d = (data or "0x").lower()
    if d.startswith("0x"):
        d = d[2:]
    if not d:
        return 0
    try:
        return int(d, 16)
    except ValueError:
        return 0


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
            a = (o.get(key) or "").lower()
            if a.startswith("0x") and len(a) == 42:
                out.add(a)
    return out


def load_known_good_cas() -> set[str]:
    cas: set[str] = set()
    # xbtscout early-call CAs (robinhood)
    cas_path = ROOT / "xbtscout" / "cas.jsonl"
    if cas_path.exists():
        with cas_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ca = (o.get("ca") or "").lower()
                chain = (o.get("chain_guess") or "robinhood").lower()
                if chain and chain not in ("robinhood", "rh", ""):
                    continue
                if ca.startswith("0x") and len(ca) == 42:
                    cas.add(ca)
    # scout ranked token CAs
    scout = ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl"
    if scout.exists():
        with scout.open(encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for fld in ("scout_token_cas", "token_cas"):
                    for c in o.get(fld) or []:
                        if isinstance(c, str):
                            cl = c.lower()
                            if cl.startswith("0x") and len(cl) == 42:
                                cas.add(cl)
                tc = (o.get("token_ca") or "").lower()
                if tc.startswith("0x") and len(tc) == 42:
                    cas.add(tc)
    cas -= SKIP_TOKENS
    cas.discard(ZERO)
    return cas


def load_watch_set() -> set[str]:
    paths = [
        ROOT / "rh-wallets" / "wallets.jsonl",
        ROOT / "rh-wallets" / "audit_active_quality_plus.jsonl",
        ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl",
    ]
    watch: set[str] = set()
    for p in paths:
        watch |= load_jsonl_addrs(p)
    return watch


def parse_buys_from_receipt(tx: dict, receipt: dict) -> tuple[set[str], bool]:
    """Return (tokens_received_by_from, swap_shaped)."""
    fr = (tx.get("from") or "").lower()
    if not fr or fr == ZERO:
        return set(), False
    if str(receipt.get("status") or "0x1").lower() in ("0x0", "0"):
        return set(), False
    received: set[str] = set()
    sent: set[str] = set()
    for lg in receipt.get("logs") or []:
        topics = lg.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue
        if len(topics) < 3:
            continue
        token = (lg.get("address") or "").lower()
        if not token.startswith("0x") or len(token) != 42:
            continue
        frm = topic_addr(topics[1])
        to = topic_addr(topics[2])
        if to == fr and token not in SKIP_TOKENS and token != fr:
            received.add(token)
        if frm == fr and token not in SKIP_TOKENS:
            sent.add(token)
    input_data = tx.get("input") or "0x"
    has_calldata = isinstance(input_data, str) and len(input_data) > 10
    sent_other = bool(sent - received) or (bool(sent) and bool(received))
    swap_shaped = bool(received) and (sent_other or has_calldata)
    return received, swap_shaped


def is_spam_bot(row: dict) -> bool:
    """Single-target high-freq callers without token receives ≈ infra/MEV spam."""
    txs = int(row.get("tx_count") or 0)
    contracts = int(row.get("unique_contracts") or 0)
    toks = int(row.get("unique_tokens") or 0)
    if toks >= 1:
        return False
    if contracts <= 1 and txs >= 40:
        return True
    if contracts <= 2 and txs >= 120 and int(row.get("buy_shaped_txs") or 0) == 0:
        return True
    return False


def score_wallet(row: dict) -> float:
    txs = int(row.get("tx_count") or 0)
    toks = int(row.get("unique_tokens") or 0)
    overlap = int(row.get("good_ca_overlap") or 0)
    in_watch = 1 if row.get("in_watch") else 0
    contracts = int(row.get("unique_contracts") or 0)
    buy_n = int(row.get("buy_shaped_txs") or 0)
    # Prefer multi-buy active traders over one-shot / spam bots
    oneshot_penalty = 8.0 if toks <= 1 and txs <= 2 else 0.0
    spam_penalty = 200.0 if is_spam_bot(row) else 0.0
    # Cap raw tx weight so 1000-tx spam does not drown real traders
    tx_term = min(txs, 40) * 2.0 + min(max(txs - 40, 0), 60) * 0.25
    return (
        tx_term
        + toks * 8.0
        + overlap * 12.0
        + in_watch * 14.0
        + min(contracts, 10) * 2.0
        + buy_n * 5.0
        - oneshot_penalty
        - spam_penalty
    )


def hunt_once(rpc: RpcClient) -> dict:
    t0 = time.time()
    blocks_n = max(100, min(3000, env_int("HUNT_BLOCKS", 1000)))
    min_txs = max(1, env_int("HUNT_MIN_TXS", 2))
    min_tokens = max(0, env_int("HUNT_MIN_TOKENS", 1))
    receipt_top = max(20, env_int("HUNT_RECEIPT_TOP", 120))
    receipt_per = max(3, env_int("HUNT_RECEIPT_PER_ADDR", 12))
    receipt_budget = max(50, env_int("HUNT_RECEIPT_BUDGET", 900))
    concurrency = max(1, min(2, env_int("HUNT_CONCURRENCY", 1)))
    sleep_ms = max(0, env_int("HUNT_SLEEP_MS", 25))

    log("loading watch + known good CAs…")
    watch = load_watch_set()
    good_cas = load_known_good_cas()
    log(f"watch_addrs={len(watch)} known_good_cas={len(good_cas)}")

    head_hex = rpc.call("eth_blockNumber", [])
    head = int(head_hex, 16)
    start = max(0, head - blocks_n + 1)
    log(f"scan blocks {start}..{head} (n={head - start + 1}) rpc={RPC_URL} conc={concurrency}")

    # per-addr accumulators
    tx_count: dict[str, int] = defaultdict(int)
    unique_to: dict[str, set[str]] = defaultdict(set)
    last_block: dict[str, int] = {}
    first_block: dict[str, int] = {}
    calldata_n: dict[str, int] = defaultdict(int)
    tx_hashes: dict[str, list[tuple[str, dict]]] = defaultdict(list)  # (hash, tx) capped
    # frequent contract targets (= likely routers)
    to_count: dict[str, int] = defaultdict(int)
    total_txs = 0
    scanned = 0
    empty_blocks = 0

    def fetch_block(bn: int):
        return bn, rpc.call("eth_getBlockByNumber", [hex(bn), True])

    bn = start
    while bn <= head:
        # tip skip-ahead: if we fall far behind a moving tip mid-scan, still finish window
        chunk_end = min(head, bn + concurrency - 1)
        chunk = list(range(bn, chunk_end + 1))
        results: list[tuple[int, object]] = []
        if concurrency == 1:
            for x in chunk:
                results.append(fetch_block(x))
                if sleep_ms:
                    time.sleep(sleep_ms / 1000.0)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(fetch_block, x) for x in chunk]
                for fut in as_completed(futs):
                    results.append(fut.result())
            results.sort(key=lambda r: r[0])
            if sleep_ms:
                time.sleep(sleep_ms / 1000.0)

        for block_num, blk in results:
            scanned += 1
            if not isinstance(blk, dict):
                empty_blocks += 1
                continue
            txs = blk.get("transactions") or []
            if not txs:
                empty_blocks += 1
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                total_txs += 1
                fr = (tx.get("from") or "").lower()
                to = (tx.get("to") or "").lower()
                if not fr or fr in SKIP_FROM or len(fr) != 42:
                    continue
                input_data = tx.get("input") or "0x"
                has_calldata = isinstance(input_data, str) and len(input_data) > 10
                # Count all senders; prefer contract callers
                if to:
                    to_count[to] += 1
                if not has_calldata and (not to):
                    continue  # plain ETH transfer / create — weak signal
                if not has_calldata and to:
                    # value transfer to contract without calldata — still count lightly
                    pass
                tx_count[fr] += 1
                if to:
                    unique_to[fr].add(to)
                if has_calldata:
                    calldata_n[fr] += 1
                if fr not in first_block:
                    first_block[fr] = block_num
                last_block[fr] = block_num
                if len(tx_hashes[fr]) < receipt_per:
                    tx_hashes[fr].append((tx.get("hash") or "", tx))

        bn = chunk[-1] + 1
        if scanned % 100 == 0 or bn > head:
            elapsed = time.time() - t0
            rate = scanned / elapsed if elapsed > 0 else 0
            log(
                f"progress scanned={scanned}/{head - start + 1} "
                f"senders={len(tx_count)} txs={total_txs} "
                f"rate={rate:.1f} blk/s 429={rpc.errors_429}"
            )

    # Likely routers / hubs: high inbound tx count
    routers = {a for a, n in to_count.items() if n >= max(8, blocks_n // 80)}
    # Drop addresses that look like contracts (also popular as `to`)
    contractish = {a for a, n in to_count.items() if n >= 5}

    # Candidate shortlist by tx activity
    candidates = []
    for addr, n in tx_count.items():
        if n < min_txs:
            continue
        if addr in contractish and calldata_n.get(addr, 0) == 0:
            continue
        router_hits = len(unique_to[addr] & routers)
        candidates.append(
            {
                "address": addr,
                "tx_count": n,
                "unique_contracts": len(unique_to[addr]),
                "calldata_txs": calldata_n.get(addr, 0),
                "router_hits": router_hits,
                "first_block": first_block.get(addr, 0),
                "last_block": last_block.get(addr, 0),
                "in_watch": addr in watch,
                "tx_hashes": tx_hashes.get(addr, []),
            }
        )
    def shortlist_key(r: dict) -> tuple:
        # Deprioritize single-contract spam for expensive receipt checks
        spam = 1 if (r["unique_contracts"] <= 1 and r["tx_count"] >= 40) else 0
        diversity = min(r["unique_contracts"], 8) * 4 + min(r["router_hits"], 5) * 3
        activity = min(r["tx_count"], 25) * 2 + min(r["calldata_txs"], 25)
        watch_b = 8 if r["in_watch"] else 0
        return (0 if spam else 1, diversity + activity + watch_b, r["last_block"])

    candidates.sort(key=shortlist_key, reverse=True)
    shortlist = candidates[:receipt_top]
    log(f"candidates≥{min_txs}txs={len(candidates)} receipt_shortlist={len(shortlist)} routers~={len(routers)}")

    # Receipt pass — unique tokens bought
    used_receipts = 0
    for i, row in enumerate(shortlist):
        if used_receipts >= receipt_budget:
            log(f"receipt budget hit at shortlist#{i}")
            break
        tokens: set[str] = set()
        buy_shaped = 0
        for th, tx in row["tx_hashes"]:
            if used_receipts >= receipt_budget:
                break
            if not th:
                continue
            try:
                rc = rpc.call("eth_getTransactionReceipt", [th])
            except Exception as e:
                log(f"receipt fail {th[:12]}… {type(e).__name__}")
                continue
            used_receipts += 1
            if sleep_ms:
                time.sleep(sleep_ms / 1000.0)
            if not isinstance(rc, dict):
                continue
            recv, shaped = parse_buys_from_receipt(tx, rc)
            tokens |= recv
            if shaped:
                buy_shaped += 1
        row["tokens"] = sorted(tokens)
        row["unique_tokens"] = len(tokens)
        row["buy_shaped_txs"] = buy_shaped
        row["good_ca_overlap"] = len(set(tokens) & good_cas)
        row["good_cas"] = sorted(set(tokens) & good_cas)[:12]
        if (i + 1) % 20 == 0:
            log(f"receipts progress {i+1}/{len(shortlist)} used={used_receipts} 429={rpc.errors_429}")

    # Fill missing receipt fields for unscanned shortlist tail / non-shortlist
    for row in candidates:
        row.setdefault("unique_tokens", 0)
        row.setdefault("buy_shaped_txs", 0)
        row.setdefault("good_ca_overlap", 0)
        row.setdefault("tokens", [])
        row.setdefault("good_cas", [])
        row.pop("tx_hashes", None)

    # Hot filter: active + not one-shot preference; drop single-target spam bots
    hot = []
    spam_dropped = 0
    for row in candidates:
        if is_spam_bot(row):
            spam_dropped += 1
            continue
        toks = int(row["unique_tokens"])
        txs = int(row["tx_count"])
        # Prefer receipt-confirmed buys; allow diversified calldata traders
        if toks >= max(2, min_tokens):
            pass
        elif toks >= min_tokens and (txs >= 3 or row["good_ca_overlap"] > 0 or row["in_watch"]):
            pass
        elif (
            toks == 0
            and txs >= max(4, min_txs + 2)
            and int(row["calldata_txs"]) >= 3
            and int(row["unique_contracts"]) >= 2
        ):
            pass
        else:
            continue
        row["score"] = round(score_wallet(row), 2)
        row["spam_bot"] = False
        hot.append(row)

    log(f"spam_bots_dropped={spam_dropped}")
    hot.sort(
        key=lambda r: (
            r["unique_tokens"],
            r["good_ca_overlap"],
            r["buy_shaped_txs"],
            r["score"],
            r["last_block"],
        ),
        reverse=True,
    )

    # Write outputs
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for rank, row in enumerate(hot, 1):
            out = {
                "rank": rank,
                "address": row["address"],
                "score": row["score"],
                "tx_count": row["tx_count"],
                "unique_tokens": row["unique_tokens"],
                "buy_shaped_txs": row["buy_shaped_txs"],
                "unique_contracts": row["unique_contracts"],
                "calldata_txs": row["calldata_txs"],
                "router_hits": row["router_hits"],
                "good_ca_overlap": row["good_ca_overlap"],
                "good_cas": row["good_cas"],
                "tokens": (row.get("tokens") or [])[:20],
                "in_watch": bool(row["in_watch"]),
                "first_block": row["first_block"],
                "last_block": row["last_block"],
                "window_start": start,
                "window_end": head,
                "chain": "robinhood",
                "source": "onchain_hunt",
                "scanned_at": now_utc.isoformat(),
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    top20 = hot[:20]
    lines = [
        "# RH on-chain hot active wallets",
        "",
        f"- Updated: **{now_jst.strftime('%Y-%m-%d %H:%M JST')}**",
        f"- Window: blocks `{start}` … `{head}` (**{scanned}** scanned, tip={head})",
        f"- RPC calls: **{rpc.calls}** (429s={rpc.errors_429})",
        f"- Elapsed: **{time.time() - t0:.1f}s**",
        f"- Total txs in window: **{total_txs}** · unique senders: **{len(tx_count)}**",
        f"- Candidates (≥{min_txs} txs): **{len(candidates)}** · receipt-checked: **{len(shortlist)}** (receipts={used_receipts})",
        f"- Hot wallets: **{len(hot)}** (spam bots dropped: {spam_dropped})",
        f"- Known-good CAs loaded: **{len(good_cas)}** · watch overlap set: **{len(watch)}**",
        f"- In-watch among hot: **{sum(1 for r in hot if r['in_watch'])}** · new discoveries: **{sum(1 for r in hot if not r['in_watch'])}**",
        "",
        "## Top 20 (on-chain metrics)",
        "",
        "| # | address | score | txs | tokens | buys | goodCA | watch | last_block |",
        "|---|---------|------:|----:|-------:|-----:|-------:|:-----:|-----------:|",
    ]
    for i, r in enumerate(top20, 1):
        lines.append(
            f"| {i} | `{r['address']}` | {r['score']:.1f} | {r['tx_count']} | "
            f"{r['unique_tokens']} | {r['buy_shaped_txs']} | {r['good_ca_overlap']} | "
            f"{'Y' if r['in_watch'] else ''} | {r['last_block']} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Derived from live RH tip blocks (eth_getBlockByNumber + selective receipts).",
            "- **Not** a re-filter of `audit_active_quality_plus` — activity is on-chain window only.",
            "- Score favors frequent buys, multi-token, known-good CA overlap, existing watch.",
            "- LIVE_TRADING=off · no box GMGN.",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "head": head,
        "start": start,
        "scanned": scanned,
        "total_txs": total_txs,
        "senders": len(tx_count),
        "candidates": len(candidates),
        "hot": len(hot),
        "receipts": used_receipts,
        "rpc_calls": rpc.calls,
        "errors_429": rpc.errors_429,
        "elapsed_s": round(time.time() - t0, 2),
        "top20": [
            {
                "address": r["address"],
                "score": r["score"],
                "tx_count": r["tx_count"],
                "unique_tokens": r["unique_tokens"],
                "buy_shaped_txs": r["buy_shaped_txs"],
                "good_ca_overlap": r["good_ca_overlap"],
                "in_watch": r["in_watch"],
                "last_block": r["last_block"],
            }
            for r in top20
        ],
        "updated_at": now_utc.isoformat(),
    }
    STATE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(
        f"done hot={len(hot)} scanned={scanned} receipts={used_receipts} "
        f"elapsed={summary['elapsed_s']}s → {OUT_JSONL.name} + {OUT_MD.name}"
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="RH on-chain hot wallet hunter")
    ap.add_argument("--blocks", type=int, default=None, help="override HUNT_BLOCKS")
    ap.add_argument("--daemon", action="store_true", help="loop every HUNT_INTERVAL_SEC")
    ap.add_argument("--interval", type=int, default=None, help="daemon interval seconds")
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    args = ap.parse_args()

    if args.blocks is not None:
        os.environ["HUNT_BLOCKS"] = str(args.blocks)
    daemon = args.daemon or (os.environ.get("HUNT_DAEMON") or "").strip() in ("1", "true", "yes")
    interval = args.interval if args.interval is not None else env_int("HUNT_INTERVAL_SEC", 600)

    log(f"start LIVE_TRADING={os.environ.get('LIVE_TRADING', '0')} daemon={daemon} interval={interval}")
    rpc = RpcClient(RPC_URL)

    while True:
        try:
            summary = hunt_once(rpc)
            print(json.dumps({"ok": True, "hot": summary["hot"], "scanned": summary["scanned"]}, ensure_ascii=False))
        except Exception as e:
            log(f"FATAL {type(e).__name__}: {e}")
            if not daemon:
                return 1
        if not daemon or args.once:
            break
        log(f"daemon sleep {interval}s")
        time.sleep(max(30, interval))
        # reset per-loop call counters soft
        rpc.calls = 0
        rpc.errors_429 = 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

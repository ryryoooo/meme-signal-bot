#!/usr/bin/env python3
"""Free RH onchain watchlist buy detector → Discord (no FOMO / no box GMGN).

Polls public RH RPC for new blocks; when a watchlist address sends a tx that
receives an ERC-20 Transfer (meme token buy heuristic), posts via the same
passthrough / priority path as bot.py.

Env (key knobs):
  RH_RPC_URL                 default https://rpc.mainnet.chain.robinhood.com
  ONCHAIN_POLL_SECONDS       loop sleep (default 2)
  ONCHAIN_MAX_BLOCKS         tip window per tick (default 32)
  ONCHAIN_LOOKBACK_BOOT      first-run lookback blocks (default 12)
  ONCHAIN_TICK_ONCE=1        run one scan and exit
  WATCHLIST_PATH / STATE_PATH / COOLDOWN_SECONDS (default 300) /
  NOTIFY_MARKET_SOURCE=dex (card fields from DexScreener; never GMGN on box) /
  ENRICH_ON_DEX_FAIL=1 (default): on Dex CF/429/empty, dispatch GHA enrich-notify.yml
    instead of posting an empty (—) Discord card. Only GHA posts the full card.
  DEX_FAST_FAIL=1 (auto when ENRICH_ON_DEX_FAIL): ≤2s single Dex probe; no 5s×3 sleeps.
  NOTIFY_PASSTHROUGH …
  LIVE_TRADING stays off.

Detection (simple, log false positives):
  - tx.from ∈ watchlist
  - receipt has ERC-20 Transfer where `to` == wallet
  - token not in skip set (WETH/stables/zero)
  - prefer swap-shaped txs (wallet also sent some token, or input calldata)
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
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Soft env defaults before importing bot (bot reads many at import time)
os.environ.setdefault("CHAIN", "robinhood")
os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")
os.environ.setdefault("GMGN_SMARTMONEY", "0")
os.environ.setdefault("FOMO_ENABLED", "0")
os.environ.setdefault("NOTIFY_PASSTHROUGH", "1")
os.environ.setdefault("HARD_MARKET_GATES", "1")
os.environ.setdefault("HONEYPOT_REQUIRE", "1")
os.environ.setdefault("HARD_MIN_VOLUME_H24_USD", "5000")
os.environ.setdefault("HARD_MIN_VOLUME_M5_USD", "500")
os.environ.setdefault("RH_NOTIFY_ALWAYS", "1")
os.environ.setdefault("PAPER_TRADING", "0")
os.environ.setdefault("DROP_WEAK_WALLETS", "0")
os.environ.setdefault("DROP_BOT_WALLETS", "0")
os.environ.setdefault("WATCH_MIN_REALIZED_HARD", "100")
os.environ.setdefault("MIN_WALLETS", "2")
os.environ.setdefault("COOLDOWN_SECONDS", "300")
os.environ.setdefault("NOTIFY_MARKET_SOURCE", "dex")
os.environ.setdefault("ENRICH_ON_DEX_FAIL", "1")
os.environ.setdefault("GMGN_MARKET", "0")
# Box: never stall the tip-follow loop on Dex CF/429 — 0–1 short probe then GHA handoff
if (os.environ.get("ENRICH_ON_DEX_FAIL") or "1").strip().lower() not in ("0", "false", "no", "off"):
    os.environ.setdefault("DEX_FAST_FAIL", "1")
    os.environ.setdefault("DEX_HTTP_ATTEMPTS", "1")
    os.environ.setdefault("DEX_HTTP_TIMEOUT", "2")
    os.environ.setdefault("DEX_RETRY_AFTER_CAP", "0.05")
    os.environ.setdefault("DEX_CURL_ATTEMPTS", "0")
    os.environ.setdefault("DEX_GECKO_FALLBACK", "0")

import bot as bot_mod  # noqa: E402
from load_secrets import load as load_secrets  # noqa: E402

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
JST = timezone(timedelta(hours=9))

# Common RH / EVM quote assets to ignore as the "bought" token
SKIP_TOKENS = {
    ZERO,
    "0x0000000000000000000000000000000000000000",
    # WETH / wrapped natives often used as quote
    "0x4200000000000000000000000000000000000006",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    # USDC / USDT variants (expand as needed; false-positive log covers rest)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
}

RPC_URL = (os.environ.get("RH_RPC_URL") or "https://rpc.mainnet.chain.robinhood.com").strip()
UA = os.environ.get(
    "ONCHAIN_HTTP_UA",
    "Mozilla/5.0 (compatible; meme-signal-bot/1.0; +https://github.com/ryryoooo/meme-signal-bot)",
)


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
    print(f"{ts} onchain {msg}", flush=True)


class RpcClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._backoff = 0.25
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
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                self.calls += 1
                self._backoff = max(0.15, self._backoff * 0.85)
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                wait = min(20.0, self._backoff * (2 ** attempt))
                if e.code in (429, 403, 502, 503, 504):
                    self.errors_429 += 1
                    ra = e.headers.get("Retry-After")
                    try:
                        wait = max(wait, float(ra))
                    except (TypeError, ValueError):
                        pass
                    log(f"rpc HTTP {e.code} {label} sleep={wait:.1f}s")
                    time.sleep(wait)
                    self._backoff = min(6.0, self._backoff * 1.4)
                    continue
                time.sleep(wait)
            except Exception as e:
                last_err = e
                wait = min(12.0, self._backoff * (2 ** attempt))
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
                time.sleep(min(8.0, self._backoff * 2))
                return self.call(method, params)
            raise RuntimeError(msg)
        return body.get("result") if isinstance(body, dict) else None

    def batch(self, calls: list[tuple[str, list]]) -> list[object]:
        """JSON-RPC batch; returns results aligned with calls (None on item error)."""
        if not calls:
            return []
        if len(calls) == 1:
            return [self.call(calls[0][0], calls[0][1])]
        reqs = [
            {"jsonrpc": "2.0", "id": i, "method": m, "params": p}
            for i, (m, p) in enumerate(calls)
        ]
        payload = json.dumps(reqs).encode()
        body = self._post(payload, f"batch[{len(calls)}]")
        if isinstance(body, dict):
            # some gateways wrap single — treat as fail → fallback sequential
            return [self.call(m, p) for m, p in calls]
        if not isinstance(body, list):
            return [self.call(m, p) for m, p in calls]
        by_id = {}
        for item in body:
            if isinstance(item, dict) and "id" in item:
                by_id[item["id"]] = item
        out: list[object] = []
        for i, (m, p) in enumerate(calls):
            item = by_id.get(i)
            if not item:
                out.append(None)
                continue
            if item.get("error"):
                out.append(None)
                continue
            out.append(item.get("result"))
        return out


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


def load_watch_addrs(path: Path) -> tuple[dict[str, dict], set[str]]:
    min_realized = env_float("WATCH_MIN_REALIZED_USD", 0.0)
    # Keep filters soft for onchain (same as FOMO soft RH tick)
    os.environ.setdefault("DROP_WEAK_WALLETS", "0")
    watch, raw_n, fallback = bot_mod.load_watchlist(path, min_realized)
    addrs = set(watch.keys())
    log(f"watchlist path={path} raw={raw_n} keep={len(addrs)} fallback={fallback}")
    return watch, addrs


def classify_buys_from_receipt(
    tx: dict,
    receipt: dict,
    watch_set: set[str],
) -> list[dict]:
    """Return list of buy dicts: {wallet, ca, amount_raw, tx_hash, block, false_positive_hint}."""
    fr = (tx.get("from") or "").lower()
    if fr not in watch_set:
        return []
    if str(receipt.get("status") or "0x1").lower() in ("0x0", "0"):
        return []
    logs = receipt.get("logs") or []
    received: dict[str, int] = defaultdict(int)  # token -> amount
    sent: dict[str, int] = defaultdict(int)
    for lg in logs:
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
        amt = decode_uint(lg.get("data") or "0x0")
        if to == fr:
            received[token] += amt
        if frm == fr:
            sent[token] += amt

    buys: list[dict] = []
    input_data = tx.get("input") or "0x"
    has_calldata = isinstance(input_data, str) and len(input_data) > 10
    for token, amt in received.items():
        if token in SKIP_TOKENS or token == fr:
            continue
        # Heuristic: meme buy if wallet received token and either sent another
        # asset (swap) or called a contract with calldata.
        sent_other = any(t != token and a > 0 for t, a in sent.items())
        if not (sent_other or has_calldata):
            # Possible airdrop / push — log as soft false-positive candidate
            hint = "recv_only_no_calldata"
        elif not sent_other and has_calldata:
            hint = "recv_with_calldata"
        else:
            hint = "swap_shaped"
        buys.append(
            {
                "wallet": fr,
                "ca": token,
                "amount_raw": amt,
                "tx_hash": tx.get("hash"),
                "block": int(receipt.get("blockNumber") or tx.get("blockNumber") or "0x0", 16)
                if isinstance(receipt.get("blockNumber") or tx.get("blockNumber"), str)
                else int(receipt.get("blockNumber") or 0),
                "to": (tx.get("to") or "").lower(),
                "hint": hint,
            }
        )
    return buys


def estimate_usd(ca: str, amount_raw: int, decimals_hint: int | None, price_usd: float | None) -> float:
    if not price_usd or amount_raw <= 0:
        return 0.0
    dec = decimals_hint if decimals_hint is not None else 18
    try:
        return (amount_raw / (10 ** dec)) * float(price_usd)
    except Exception:
        return 0.0


def cluster_buys(buys: list[dict], window_sec: int) -> list[dict]:
    """Group by CA; each signal has wallets list."""
    by_ca: dict[str, list[dict]] = defaultdict(list)
    for b in buys:
        by_ca[b["ca"]].append(b)
    signals = []
    now = time.time()
    for ca, rows in by_ca.items():
        # unique wallets
        by_w: dict[str, dict] = {}
        for r in rows:
            w = r["wallet"]
            prev = by_w.get(w)
            if prev is None or (r.get("amount_raw") or 0) > (prev.get("amount_raw") or 0):
                by_w[w] = r
        wallets = []
        for w, r in by_w.items():
            wallets.append(
                {
                    "address": w,
                    "usd": float(r.get("usd") or 0),
                    "label": r.get("label") or "",
                    "tx_hash": r.get("tx_hash"),
                    "hint": r.get("hint"),
                }
            )
        t0 = now
        key = f"{ca}:onchain:{','.join(sorted(by_w.keys()))}:{int(t0)}"
        signals.append(
            {
                "ca": ca,
                "n": len(wallets),
                "wallets": wallets,
                "key": key,
                "symbol": None,
                "elapsed": 0,
                "window": window_sec,
            }
        )
    return signals



def _dex_has_usable_nums(safety: dict) -> bool:
    """True when card has at least one real market number / symbol (not all dashes)."""
    if not isinstance(safety, dict):
        return False
    if safety.get("fetch_failed"):
        return False
    return any(safety.get(k) is not None for k in ("mcap_usd", "price_usd", "symbol_hint", "liq_usd"))


def dispatch_enrich_notify(
    *,
    ca: str,
    chain: str,
    wallets: list[dict],
    seen_key: str,
    tx_hash: str | None = None,
) -> bool:
    """Fire-and-forget GHA enrich-notify.yml (Dex from Actions IP → Discord)."""
    import subprocess

    if (os.environ.get("ENRICH_ON_DEX_FAIL") or "1").strip().lower() in ("0", "false", "no", "off"):
        log("enrich dispatch disabled ENRICH_ON_DEX_FAIL=0")
        return False
    repo = (os.environ.get("SIGNAL_STATE_REPO") or os.environ.get("GITHUB_REPOSITORY") or "ryryoooo/meme-signal-bot").strip()
    wf = (os.environ.get("ENRICH_WORKFLOW") or "enrich-notify.yml").strip()
    compact = []
    for w in wallets or []:
        if not isinstance(w, dict):
            continue
        addr = (w.get("address") or "").strip().lower()
        if not addr:
            continue
        try:
            usd = float(w.get("usd") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        compact.append(
            {
                "address": addr,
                "usd": usd,
                "label": str(w.get("label") or "")[:80],
                "source": str(w.get("source") or "onchain"),
            }
        )
        if not tx_hash and w.get("tx_hash"):
            tx_hash = str(w.get("tx_hash"))
    wallets_json = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    # GitHub workflow_dispatch input soft cap — keep under ~50k
    if len(wallets_json) > 48000:
        wallets_json = json.dumps(compact[:12], ensure_ascii=False, separators=(",", ":"))
    cmd = [
        "gh",
        "workflow",
        "run",
        wf,
        "--repo",
        repo,
        "-f",
        f"ca={ca}",
        "-f",
        f"chain={chain}",
        "-f",
        f"wallets={wallets_json}",
        "-f",
        f"seen_key={seen_key}",
    ]
    if tx_hash:
        cmd.extend(["-f", f"tx_hash={tx_hash}"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0:
            log(f"enrich dispatch ok ca={ca[:12]}… wf={wf} repo={repo}")
            return True
        log(f"enrich dispatch FAIL rc={r.returncode} ca={ca[:12]}… {out[:240]}")
        return False
    except Exception as e:
        log(f"enrich dispatch exception {type(e).__name__}: {e}")
        return False


def post_signals(
    signals: list[dict],
    watch: dict[str, dict],
    state: dict,
    chain: str = "robinhood",
) -> tuple[int, int]:
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook:
        try:
            webhook = bot_mod.resolve_signal_webhook(chain)
        except SystemExit:
            webhook = ""
    if not webhook:
        log("WARN no DISCORD_WEBHOOK_URL — dry log only")
    cooldown = env_int("COOLDOWN_SECONDS", 300)
    min_wallets = env_int("MIN_WALLETS", 2)
    seen = set(state.get("seen_signal_keys") or [])
    ca_last: dict = dict(state.get("ca_last_posted") or {})
    open_alerts: list = list(state.get("open_alerts") or [])
    now = time.time()
    posted = skipped = 0
    paper_path = Path(os.environ.get("PAPER_LOG_PATH", str(ROOT / "paper_log.jsonl")))

    for s in signals:
        ca = s["ca"]
        # attach labels from watch
        for w in s["wallets"]:
            meta = watch.get((w.get("address") or "").lower()) or {}
            lab = ""
            labels = meta.get("labels") or []
            if labels:
                lab = str(labels[0])
            elif meta.get("label"):
                lab = str(meta["label"])
            w["label"] = lab
            w["tags"] = list(meta.get("tags") or [])

        total_usd = sum(float(w.get("usd") or 0) for w in s["wallets"])
        if s["n"] < min_wallets:
            skipped += 1
            log(f"skip n<{min_wallets} ca={ca[:12]}…")
            continue
        if s["key"] in seen:
            skipped += 1
            continue
        last_ts = ca_last.get(ca)
        if last_ts is not None and (now - float(last_ts)) < cooldown:
            remain = int(cooldown - (now - float(last_ts)))
            log(f"cooldown skip ca={ca[:12]}… remain={remain}s")
            seen.add(s["key"])
            skipped += 1
            continue

        # Force Dex-only card fill on box onchain path — never require GMGN
        try:
            snap = bot_mod.gmgn_tok.market_snapshot(
                bot_mod.CHAIN_META.get(chain, {}).get("gmgn_chain") or chain, ca
            )
        except Exception:
            snap = {}
        if bot_mod.notify_market_source("dex") == "dex" or bot_mod.gmgn_tok.gmgn_disabled():
            safety = bot_mod.dex_only_safety(ca, chain, snap=snap if isinstance(snap, dict) else None)
        else:
            safety = bot_mod.safety_check(ca, chain)
        passthrough = bot_mod.notify_passthrough_enabled(chain)
        pt_reasons: list[str] = []
        if (not safety.get("ok")) or safety.get("fetch_failed"):
            if passthrough:
                if not safety.get("dex_overlay"):
                    safety = bot_mod.merge_dex_fields_for_passthrough(dict(safety), ca, chain)
                fail_rs = [r for r in (safety.get("reasons") or []) if r and r != "ok"]
                if safety.get("fetch_failed") and "fetch_failed" not in fail_rs:
                    fail_rs.append("fetch_failed")
                pt_reasons.extend(fail_rs)
                safety["ok"] = True
                safety["passthrough"] = True
                prev_jp = safety.get("jp") or ""
                note = "passthrough・onchain・dex"
                if "passthrough" not in prev_jp:
                    safety["jp"] = (prev_jp + f"（{note}）") if prev_jp else f"（{note}）"
            else:
                log(f"skip safety_fail ca={ca[:12]}… {safety.get('reasons')}")
                seen.add(s["key"])
                skipped += 1
                continue
        # Log card fill so dry-run / ops can verify Dex mcap
        log(
            f"dex_fill ca={ca[:12]}… ok={safety.get('ok')} src={safety.get('source')} "
            f"sym={safety.get('symbol_hint')} mcap={safety.get('mcap_usd')} "
            f"liq={safety.get('liq_usd')} px={safety.get('price_usd')} "
            f"vol24={safety.get('volume_h24')} fetch_failed={safety.get('fetch_failed')}"
        )

        # HARD honeypot/scam (no volume yet) — blocks Discord AND enrich spam
        hp_fails = bot_mod.notify_hard_gate_reasons(
            safety, ca, chain, require_volume=False
        )
        if hp_fails:
            log(f"skip hard_gate (pre-enrich) ca={ca[:12]}… fails={hp_fails}")
            seen.add(s["key"])
            skipped += 1
            try:
                bot_mod.append_paper_log(
                    paper_path,
                    {
                        "ca": ca,
                        "n": s["n"],
                        "total_usd": total_usd,
                        "key": s["key"],
                        "chain": chain,
                        "source": "onchain",
                        "source_mode": "onchain_watch",
                        "posted": False,
                        "reason": "hard_gate:" + ",".join(hp_fails),
                    },
                )
            except Exception:
                pass
            continue

        # Dex empty / CF-429 on box → do NOT post empty (—) card; GHA enrich posts full card
        if not _dex_has_usable_nums(safety):
            ok_disp = dispatch_enrich_notify(
                ca=ca,
                chain=chain,
                wallets=list(s.get("wallets") or []),
                seen_key=str(s.get("key") or ""),
            )
            seen.add(s["key"])
            ca_last[ca] = now
            if ok_disp:
                posted += 1  # count as handed-off notify (GHA will post)
                log(f"enrich handed-off ca={ca} (no empty Discord card)")
                try:
                    bot_mod.append_paper_log(
                        paper_path,
                        {
                            "ca": ca,
                            "symbol": None,
                            "n": s["n"],
                            "total_usd": total_usd,
                            "key": s["key"],
                            "chain": chain,
                            "source": "onchain",
                            "source_mode": "onchain_watch",
                            "posted": False,
                            "reason": "enrich_dispatch",
                            "mcap": None,
                            "liq": None,
                        },
                    )
                except Exception:
                    pass
            else:
                skipped += 1
                log(f"enrich dispatch failed — suppressing empty card ca={ca[:12]}…")
            continue

        # Fill USD from dex price when missing
        price = safety.get("price_usd")
        for w in s["wallets"]:
            if float(w.get("usd") or 0) <= 0 and price:
                # amount_raw stored on matching buy via hint only — skip if unknown
                pass
        # Recompute total after any fills
        total_usd = sum(float(w.get("usd") or 0) for w in s["wallets"])
        # If still 0, use MIN_TRADE_USD as placeholder for priority math only when unknown
        if total_usd <= 0:
            placeholder = env_float("ONCHAIN_USD_PLACEHOLDER", 0.0)
            if placeholder > 0:
                for w in s["wallets"]:
                    w["usd"] = placeholder
                total_usd = placeholder * s["n"]

        wallet_scores = [
            bot_mod.wallet_quality_score(watch.get((w.get("address") or "").lower()) or {})
            for w in s["wallets"]
        ]
        # HARD volume (+ re-check honeypot) — passthrough cannot override
        hard_fails = bot_mod.notify_hard_gate_reasons(
            safety, ca, chain, require_volume=True
        )
        if hard_fails:
            log(f"skip hard_gate ca={ca[:12]}… fails={hard_fails}")
            seen.add(s["key"])
            skipped += 1
            try:
                bot_mod.append_paper_log(
                    paper_path,
                    {
                        "ca": ca,
                        "symbol": safety.get("symbol_hint"),
                        "n": s["n"],
                        "total_usd": total_usd,
                        "key": s["key"],
                        "chain": chain,
                        "source": "onchain",
                        "source_mode": "onchain_watch",
                        "posted": False,
                        "reason": "hard_gate:" + ",".join(hard_fails),
                        "volume_h24": safety.get("volume_h24"),
                        "volume_m5": safety.get("volume_m5"),
                        "mcap": safety.get("mcap_usd"),
                        "liq": safety.get("liq_usd"),
                    },
                )
            except Exception:
                pass
            continue
        market_fails = bot_mod.notify_market_gate_reasons(safety, total_usd, wallet_scores)
        soft_fails = [r for r in market_fails if not bot_mod.is_hard_notify_reason(r)]
        if soft_fails:
            if passthrough:
                pt_reasons.extend(soft_fails)
                log(f"passthrough ignore gates ca={ca[:12]}… fails={soft_fails}")
            else:
                seen.add(s["key"])
                skipped += 1
                continue

        if pt_reasons:
            log(f"passthrough post ca={ca[:12]}… reasons={pt_reasons}")

        # stamp symbol
        s["symbol"] = safety.get("symbol_hint")
        # annotate source for embed mix (smart wallets)
        for w in s["wallets"]:
            w.setdefault("source", "onchain")

        pri_ok, pri_reasons = bot_mod.priority_score_ok(s["n"], total_usd, safety)
        pri_hook = None
        if pri_ok:
            try:
                pri_hook = bot_mod.resolve_priority_webhook(chain)
            except SystemExit:
                pri_hook = None
        style_main = bool(pri_ok and not pri_hook)

        jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        log(
            f"notify ca={ca} n={s['n']} usd={total_usd:.0f} pri={pri_ok} "
            f"sym={s.get('symbol')} {jst}"
        )

        if not webhook:
            seen.add(s["key"])
            ca_last[ca] = now
            posted += 1
            continue

        embed = bot_mod.build_embed(
            s, chain, safety, "onchain_watch", watch=watch, priority=style_main
        )
        # Clarify footer path
        try:
            if isinstance(embed.get("footer"), dict):
                base = embed["footer"].get("text") or ""
                embed["footer"]["text"] = (base + " · onchain RPC").strip(" ·")
        except Exception:
            pass
        resp = bot_mod.discord_webhook(webhook, content="", embeds=[embed])
        if pri_ok and pri_hook:
            try:
                pri_embed = bot_mod.build_embed(
                    s, chain, safety, "onchain_watch", watch=watch, priority=True
                )
                bot_mod.discord_webhook(pri_hook, content="", embeds=[pri_embed])
            except Exception as e:
                log(f"priority webhook fail {type(e).__name__}")
        msg_id = resp.get("id") if isinstance(resp, dict) else None
        seen.add(s["key"])
        ca_last[ca] = now
        open_alerts = [a for a in open_alerts if (a.get("ca") or "").lower() != ca]
        open_alerts.append(
            {
                "ca": ca,
                "symbol": s.get("symbol") or safety.get("symbol_hint"),
                "alert_price_usd": safety.get("price_usd"),
                "alert_mcap": safety.get("mcap_usd") or safety.get("fdv"),
                "alert_liq": safety.get("liq_usd"),
                "posted_at": now,
                "message_id": msg_id,
                "milestones_hit": [],
                "source_mode": "onchain_watch",
                "priority": bool(pri_ok),
            }
        )
        posted += 1
        try:
            bot_mod.append_paper_log(
                paper_path,
                {
                    "ca": ca,
                    "symbol": s.get("symbol"),
                    "n": s["n"],
                    "total_usd": total_usd,
                    "key": s["key"],
                    "chain": chain,
                    "source": "onchain",
                    "source_mode": "onchain_watch",
                    "posted": True,
                    "reason": "posted",
                    "priority": bool(pri_ok),
                    "priority_reasons": pri_reasons if not pri_ok else [],
                    "mcap": safety.get("mcap_usd"),
                    "liq": safety.get("liq_usd"),
                },
            )
        except Exception:
            pass
        time.sleep(0.4)

    state["seen_signal_keys"] = list(seen)[-500:]
    state["ca_last_posted"] = ca_last
    state["open_alerts"] = open_alerts[-200:]
    state["trade_source"] = "onchain"
    state["source_mode"] = "onchain_watch"
    state["last_onchain_poll_ts"] = datetime.now(timezone.utc).isoformat()
    return posted, skipped


def scan_once(rpc: RpcClient, watch: dict[str, dict], watch_set: set[str], state: dict) -> dict:
    """Tip-follow scan: batch getBlock, receipts only for watchlist senders, no Dex during scan."""
    head_hex = rpc.call("eth_blockNumber", [])
    head = int(head_hex, 16)
    last = state.get("onchain_last_block")
    lookback_boot = env_int("ONCHAIN_LOOKBACK_BOOT", 12)
    # Tip window: finish in ~1–3s. Prefer missing old blocks over lagging the tip.
    max_blocks = env_int("ONCHAIN_MAX_BLOCKS", 24)
    batch_size = env_int("ONCHAIN_BATCH_SIZE", 1)
    if last is None:
        start = max(0, head - lookback_boot + 1)
    else:
        try:
            last_i = int(last)
        except (TypeError, ValueError):
            last_i = head - lookback_boot
        start = last_i + 1
    if start > head:
        return {"head": head, "scanned": 0, "buys": 0, "posted": 0, "skipped": 0, "watch_txs": 0}
    if head - start + 1 > max_blocks:
        start = head - max_blocks + 1
        log(f"tip-follow skip-gap start={start} head={head} max={max_blocks}")

    all_buys: list[dict] = []
    fp_n = 0
    scanned = 0
    watch_tx_n = 0
    hit_txs: list[dict] = []

    bn = start
    while bn <= head:
        chunk = list(range(bn, min(head, bn + max(1, batch_size) - 1) + 1))
        if batch_size <= 1:
            results = [rpc.call("eth_getBlockByNumber", [hex(x), True]) for x in chunk]
        else:
            calls = [("eth_getBlockByNumber", [hex(x), True]) for x in chunk]
            results = rpc.batch(calls)
        for block_num, blk in zip(chunk, results):
            scanned += 1
            if not isinstance(blk, dict):
                continue
            for tx in blk.get("transactions") or []:
                if not isinstance(tx, dict):
                    continue
                fr = (tx.get("from") or "").lower()
                if fr in watch_set:
                    hit_txs.append(tx)
                    watch_tx_n += 1
        bn = chunk[-1] + 1

    # Receipts (sequential default — public RPC hates big batches)
    receipt_batch = env_int("ONCHAIN_RECEIPT_BATCH", 1)
    step = max(1, receipt_batch)
    for i in range(0, len(hit_txs), step):
        group = hit_txs[i : i + step]
        if step <= 1:
            rcpts = [rpc.call("eth_getTransactionReceipt", [tx["hash"]]) for tx in group]
        else:
            rcalls = [("eth_getTransactionReceipt", [tx["hash"]]) for tx in group]
            rcpts = rpc.batch(rcalls)
        for tx, rcpt in zip(group, rcpts):
            if not isinstance(rcpt, dict):
                continue
            buys = classify_buys_from_receipt(tx, rcpt, watch_set)
            for b in buys:
                meta = watch.get(b["wallet"]) or {}
                labels = meta.get("labels") or []
                b["label"] = str(labels[0]) if labels else ""
                b["usd"] = 0.0
                if b.get("hint") == "recv_only_no_calldata":
                    fp_n += 1
                    log(
                        f"fp_candidate wallet={b['wallet'][:10]}… ca={b['ca'][:12]}… "
                        f"tx={str(b.get('tx_hash'))[:14]}… hint={b['hint']}"
                    )
                    if not (os.environ.get("ONCHAIN_ALLOW_RECV_ONLY") or "").strip().lower() in (
                        "1", "true", "yes",
                    ):
                        continue
                all_buys.append(b)
                log(
                    f"buy wallet={b['wallet'][:10]}… ca={b['ca'][:12]}… "
                    f"hint={b.get('hint')} tx={str(b.get('tx_hash'))[:14]}… "
                    f"blk={b.get('block')}"
                )

    state["onchain_last_block"] = head
    window = env_int("WINDOW_SECONDS", 1200)
    signals = cluster_buys(all_buys, window) if all_buys else []
    # Optional pre-fill USD from Dex once per CA (post_signals also fills card fields).
    # Skip when NOTIFY_MARKET_SOURCE=dex to avoid double Dex round-trips on the hot path.
    prefill = (os.environ.get("NOTIFY_MARKET_SOURCE") or "dex").strip().lower() not in (
        "dex", "dexscreener", "dex-only", "dex_only",
    )
    if signals and prefill:
        for s in signals:
            ca = s["ca"]
            try:
                snap = bot_mod.gmgn_tok.market_snapshot("robinhood", ca)
            except Exception:
                snap = {}
            if isinstance(snap, dict) and snap.get("price_usd"):
                s["symbol"] = snap.get("symbol") or s.get("symbol")
            for w in s["wallets"]:
                for b in all_buys:
                    if b["ca"] == ca and b["wallet"] == (w.get("address") or "").lower():
                        if isinstance(snap, dict) and snap.get("price_usd"):
                            w["usd"] = estimate_usd(ca, int(b["amount_raw"]), 18, float(snap["price_usd"]))
                        break

    posted = skipped = 0
    if signals:
        posted, skipped = post_signals(signals, watch, state, "robinhood")
    return {
        "head": head,
        "start": start,
        "scanned": scanned,
        "watch_txs": watch_tx_n,
        "buys": len(all_buys),
        "fp": fp_n,
        "signals": len(signals),
        "posted": posted,
        "skipped": skipped,
        "rpc_calls": rpc.calls,
        "rpc_429": rpc.errors_429,
    }


def patch_health(stats: dict) -> None:
    path = Path(
        os.environ.get(
            "SIGNAL_TICK_HEALTH",
            "/home/box/.local/share/scout-wallet-bot/health.json",
        )
    )
    try:
        data = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        data["onchain_tick"] = {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **{k: stats.get(k) for k in (
                "head", "scanned", "watch_txs", "buys", "signals", "posted", "skipped",
                "rpc_calls", "rpc_429", "elapsed_sec", "poll_sec",
            )},
            "source": "onchain",
            "rpc": RPC_URL,
        }
        path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as e:
        log(f"health patch fail {type(e).__name__}")


def main() -> int:
    ap = argparse.ArgumentParser(description="RH free onchain watchlist buy tick")
    ap.add_argument("--once", action="store_true", help="single scan then exit")
    ap.add_argument("--dry-run", action="store_true", help="scan/classify only, no Discord")
    args = ap.parse_args()
    once = args.once or (os.environ.get("ONCHAIN_TICK_ONCE") or "").strip() in ("1", "true", "yes")

    load_secrets(
        [
            "DISCORD_WEBHOOK_URL",
            "DISCORD_PRIORITY_WEBHOOK_URL",
            "DISCORD_PAPER_WEBHOOK_URL",
        ]
    )
    if args.dry_run:
        os.environ["DISCORD_WEBHOOK_URL"] = ""

    watch_path = Path(
        os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets" / "wallets.jsonl"))
    ).resolve()
    state_path = Path(os.environ.get("STATE_PATH", str(ROOT / "state.json"))).resolve()
    watch, watch_set = load_watch_addrs(watch_path)
    if not watch_set:
        log("empty watchlist — exit")
        return 1

    poll = env_float("ONCHAIN_POLL_SECONDS", 2.0)
    if poll < 1:
        poll = 1.0
    rpc = RpcClient(RPC_URL)
    watch_reload = env_float("ONCHAIN_WATCH_RELOAD_SEC", 60.0)
    last_watch_load = time.time()
    log(
        f"start rpc={RPC_URL} watch={len(watch_set)} poll={poll}s once={once} "
        f"max_blocks={env_int('ONCHAIN_MAX_BLOCKS', 24)} batch={env_int('ONCHAIN_BATCH_SIZE', 1)} "
        f"state={state_path} passthrough={bot_mod.notify_passthrough_enabled('robinhood')}"
    )

    while True:
        t0 = time.time()
        if (not once) and (t0 - last_watch_load) >= watch_reload:
            try:
                watch, watch_set = load_watch_addrs(watch_path)
                last_watch_load = t0
            except Exception as e:
                log(f"watch reload fail {type(e).__name__}")
        # Reset per-tick call counters
        rpc.calls = 0
        rpc.errors_429 = 0
        state = bot_mod.load_state(state_path)
        try:
            stats = scan_once(rpc, watch, watch_set, state)
        except Exception as e:
            log(f"scan fail {type(e).__name__}: {e}")
            stats = {"error": str(e), "posted": 0, "scanned": 0}
        elapsed = time.time() - t0
        stats["elapsed_sec"] = round(elapsed, 2)
        stats["poll_sec"] = poll
        bot_mod.save_state(state_path, state)
        log(
            f"tick head={stats.get('head')} scanned={stats.get('scanned')} "
            f"watch_txs={stats.get('watch_txs')} buys={stats.get('buys')} "
            f"signals={stats.get('signals')} posted={stats.get('posted')} "
            f"skipped={stats.get('skipped')} rpc={stats.get('rpc_calls')} "
            f"429={stats.get('rpc_429')} elapsed={elapsed:.1f}s"
        )
        patch_health(stats)
        if once:
            return 0
        sleep_for = max(0.3, poll - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())

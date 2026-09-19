#!/usr/bin/env python3
"""GHA enrich-notify: DexScreener from Actions IP → Discord full card.

Box onchain detection stays on the box. When DexScreener is CF/429-blocked there,
the box dispatches this workflow instead of posting an empty (—) card.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "0")
os.environ.setdefault("GMGN_DISABLED", "1")
os.environ.setdefault("GMGN_MARKET", "0")
os.environ.setdefault("NOTIFY_MARKET_SOURCE", "dex")
os.environ.setdefault("NOTIFY_PASSTHROUGH", "1")
os.environ.setdefault("CHAIN", "robinhood")

import bot as bot_mod  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def _compact_wallets(raw) -> list[dict]:
    out: list[dict] = []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return out
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            _log(f"WARN wallets JSON parse fail len={len(raw)}")
            return out
    if not isinstance(raw, list):
        return out
    for w in raw:
        if not isinstance(w, dict):
            continue
        addr = (w.get("address") or w.get("wallet") or "").strip().lower()
        if not addr:
            continue
        try:
            usd = float(w.get("usd") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        out.append(
            {
                "address": addr,
                "usd": usd,
                "label": str(w.get("label") or ""),
                "source": str(w.get("source") or "onchain"),
                "tx_hash": w.get("tx_hash"),
                "tags": list(w.get("tags") or []),
            }
        )
    return out


def _load_watch(path: Path) -> dict[str, dict]:
    watch: dict[str, dict] = {}
    if not path.exists():
        return watch
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            addr = (row.get("address") or row.get("wallet") or "").strip().lower()
            if addr:
                watch[addr] = row
    except Exception as e:
        _log(f"WARN watch load {type(e).__name__}: {e}")
    return watch


def _attach_labels(wallets: list[dict], watch: dict[str, dict]) -> None:
    for w in wallets:
        meta = watch.get((w.get("address") or "").lower()) or {}
        if w.get("label"):
            continue
        labels = meta.get("labels") or []
        if labels:
            w["label"] = str(labels[0])
        elif meta.get("label"):
            w["label"] = str(meta["label"])
        w.setdefault("tags", list(meta.get("tags") or []))


def _update_state(state_path: Path, *, ca: str, seen_key: str | None, safety: dict, n: int) -> None:
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    now = time.time()
    seen = list(state.get("seen_signal_keys") or [])
    if seen_key and seen_key not in seen:
        seen.append(seen_key)
    state["seen_signal_keys"] = seen[-500:]
    ca_last = dict(state.get("ca_last_posted") or {})
    ca_last[ca.lower()] = now
    # prune
    items = sorted(ca_last.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:400]
    state["ca_last_posted"] = dict(items)
    open_alerts = [a for a in (state.get("open_alerts") or []) if (a.get("ca") or "").lower() != ca.lower()]
    open_alerts.append(
        {
            "ca": ca.lower(),
            "symbol": safety.get("symbol_hint"),
            "alert_price_usd": safety.get("price_usd"),
            "alert_mcap": safety.get("mcap_usd") or safety.get("fdv"),
            "alert_liq": safety.get("liq_usd"),
            "posted_at": now,
            "message_id": None,
            "milestones_hit": [],
            "source_mode": "gha_enrich",
            "priority": False,
            "n": n,
        }
    )
    state["open_alerts"] = open_alerts[-200:]
    state["trade_source"] = "onchain"
    state["source_mode"] = "gha_enrich"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Enrich + Discord notify from GHA (DexScreener)")
    p.add_argument("--ca", required=True)
    p.add_argument("--chain", default="robinhood")
    p.add_argument("--wallets", default="[]", help="JSON array of wallet dicts")
    p.add_argument("--tx-hash", default="", dest="tx_hash")
    p.add_argument("--seen-key", default="", dest="seen_key")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    ca = (args.ca or "").strip().lower()
    if not ca.startswith("0x") or len(ca) < 10:
        _log(f"ERROR bad ca={args.ca!r}")
        return 2
    chain = (args.chain or "robinhood").strip().lower()
    if chain in ("rh", "robinhood-chain"):
        chain = "robinhood"

    wallets = _compact_wallets(args.wallets)
    watch_path = Path(os.environ.get("WATCHLIST_PATH") or str(ROOT / "rh-wallets" / "wallets.jsonl"))
    watch = _load_watch(watch_path)
    _attach_labels(wallets, watch)
    if not wallets:
        wallets = [{"address": "0x0000000000000000000000000000000000000000", "usd": 0, "label": "manual", "source": "onchain"}]

    tx_hash = (args.tx_hash or "").strip() or None
    if tx_hash:
        for w in wallets:
            w.setdefault("tx_hash", tx_hash)

    seen_key = (args.seen_key or "").strip() or f"{ca}:gha_enrich:{int(time.time())}"

    _log(f"enrich start ca={ca} chain={chain} wallets={len(wallets)} seen_key={seen_key[:48]}…")

    # Fresh Dex fetch from GHA IP (no box CF ban)
    # Extra Dex attempts on GHA (different IP usually succeeds; retry transient 429)
    tries = int(os.environ.get("DEX_ENRICH_RETRIES") or "4")
    safety = {}
    for i in range(max(1, tries)):
        try:
            snap = bot_mod.gmgn_tok.market_snapshot(
                bot_mod.CHAIN_META.get(chain, {}).get("gmgn_chain") or chain, ca
            )
        except Exception as e:
            _log(f"dex snapshot exception try={i+1} {type(e).__name__}: {e}")
            snap = {}
        safety = bot_mod.dex_only_safety(ca, chain, snap=snap if isinstance(snap, dict) else None)
        if safety.get("ok") and not safety.get("fetch_failed"):
            break
        time.sleep(min(8, 1.5 * (i + 1)))
    _log(
        f"dex_fill ca={ca[:12]}… ok={safety.get('ok')} fetch_failed={safety.get('fetch_failed')} "
        f"sym={safety.get('symbol_hint')} mcap={safety.get('mcap_usd')} "
        f"liq={safety.get('liq_usd')} px={safety.get('price_usd')}"
    )

    has_nums = any(safety.get(k) is not None for k in ("mcap_usd", "price_usd", "symbol_hint", "liq_usd"))
    if safety.get("fetch_failed") or not safety.get("ok") or not has_nums:
        # Never post empty (—) cards from GHA either — fail loud so box can retry later
        _log("ERROR Dex empty on GHA — suppressing Discord post (no dashes card)")
        if args.dry_run:
            _log("dry-run would abort (no numbers)")
            return 1
        return 1

    # Mark as enrich path (numbers present)
    safety = dict(safety)
    safety["ok"] = True
    prev = safety.get("jp") or "通過（DexScreener）"
    if "GHA enrich" not in prev:
        safety["jp"] = f"{prev}（GHA enrich）" if "通過" in prev or "Dex" in prev else "通過（DexScreener・GHA enrich）"

    signal = {
        "ca": ca,
        "n": len(wallets),
        "wallets": wallets,
        "key": seen_key,
        "symbol": safety.get("symbol_hint"),
        "elapsed": 0,
        "window": int(os.environ.get("WINDOW_SECONDS") or 1200),
    }

    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook and not args.dry_run:
        try:
            webhook = bot_mod.resolve_signal_webhook(chain)
        except SystemExit:
            webhook = ""
    if not webhook and not args.dry_run:
        _log("ERROR no DISCORD_WEBHOOK_URL")
        return 3

    embed = bot_mod.build_embed(
        signal, chain, safety, "gha_enrich", watch=watch, priority=False
    )
    try:
        if isinstance(embed.get("footer"), dict):
            base = embed["footer"].get("text") or ""
            embed["footer"]["text"] = (base + " · GHA enrich").strip(" ·")
    except Exception:
        pass

    if args.dry_run:
        _log("dry-run embed=" + json.dumps(embed, ensure_ascii=False)[:800])
        return 0

    resp = bot_mod.discord_webhook(webhook, content="", embeds=[embed])
    msg_id = resp.get("id") if isinstance(resp, dict) else None
    _log(f"discord posted msg_id={msg_id} sym={safety.get('symbol_hint')} mcap={safety.get('mcap_usd')}")

    # Optional priority channel
    try:
        n = len(wallets)
        total_usd = sum(float(w.get("usd") or 0) for w in wallets)
        pri_ok, _ = bot_mod.priority_score_ok(n, total_usd, safety)
        if pri_ok:
            pri_hook = bot_mod.resolve_priority_webhook(chain)
            if pri_hook:
                pri_embed = bot_mod.build_embed(
                    signal, chain, safety, "gha_enrich", watch=watch, priority=True
                )
                bot_mod.discord_webhook(pri_hook, content="", embeds=[pri_embed])
                _log("priority webhook posted")
    except Exception as e:
        _log(f"priority skip {type(e).__name__}")

    state_path = Path(os.environ.get("STATE_PATH") or str(ROOT / "state.json"))
    _update_state(state_path, ca=ca, seen_key=seen_key, safety=safety, n=len(wallets))
    _log(f"state updated {state_path}")

    # Best-effort push shared state for box dedupe
    sync = ROOT / "scripts" / "signal_state_sync.sh"
    if sync.exists() and (os.environ.get("ENRICH_STATE_SYNC") or "1").strip() not in ("0", "false", "no"):
        import subprocess

        env = os.environ.copy()
        env["STATE_PATH"] = str(state_path)
        env["ROOT"] = str(ROOT)
        try:
            subprocess.run(["bash", str(sync), "push"], env=env, timeout=90, check=False)
        except Exception as e:
            _log(f"state sync push skip {type(e).__name__}")

    paper_path = Path(os.environ.get("PAPER_LOG_PATH") or str(ROOT / "paper_log.jsonl"))
    try:
        bot_mod.append_paper_log(
            paper_path,
            {
                "ca": ca,
                "symbol": safety.get("symbol_hint"),
                "n": len(wallets),
                "total_usd": sum(float(w.get("usd") or 0) for w in wallets),
                "key": seen_key,
                "chain": chain,
                "source": "gha_enrich",
                "source_mode": "gha_enrich",
                "posted": True,
                "reason": "gha_enrich",
                "mcap": safety.get("mcap_usd"),
                "liq": safety.get("liq_usd"),
                "price": safety.get("price_usd"),
            },
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Harvest GMGN KOL buy tracks + FOMO EVM board, batch-vet, merge consistent winners.

Credit-light defaults:
  KOL_LIMIT=150          track page size
  KOL_VET_CAP=30         max portfolio API calls (profits batches + stats fills)
  FOMO_VET_CAP=15        max FOMO addrs to portfolio-vet (from FOMO_VET_CAP share of budget)
  KOL_MIN_TOKENS=2       distinct tokens bought
  KOL_MIN_BUY_USD=200    sum buy USD floor
  KOL_CHAINS=robinhood,arc
  KOL_SKIP_FETCH=0       set 1 to reuse raw only (no track API)
  KOL_SKIP_VET=0         set 1 to skip portfolio calls
  REQUIRE_CONSISTENT_PNL / WATCH_MIN_* — imported filters from bot.py
  ALLOW_FOMO_WITHOUT_WR=0 — FOMO must earn WR via GMGN portfolio (or existing track record)

Mass KOL = noise. Only keep average/repeat winners that pass wallet_passes_filter.
FOMO leaderboard PnL alone is not enough — vet via portfolio stats/profits first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import load_secrets

    load_secrets.load(["GMGN_API_KEY"])
except Exception:
    pass

# Ensure gmgn-cli dotenv without printing secrets
_key = (os.environ.get("GMGN_API_KEY") or "").strip()
if _key:
    _dotenv = Path.home() / ".config" / "gmgn"
    _dotenv.mkdir(parents=True, exist_ok=True)
    (_dotenv / ".env").write_text("GMGN_API_KEY=" + _key + "\n", encoding="utf-8")
    (_dotenv / ".env").chmod(0o600)

os.environ.setdefault("REQUIRE_CONSISTENT_PNL", "1")
os.environ.setdefault("WATCH_MIN_WINRATE", "0.45")
os.environ.setdefault("WATCH_MIN_TRADES", "15")
os.environ.setdefault("WATCH_MIN_AVG_PNL_PER_TRADE", "50")
os.environ.setdefault("WATCH_MIN_REALIZED_HARD", "500")
os.environ["ALLOW_FOMO_WITHOUT_WR"] = "0"

import bot  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def chain_dir(chain: str) -> Path:
    if chain in ("robinhood", "rh"):
        return ROOT / "rh-wallets"
    return ROOT / "arc-wallets"


def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        a = (o.get("address") or "").lower()
        if a:
            out[a] = o
    return out


def write_jsonl(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rows[a], ensure_ascii=False) for a in sorted(rows)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def gmgn_raw(args: list[str], timeout: int = 120) -> tuple[object | None, str | None]:
    cli = shutil.which("gmgn-cli")
    if not cli:
        return None, "gmgn-cli_missing"
    try:
        p = subprocess.run(
            [cli, *args, "--raw"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, type(e).__name__
    blob = (p.stdout or "") + "\n" + (p.stderr or "")
    up = blob.upper()
    if "RATE_LIMIT" in up or "429" in blob or "RATE_LIMIT_BANNED" in up:
        return None, "rate_limited"
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        return None, f"rc={p.returncode}:{err[:200]}"
    out = (p.stdout or "").strip()
    if not out:
        return None, "empty"
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, "bad_json"


def extract_list(data) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for k in ("list", "data", "trades", "result", "items"):
        v = data.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in ("list", "data", "items", "trades"):
                if isinstance(v.get(k2), list):
                    return v[k2]
    return []


def num(x) -> float | None:
    try:
        if x is None:
            return None
        if isinstance(x, str):
            s = x.replace("$", "").replace(",", "").replace("%", "").strip()
            if not s:
                return None
            return float(s)
        return float(x)
    except Exception:
        return None


def fetch_kol_track(chain: str, limit: int, raw_dir: Path) -> tuple[list, str | None]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / "gmgn_track_kol.json"
    err_path = raw_dir / "gmgn_track_kol.err"
    data, err = gmgn_raw(
        ["track", "kol", "--chain", chain, "--limit", str(limit), "--side", "buy"]
    )
    if err:
        err_path.write_text(err + "\n", encoding="utf-8")
        # fall back to existing raw
        if out_path.exists() and out_path.stat().st_size > 2:
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                rows = extract_list(existing)
                print(f"[{chain}] track fetch fail ({err}); reuse raw n={len(rows)}")
                return rows, err
            except Exception:
                pass
        print(f"[{chain}] track fetch fail ({err}); no usable raw")
        return [], err
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    err_path.write_text("", encoding="utf-8")
    rows = extract_list(data)
    print(f"[{chain}] track ok n={len(rows)}")
    return rows, None


def load_raw_rows(raw_dir: Path) -> list:
    rows: list = []
    seen_tx: set[str] = set()
    for name in ("gmgn_track_kol.json", "gmgn_kol_buy.json"):
        p = raw_dir / name
        if not p.exists() or p.stat().st_size < 2:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for r in extract_list(data):
            if not isinstance(r, dict):
                continue
            side = (r.get("side") or "buy").lower()
            if side and side != "buy":
                continue
            tx = str(r.get("transaction_hash") or "")
            key = tx or f"{r.get('maker')}:{r.get('base_address')}:{r.get('timestamp')}"
            if key in seen_tx:
                continue
            seen_tx.add(key)
            rows.append(r)
    return rows


def aggregate_makers(rows: list) -> dict[str, dict]:
    by: dict[str, dict] = {}
    for r in rows:
        maker = (r.get("maker") or "").strip().lower()
        if not maker.startswith("0x"):
            continue
        mi = r.get("maker_info") or {}
        if not isinstance(mi, dict):
            mi = {}
        agg = by.setdefault(
            maker,
            {
                "address": maker,
                "n_buys": 0,
                "sum_buy_usd": 0.0,
                "tokens": set(),
                "address_label": "",
                "gmgn_tags": [],
                "twitter": "",
            },
        )
        agg["n_buys"] += 1
        usd = num(r.get("amount_usd") or r.get("buy_cost_usd")) or 0.0
        agg["sum_buy_usd"] += usd
        tok = (r.get("base_address") or "").strip().lower()
        if tok:
            agg["tokens"].add(tok)
        name = mi.get("name") or mi.get("twitter_name") or ""
        if name and not agg["address_label"]:
            agg["address_label"] = str(name)
        tw = mi.get("twitter_username") or ""
        if tw and not agg["twitter"]:
            agg["twitter"] = str(tw)
        for t in mi.get("tags") or []:
            ts = str(t)
            if ts and ts not in agg["gmgn_tags"]:
                agg["gmgn_tags"].append(ts)
    return by


def candidate_list(by: dict[str, dict], min_tokens: int, min_usd: float) -> list[dict]:
    out = []
    for a, agg in by.items():
        n_tok = len(agg["tokens"])
        if n_tok < min_tokens:
            continue
        if float(agg["sum_buy_usd"]) < min_usd:
            continue
        out.append(
            {
                "address": a,
                "n_buys": agg["n_buys"],
                "sum_buy_usd": round(float(agg["sum_buy_usd"]), 2),
                "n_tokens": n_tok,
                "address_label": agg["address_label"] or (f"@{agg['twitter']}" if agg["twitter"] else ""),
                "gmgn_tags": list(agg["gmgn_tags"]),
            }
        )
    out.sort(key=lambda r: (-r["sum_buy_usd"], -r["n_tokens"], -r["n_buys"]))
    return out


def parse_portfolio_row(row: dict) -> dict:
    """Map portfolio stats/profits fields into wallet record fields."""
    if not isinstance(row, dict):
        return {}
    # Flatten known nests first (GMGN stats puts WR under pnl_stat / common)
    flat = dict(row)
    for nest in ("data", "stats", "profit", "pnl", "pnl_stat", "common", "summary", "30d", "7d", "all"):
        inner = row.get(nest)
        if isinstance(inner, dict):
            for k, v in inner.items():
                if k not in flat or flat.get(k) is None:
                    flat[k] = v
            # one more level (pnl_stat.winrate etc. already copied; also nested period)
            for nest2 in ("30d", "7d", "all", "1d"):
                inner2 = inner.get(nest2)
                if isinstance(inner2, dict):
                    for k, v in inner2.items():
                        if k not in flat or flat.get(k) is None:
                            flat[k] = v

    rp = None
    for k in (
        "realized_profit",
        "realized_pnl",
        "realized_profit_usd",
        "realized_pnl_usd",
        "total_profit",
        "profit",
        "pnl",
    ):
        v = num(flat.get(k))
        if v is not None:
            rp = v
            break

    wr = None
    for k in (
        "winrate",
        "win_rate",
        "buy_success_rate",
        "profit_win_rate",
        "pnl_winrate",
        "win_ratio",
        "realized_profit_pnl",  # sometimes a ratio 0-1
    ):
        v = num(flat.get(k))
        if v is None:
            continue
        # realized_profit_pnl can be a PnL *multiple* (e.g. 2.5x) — only treat as WR if in [0,1.5]
        if k == "realized_profit_pnl" and (v < 0 or v > 1.5):
            continue
        wr = v
        if wr > 1.5:
            wr = wr / 100.0
        break
    if wr is None:
        wins = num(flat.get("win_count") or flat.get("winner") or flat.get("profit_num") or flat.get("wins"))
        total = num(flat.get("token_num") or flat.get("total_num") or flat.get("trade_num") or flat.get("total"))
        if wins is not None and total and total > 0:
            wr = float(wins) / float(total)

    nt = None
    for k in (
        "txs",
        "tx_count",
        "total_trades",
        "trade_count",
        "n_trades",
        "buy_30d",
    ):
        v = flat.get(k)
        if v is None:
            continue
        try:
            if isinstance(v, (int, float)):
                nt = int(v)
                break
            if isinstance(v, str) and v.strip().isdigit():
                nt = int(v)
                break
        except (TypeError, ValueError):
            pass
    def _count(val) -> float | None:
        if val is None:
            return None
        if isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            return num(val)
        if isinstance(val, dict):
            for ck in (
                "count", "tx", "txs", "num", "n", "buy", "sell",
                "30d", "7d", "1d", "all",
            ):
                n = _count(val.get(ck))
                if n is not None:
                    return n
            return None
        return None

    buy_n = _count(
        flat.get("buy")
        or flat.get("buy_tx_count")
        or flat.get("buy_count")
        or flat.get("buy_num")
        or flat.get("buys")
    )
    sell_n = _count(
        flat.get("sell")
        or flat.get("sell_tx_count")
        or flat.get("sell_count")
        or flat.get("sell_num")
        or flat.get("sells")
    )
    if nt is None and (buy_n is not None or sell_n is not None):
        nt = int((buy_n or 0) + (sell_n or 0))

    addr = None
    for k in ("address", "wallet_address", "walletAddress", "maker", "wallet"):
        v = flat.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            addr = v.lower()
            break
    return {
        "address": addr,
        "realized_pnl_usd": rp,
        "win_rate": wr,
        "n_trades": nt,
        "gmgn_buy": int(buy_n) if buy_n is not None else None,
        "gmgn_sell": int(sell_n) if sell_n is not None else None,
        "raw_keys": sorted(flat.keys())[:50],
    }


def _ingest_portfolio_payload(data, batch: list[str], out: dict[str, dict]) -> list[str]:
    """Parse portfolio stats/profits JSON into out; return sample raw_keys for logging."""
    sample_keys: list[str] = []
    shape = type(data).__name__
    top_keys = list(data.keys())[:20] if isinstance(data, dict) else []
    items = extract_list(data)
    if not items and isinstance(data, dict):
        for a in batch:
            if isinstance(data.get(a), dict):
                items.append({"address": a, **data[a]})
            elif isinstance(data.get(a.lower()), dict):
                items.append({"address": a, **data[a.lower()]})
        dd = data.get("data")
        if isinstance(dd, dict) and not items:
            # addr -> stats map OR single wallet object
            addr_like = [k for k in dd.keys() if isinstance(k, str) and k.lower().startswith("0x")]
            if addr_like:
                for k in addr_like:
                    v = dd[k]
                    if isinstance(v, dict):
                        items.append({"address": k, **v})
            elif any(
                k in dd
                for k in ("realized_profit", "realized_pnl", "winrate", "win_rate", "buy", "pnl_stat")
            ):
                items.append(dict(dd))
            else:
                for k, v in dd.items():
                    if isinstance(v, dict):
                        items.append({"address": k, **v} if isinstance(k, str) and k.startswith("0x") else dict(v))
                    elif k == "list" and isinstance(v, list):
                        items.extend([x for x in v if isinstance(x, dict)])
        if not items:
            if any(
                k in data
                for k in ("realized_profit", "realized_pnl", "winrate", "win_rate", "buy", "pnl_stat")
            ):
                items.append(dict(data))
    print(f"  portfolio_shape={shape} top_keys={top_keys} items={len(items)} batch={len(batch)}")
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        parsed = parse_portfolio_row(row)
        addr = parsed.get("address")
        # zip with request order when API omits address
        if not addr and i < len(batch):
            addr = batch[i]
            parsed["address"] = addr
        if not addr and len(batch) == 1:
            addr = batch[0]
            parsed["address"] = addr
        if not addr:
            continue
        addr = addr.lower()
        prev = out.get(addr) or {}
        merged = dict(prev)
        for k, v in parsed.items():
            if v is not None and k != "raw_keys":
                merged[k] = v
        if parsed.get("raw_keys"):
            merged["raw_keys"] = parsed["raw_keys"]
            if not sample_keys:
                sample_keys = list(parsed["raw_keys"])
        out[addr] = merged
    # If still only one blob for many wallets, assign to first only (API quirk) — caller may retry smaller
    if len(out) == 1 and len(batch) > 1 and batch[0] not in out:
        only = next(iter(out.values()))
        # remapped below
        pass
    if len(items) == 1 and len(batch) > 1 and len(out) <= 1:
        # single-object response: bind to batch[0]
        parsed = parse_portfolio_row(items[0] if isinstance(items[0], dict) else {})
        a0 = batch[0]
        parsed["address"] = a0
        prev = out.get(a0) or {}
        merged = dict(prev)
        for k, v in parsed.items():
            if v is not None and k != "raw_keys":
                merged[k] = v
        if parsed.get("raw_keys"):
            merged["raw_keys"] = parsed["raw_keys"]
            sample_keys = list(parsed["raw_keys"])
        out.clear()
        out[a0] = merged
        print(f"  stats_single_object_bound_to={a0[:10]}… (API ignored multi-wallet)")
    return sample_keys


def batch_vet(chain: str, addresses: list[str], remaining_cap: int) -> tuple[dict[str, dict], int, str | None]:
    """Vet wallets via portfolio stats (WR). Multi-wallet stats often returns 1 blob — use per-wallet.

    remaining_cap = max successful CLI calls. Prefer 1 wallet per call for correct WR binding.
    """
    out: dict[str, dict] = {}
    if remaining_cap <= 0 or not addresses:
        return out, 0, None
    calls = 0
    err_kind = None
    # Per-wallet stats until cap or rate limit (multi-wallet binding is unreliable)
    for a in addresses:
        if calls >= remaining_cap:
            break
        sargs = ["portfolio", "stats", "--chain", chain, "--period", "30d", "--wallet", a]
        sdata, serr = gmgn_raw(sargs, timeout=90)
        calls += 1
        if serr == "rate_limited":
            print(f"[{chain}] portfolio stats rate-limited after {len(out)} ok / tried={calls}")
            return out, calls, "rate_limited"
        if serr:
            print(f"[{chain}] stats fail {a[:10]}… {serr}")
            err_kind = serr
            continue
        before = len(out)
        keys = _ingest_portfolio_payload(sdata, [a], out)
        if a not in out:
            # force bind
            parsed = parse_portfolio_row(sdata if isinstance(sdata, dict) else {})
            if not parsed.get("raw_keys") and isinstance(sdata, dict):
                dd = sdata.get("data")
                if isinstance(dd, dict):
                    parsed = parse_portfolio_row(dd)
            parsed["address"] = a
            out[a] = {**out.get(a, {}), **{k: v for k, v in parsed.items() if v is not None}}
        wr = (out.get(a) or {}).get("win_rate")
        rp = (out.get(a) or {}).get("realized_pnl_usd")
        nt = (out.get(a) or {}).get("n_trades")
        buy_v = sell_v = None
        if isinstance(sdata, dict):
            buy_v = sdata.get("buy")
            sell_v = sdata.get("sell")
            if buy_v is None and isinstance(sdata.get("data"), dict):
                buy_v = sdata["data"].get("buy")
                sell_v = sdata["data"].get("sell")
        # last-resort: if n still None but buy/sell are numeric on wire
        bn = (out.get(a) or {}).get("gmgn_buy")
        sn = (out.get(a) or {}).get("gmgn_sell")
        if bn is None and not isinstance(buy_v, dict):
            bn = num(buy_v)
        if sn is None and not isinstance(sell_v, dict):
            sn = num(sell_v)
        if bn is not None or sn is not None:
            total = int((bn or 0) + (sn or 0))
            out[a]["gmgn_buy"] = int(bn or 0)
            out[a]["gmgn_sell"] = int(sn or 0)
            if total > 0:
                out[a]["n_trades"] = total
                nt = total
            else:
                # buy=0 sell=0 → no RH activity; do not invent n_trades
                out[a]["n_trades"] = None
                nt = None
        print(
            f"[{chain}] stats {a[:10]}… wr={wr} rp={rp} n={nt} "
            f"buy={buy_v!r}"[:120] + f" sell={sell_v!r}"[:80]
            + f" keys={keys[:6]}"
        )
        time.sleep(0.25)
    return out, calls, err_kind


def merge_wallet(existing: dict | None, cand: dict, stats: dict, chain: str) -> dict:
    now = now_iso()
    addr = cand["address"]
    short = f"{addr[:6]}...{addr[-4:]}"
    label = cand.get("address_label") or short
    rp = stats.get("realized_pnl_usd")
    wr = stats.get("win_rate")
    nt = stats.get("n_trades")

    if existing is None:
        row = {
            "address": addr,
            "address_label": label,
            "realized_pnl_usd": rp,
            "win_rate": wr,
            "n_trades": nt,
            "n_tokens": cand.get("n_tokens"),
            "tags": ["kol", "gmgn_kol"],
            "sources": ["gmgn_kol"],
            "source_endpoints": ["gmgn:track_kol"],
            "gmgn_tags": list(cand.get("gmgn_tags") or []),
            "kol_n_buys": cand.get("n_buys"),
            "kol_sum_buy_usd": cand.get("sum_buy_usd"),
            "pass_pnl": True,
            "chain": "arc" if chain == "arc" else "robinhood",
            "list_tier": "quality",
            "quality_reason": "kol_vetted_consistent",
            "collected_at": now,
            "vetted_at": now,
        }
        if chain == "arc":
            row["chain_id"] = 5042
        return row

    row = dict(existing)
    # do not wipe existing fields — only enrich
    if rp is not None:
        try:
            prev = float(row.get("realized_pnl_usd") or 0)
            row["realized_pnl_usd"] = max(prev, float(rp)) if prev else float(rp)
        except (TypeError, ValueError):
            row["realized_pnl_usd"] = rp
    if wr is not None and row.get("win_rate") is None:
        row["win_rate"] = wr
    elif wr is not None:
        try:
            # keep better of existing vs new if both present
            old = float(row.get("win_rate") or 0)
            row["win_rate"] = max(old, float(wr)) if old else float(wr)
        except (TypeError, ValueError):
            row["win_rate"] = wr
    if nt is not None:
        try:
            old_n = int(row.get("n_trades") or 0)
            row["n_trades"] = max(old_n, int(nt))
        except (TypeError, ValueError):
            row["n_trades"] = nt
    tags = list(row.get("tags") or [])
    for t in ("kol", "gmgn_kol"):
        if t not in tags:
            tags.append(t)
    for t in cand.get("gmgn_tags") or []:
        ts = str(t)
        if ts and ts not in tags:
            tags.append(ts)
    row["tags"] = tags
    srcs = list(row.get("sources") or [])
    if "gmgn_kol" not in srcs:
        srcs.append("gmgn_kol")
    row["sources"] = srcs
    se = list(row.get("source_endpoints") or [])
    if "gmgn:track_kol" not in se:
        se.append("gmgn:track_kol")
    row["source_endpoints"] = se
    if not row.get("address_label") and label:
        row["address_label"] = label
    row["kol_n_buys"] = cand.get("n_buys")
    row["kol_sum_buy_usd"] = cand.get("sum_buy_usd")
    row["pass_pnl"] = True
    row["vetted_at"] = now
    row["quality_reason"] = row.get("quality_reason") or "kol_vetted_consistent"
    return row


def load_fomo_candidates(min_pnl: float = 500.0) -> list[dict]:
    """FOMO EVM board → vet queue (highest 7d pnl first). No WR assumed."""
    by_addr: dict[str, dict] = {}
    for name in ("wallets_evm.jsonl", "leaderboard.jsonl"):
        p = ROOT / "fomo-wallets" / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                fo = json.loads(line)
            except json.JSONDecodeError:
                continue
            addr = (fo.get("address") or fo.get("evm") or "").strip().lower()
            if not addr.startswith("0x") or len(addr) != 42:
                continue
            pnl = num(fo.get("pnlUsd") or fo.get("realized_pnl_usd")) or 0.0
            handle = (fo.get("handle") or fo.get("displayName") or "").strip()
            cur = by_addr.get(addr)
            if cur is None or pnl > float(cur.get("fomo_pnl_usd") or 0):
                by_addr[addr] = {
                    "address": addr,
                    "address_label": handle or addr[:10],
                    "fomo_handle": handle,
                    "fomo_pnl_usd": pnl,
                    "n_buys": 0,
                    "sum_buy_usd": 0.0,
                    "n_tokens": 0,
                    "gmgn_tags": [],
                    "source": "fomo",
                }
    out = [v for v in by_addr.values() if float(v.get("fomo_pnl_usd") or 0) >= min_pnl]
    out.sort(key=lambda r: -float(r.get("fomo_pnl_usd") or 0))
    return out


def merge_fomo_wallet(existing: dict | None, cand: dict, stats: dict) -> dict:
    """Merge FOMO board + GMGN portfolio stats into RH watch row."""
    now = now_iso()
    addr = cand["address"]
    label = cand.get("address_label") or cand.get("fomo_handle") or f"{addr[:6]}...{addr[-4:]}"
    rp = stats.get("realized_pnl_usd")
    fomo_pnl = num(cand.get("fomo_pnl_usd"))
    # keep the stronger realized signal (FOMO 7d board vs GMGN 30d portfolio)
    if rp is None:
        rp = fomo_pnl
    elif fomo_pnl is not None:
        rp = max(float(rp), float(fomo_pnl))
    wr = stats.get("win_rate")
    nt = stats.get("n_trades")

    if existing is None:
        row = {
            "address": addr,
            "address_label": label,
            "fomo_handle": cand.get("fomo_handle") or label,
            "realized_pnl_usd": rp,
            "win_rate": wr,
            "n_trades": nt,
            "tags": ["fomo", "kol"],
            "sources": ["fomo", "gmgn_portfolio"],
            "source_endpoints": ["fomo_leaderboard", "gmgn:portfolio"],
            "pass_pnl": True,
            "chain": "robinhood",
            "list_tier": "quality",
            "quality_reason": "fomo_vetted_consistent",
            "collected_at": now,
            "vetted_at": now,
            "fomo_pnl_usd": fomo_pnl,
        }
        return row

    row = dict(existing)
    if rp is not None:
        try:
            prev = float(row.get("realized_pnl_usd") or 0)
            row["realized_pnl_usd"] = max(prev, float(rp)) if prev else float(rp)
        except (TypeError, ValueError):
            row["realized_pnl_usd"] = rp
    if wr is not None:
        if row.get("win_rate") is None:
            row["win_rate"] = wr
        else:
            try:
                old = float(row.get("win_rate") or 0)
                row["win_rate"] = max(old, float(wr)) if old else float(wr)
            except (TypeError, ValueError):
                row["win_rate"] = wr
    if nt is not None:
        try:
            old_n = int(row.get("n_trades") or 0)
            row["n_trades"] = max(old_n, int(nt))
        except (TypeError, ValueError):
            row["n_trades"] = nt
    if cand.get("fomo_handle"):
        row["fomo_handle"] = cand["fomo_handle"]
    if not row.get("address_label") and label:
        row["address_label"] = label
    tags = list(row.get("tags") or [])
    for t in ("fomo", "kol"):
        if t not in tags:
            tags.append(t)
    row["tags"] = tags
    srcs = list(row.get("sources") or [])
    for s in ("fomo", "gmgn_portfolio"):
        if s not in srcs:
            srcs.append(s)
    row["sources"] = srcs
    se = list(row.get("source_endpoints") or [])
    for ep in ("fomo_leaderboard", "gmgn:portfolio"):
        if ep not in se:
            se.append(ep)
    row["source_endpoints"] = se
    row["pass_pnl"] = True
    row["vetted_at"] = now
    row["fomo_pnl_usd"] = fomo_pnl
    row["quality_reason"] = row.get("quality_reason") or "fomo_vetted_consistent"
    return row


def tag_fomo_already_consistent(rh: dict[str, dict], fomo_cands: list[dict], min_realized: float) -> int:
    """Tag FOMO addrs already in RH that already pass consistency — no API."""
    fomo_addrs = {c["address"] for c in fomo_cands}
    tagged = 0
    for addr in fomo_addrs:
        cur = rh.get(addr)
        if not cur:
            continue
        if cur.get("win_rate") is None and cur.get("gmgn_winrate") is None:
            continue
        if not bot.wallet_passes_filter(cur, min_realized):
            continue
        tags = list(cur.get("tags") or [])
        changed = False
        for t in ("kol", "fomo"):
            if t not in tags:
                tags.append(t)
                changed = True
        srcs = list(cur.get("sources") or [])
        if "fomo" not in srcs:
            srcs.append("fomo")
            changed = True
        se = list(cur.get("source_endpoints") or [])
        if "fomo_leaderboard" not in se:
            se.append("fomo_leaderboard")
            changed = True
        if changed:
            cur["tags"] = tags
            cur["sources"] = srcs
            cur["source_endpoints"] = se
            if not cur.get("fomo_handle"):
                for c in fomo_cands:
                    if c["address"] == addr and c.get("fomo_handle"):
                        cur["fomo_handle"] = c["fomo_handle"]
                        break
            rh[addr] = cur
            tagged += 1
    return tagged


def main() -> int:
    limit = int(os.environ.get("KOL_LIMIT", "150"))
    vet_cap = int(os.environ.get("KOL_VET_CAP", "30"))
    fomo_vet_cap = int(os.environ.get("FOMO_VET_CAP", "15"))
    min_tokens = int(os.environ.get("KOL_MIN_TOKENS", "2"))
    min_usd = float(os.environ.get("KOL_MIN_BUY_USD", "200"))
    min_realized = float(os.environ.get("WATCH_MIN_REALIZED_HARD", "500"))
    skip_fetch = (os.environ.get("KOL_SKIP_FETCH") or "0").strip().lower() in ("1", "true", "yes")
    skip_vet = (os.environ.get("KOL_SKIP_VET") or "0").strip().lower() in ("1", "true", "yes")
    chains_raw = os.environ.get("KOL_CHAINS", "robinhood,arc")
    chains = []
    for c in chains_raw.split(","):
        c = c.strip().lower()
        if c in ("rh", "robinhood"):
            chains.append("robinhood")
        elif c == "arc":
            chains.append("arc")
    if not chains:
        chains = ["robinhood"]

    print(
        f"harvest_kol_vetted limit={limit} vet_cap={vet_cap} fomo_vet_cap={fomo_vet_cap} "
        f"min_tokens={min_tokens} min_usd={min_usd} chains={chains} "
        f"skip_fetch={skip_fetch} skip_vet={skip_vet}"
    )

    remaining = vet_cap
    summary = {
        "kol_candidates": 0,
        "fomo_candidates": 0,
        "vetted": 0,
        "passed": 0,
        "added_rh": 0,
        "updated_rh": 0,
        "added_arc": 0,
        "updated_arc": 0,
        "fomo_tagged": 0,
        "fomo_added": 0,
        "fomo_updated": 0,
        "fomo_passed": 0,
        "rate_limited": False,
        "track_rate_limited": False,
    }

    rh_path = ROOT / "rh-wallets" / "wallets.jsonl"
    arc_path = ROOT / "arc-wallets" / "wallets.jsonl"
    arc_q_path = ROOT / "arc-wallets" / "wallets_quality.jsonl"
    rh = load_jsonl(rh_path)
    arc = load_jsonl(arc_path)
    arc_q = load_jsonl(arc_q_path) if arc_q_path.exists() else dict(arc)

    fomo_cands = load_fomo_candidates(min_pnl=min_realized)
    summary["fomo_candidates"] = len(fomo_cands)
    summary["fomo_tagged"] = tag_fomo_already_consistent(rh, fomo_cands, min_realized)

    # Build per-chain KOL candidates first (fetch/reuse raw)
    kol_by_chain: dict[str, list[dict]] = {}
    for chain in chains:
        cdir = chain_dir(chain)
        raw_dir = cdir / "raw"
        if skip_fetch:
            rows = load_raw_rows(raw_dir)
            print(f"[{chain}] skip_fetch reuse raw buys={len(rows)}")
        else:
            fetched, ferr = fetch_kol_track(chain, limit, raw_dir)
            if ferr == "rate_limited":
                summary["track_rate_limited"] = True
                print(f"[{chain}] track rate-limited; continue with raw/vet")
            rows = load_raw_rows(raw_dir)
            if not rows and fetched:
                rows = [r for r in fetched if (r.get("side") or "buy").lower() == "buy"]
        by = aggregate_makers(rows)
        cands = candidate_list(by, min_tokens, min_usd)
        kol_by_chain[chain] = cands
        summary["kol_candidates"] += len(cands)
        print(f"[{chain}] makers={len(by)} candidates={len(cands)}")
        for c in cands[:12]:
            print(
                f"  cand {c['address'][:10]}… buys={c['n_buys']} "
                f"usd={c['sum_buy_usd']:.0f} toks={c['n_tokens']} "
                f"label={c['address_label']!r}"
            )

    # --- Single RH vet batch: FOMO (top) + KOL need_vet (stats-first, 1 GMGN call) ---
    rh_kol = kol_by_chain.get("robinhood") or []
    kol_need: list[dict] = []
    kol_already: list[tuple[dict, dict]] = []
    for c in rh_kol:
        cur = rh.get(c["address"])
        if cur and bot.wallet_passes_filter(cur, min_realized):
            kol_already.append((c, cur))
        else:
            kol_need.append(c)

    fomo_need: list[dict] = []
    for c in fomo_cands:
        cur = rh.get(c["address"])
        if cur and bot.wallet_passes_filter(cur, min_realized):
            continue
        fomo_need.append(c)

    # Dedupe addresses; FOMO first (board PnL ranked), then KOL
    seen_addr: set[str] = set()
    combined: list[tuple[str, dict]] = []  # (kind, cand)
    for c in fomo_need[:fomo_vet_cap]:
        a = c["address"]
        if a in seen_addr:
            continue
        seen_addr.add(a)
        combined.append(("fomo", c))
    for c in kol_need:
        a = c["address"]
        if a in seen_addr:
            continue
        seen_addr.add(a)
        combined.append(("kol", c))

    # Cap total wallets in the one stats batch (credit/429 awareness)
    max_batch = min(len(combined), max(vet_cap, fomo_vet_cap), 40)
    combined = combined[:max_batch]
    print(
        f"[rh-vet] queue fomo_need={len(fomo_need)} kol_need={len(kol_need)} "
        f"batch={len(combined)} already_ok_kol={len(kol_already)} "
        f"fomo_tagged={summary['fomo_tagged']}"
    )

    stats_map: dict[str, dict] = {}
    if not skip_vet and combined and remaining > 0:
        addrs = [c["address"] for _, c in combined]
        # FOMO EVM may be idle on robinhood — try RH then base then eth per wallet until budget gone
        for idx, a in enumerate(addrs):
            if remaining <= 0 or summary["rate_limited"]:
                break
            kind = combined[idx][0] if idx < len(combined) else "kol"
            chains_try = ["base", "eth", "robinhood"] if kind == "fomo" else ["robinhood"]
            got = None
            for ch in chains_try:
                if remaining <= 0:
                    break
                sm, used, verr = batch_vet(ch, [a], 1)
                remaining -= used
                summary["vetted"] += len(sm)
                if verr == "rate_limited":
                    summary["rate_limited"] = True
                    break
                st = sm.get(a)
                if not st:
                    continue
                b = st.get("gmgn_buy")
                s = st.get("gmgn_sell")
                active = (b is not None or s is not None) and int((b or 0) + (s or 0)) > 0
                # prefer first chain with real trades; else keep best WR so far
                if active and st.get("win_rate") is not None:
                    st["vet_chain"] = ch
                    got = st
                    break
                if got is None and st.get("win_rate") is not None:
                    st["vet_chain"] = ch
                    got = st
            if got:
                stats_map[a] = got
        print(f"[rh-vet] stats_map={len(stats_map)} remaining={remaining} rate_limited={summary['rate_limited']}")
    elif skip_vet:
        print("[rh-vet] skip_vet")

    # Apply already-ok KOL tag merges
    for c, cur in kol_already:
        st = {
            "realized_pnl_usd": cur.get("realized_pnl_usd"),
            "win_rate": cur.get("win_rate") or cur.get("gmgn_winrate"),
            "n_trades": cur.get("n_trades"),
        }
        prev = rh.get(c["address"])
        was = prev is not None
        rh[c["address"]] = merge_wallet(prev, c, st, "robinhood")
        summary["passed"] += 1
        if was:
            summary["updated_rh"] += 1
        else:
            summary["added_rh"] += 1

    for kind, c in combined:
        st = stats_map.get(c["address"])
        if not st:
            continue
        if kind == "fomo":
            rp = st.get("realized_pnl_usd")
            fp = num(c.get("fomo_pnl_usd"))
            if rp is None:
                rp = fp or 0
            elif fp is not None:
                rp = max(float(rp), float(fp))
            trial = {
                "address": c["address"],
                "realized_pnl_usd": rp or 0,
                "win_rate": st.get("win_rate"),
                "n_trades": st.get("n_trades") or 0,
                "tags": ["fomo", "kol"],
                "sources": ["fomo", "gmgn_portfolio"],
                "source_endpoints": ["fomo_leaderboard", "gmgn:portfolio"],
                "fomo_handle": c.get("fomo_handle"),
            }
            buy_n = st.get("gmgn_buy")
            sell_n = st.get("gmgn_sell")
            real_n = st.get("n_trades")
            if (buy_n is not None or sell_n is not None) and int((buy_n or 0) + (sell_n or 0)) <= 0:
                real_n = None
                trial["n_trades"] = 0
            ok = (
                real_n is not None
                and int(real_n) >= 15
                and bot.wallet_passes_filter(trial, min_realized)
                and bot.wallet_is_consistent(trial)
            )
            print(
                f"  fomo-vet {c['address'][:10]}… rp={rp} wr={st.get('win_rate')} "
                f"n={real_n} buy={buy_n} sell={sell_n} board={c.get('fomo_pnl_usd')} pass={ok}"
            )
            if not ok:
                continue
            summary["fomo_passed"] += 1
            summary["passed"] += 1
            prev = rh.get(c["address"])
            was = prev is not None
            rh[c["address"]] = merge_fomo_wallet(prev, c, {**st, "realized_pnl_usd": rp})
            if was:
                summary["fomo_updated"] += 1
                summary["updated_rh"] += 1
            else:
                summary["fomo_added"] += 1
                summary["added_rh"] += 1
        else:
            trial = {
                "address": c["address"],
                "realized_pnl_usd": st.get("realized_pnl_usd") or 0,
                "win_rate": st.get("win_rate"),
                "n_trades": st.get("n_trades") or 0,
                "n_tokens": c.get("n_tokens"),
                "tags": ["kol", "gmgn_kol"] + list(c.get("gmgn_tags") or []),
                "sources": ["gmgn_kol"],
                "source_endpoints": ["gmgn:track_kol"],
            }
            ok = bot.wallet_passes_filter(trial, min_realized) and bot.wallet_is_consistent(trial)
            print(
                f"  kol-vet {c['address'][:10]}… rp={st.get('realized_pnl_usd')} "
                f"wr={st.get('win_rate')} n={st.get('n_trades')} pass={ok}"
            )
            if not ok:
                continue
            summary["passed"] += 1
            prev = rh.get(c["address"])
            was = prev is not None
            rh[c["address"]] = merge_wallet(prev, c, st, "robinhood")
            if was:
                summary["updated_rh"] += 1
            else:
                summary["added_rh"] += 1

    # --- Arc KOL (separate small stats batch if budget left) ---
    arc_kol = kol_by_chain.get("arc") or []
    if arc_kol and not skip_vet and remaining > 0 and not summary["rate_limited"]:
        need = []
        already = []
        for c in arc_kol:
            cur = arc.get(c["address"])
            if cur and bot.wallet_passes_filter(cur, min_realized):
                already.append((c, cur))
            else:
                need.append(c)
        for c, cur in already:
            st = {
                "realized_pnl_usd": cur.get("realized_pnl_usd"),
                "win_rate": cur.get("win_rate") or cur.get("gmgn_winrate"),
                "n_trades": cur.get("n_trades"),
            }
            merged = merge_wallet(arc.get(c["address"]), c, st, "arc")
            arc[c["address"]] = merged
            arc_q[c["address"]] = merge_wallet(arc_q.get(c["address"]), c, st, "arc")
            summary["passed"] += 1
            summary["updated_arc"] += 1
        if need:
            addrs = [c["address"] for c in need[: min(20, len(need))]]
            sm, used, verr = batch_vet("arc", addrs, min(remaining, 5))
            remaining -= used
            summary["vetted"] += len(sm)
            if verr == "rate_limited":
                summary["rate_limited"] = True
            for c in need:
                st = sm.get(c["address"])
                if not st:
                    continue
                trial = {
                    "address": c["address"],
                    "realized_pnl_usd": st.get("realized_pnl_usd") or 0,
                    "win_rate": st.get("win_rate"),
                    "n_trades": st.get("n_trades") or 0,
                    "n_tokens": c.get("n_tokens"),
                    "tags": ["kol", "gmgn_kol"] + list(c.get("gmgn_tags") or []),
                    "sources": ["gmgn_kol"],
                    "source_endpoints": ["gmgn:track_kol"],
                }
                ok = bot.wallet_passes_filter(trial, min_realized)
                # Arc multi-hit: n_tokens from KOL buys can satisfy Path B when WR/trades thin
                if not ok and trial.get("win_rate") is None:
                    trial2 = dict(trial)
                    trial2["n_tokens"] = c.get("n_tokens") or 0
                    # Path B needs nt < min_n — if portfolio gave huge nt without WR, still fail
                    ok = bot.wallet_passes_filter(trial2, min_realized)
                print(
                    f"  arc-vet {c['address'][:10]}… rp={st.get('realized_pnl_usd')} "
                    f"wr={st.get('win_rate')} n={st.get('n_trades')} toks={c.get('n_tokens')} pass={ok}"
                )
                if not ok:
                    continue
                summary["passed"] += 1
                prev = arc.get(c["address"])
                was = prev is not None
                merged = merge_wallet(prev, c, st, "arc")
                arc[c["address"]] = merged
                arc_q[c["address"]] = merge_wallet(arc_q.get(c["address"]), c, st, "arc")
                if was:
                    summary["updated_arc"] += 1
                else:
                    summary["added_arc"] += 1
    elif not arc_kol:
        print("[arc] no KOL candidates (need track raw or live fetch)")

    summary["candidates"] = summary["kol_candidates"] + summary["fomo_candidates"]

    if (
        summary["added_rh"]
        or summary["updated_rh"]
        or summary["fomo_tagged"]
        or summary["fomo_added"]
        or summary["fomo_updated"]
    ):
        write_jsonl(rh_path, rh)
    if summary["added_arc"] or summary["updated_arc"]:
        write_jsonl(arc_path, arc)
        write_jsonl(arc_q_path, arc_q)

    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

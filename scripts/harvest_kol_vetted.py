#!/usr/bin/env python3
"""Harvest GMGN KOL buy tracks, aggregate makers, batch-vet, merge consistent winners.

Credit-light defaults:
  KOL_LIMIT=100          track page size
  KOL_VET_CAP=20         max portfolio calls across chains
  KOL_MIN_TOKENS=2       distinct tokens bought
  KOL_MIN_BUY_USD=200    sum buy USD floor
  KOL_CHAINS=robinhood,arc
  KOL_SKIP_FETCH=0       set 1 to reuse raw only (no track API)
  KOL_SKIP_VET=0         set 1 to skip portfolio calls
  REQUIRE_CONSISTENT_PNL / WATCH_MIN_* — imported filters from bot.py

Mass KOL = noise. Only keep average/repeat winners that pass wallet_passes_filter.
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
    # nested wrappers
    for nest in ("data", "stats", "profit", "pnl"):
        inner = row.get(nest)
        if isinstance(inner, dict) and (
            any(k in inner for k in ("realized_profit", "realized_pnl", "winrate", "win_rate", "profit"))
        ):
            # prefer flattening useful fields from nest into a copy
            merged = {**row, **inner}
            row = merged
            break

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
        v = num(row.get(k))
        if v is not None:
            rp = v
            break
    # nested period objects e.g. {"30d": {"realized_profit": ...}}
    if rp is None:
        for pk in ("30d", "7d", "all", "1d"):
            d = row.get(pk)
            if isinstance(d, dict):
                for k in ("realized_profit", "realized_pnl", "profit", "pnl"):
                    v = num(d.get(k))
                    if v is not None:
                        rp = v
                        break
            if rp is not None:
                break

    wr = None
    for k in ("winrate", "win_rate", "buy_success_rate", "profit_win_rate"):
        v = num(row.get(k))
        if v is not None:
            wr = v
            if wr > 1.5:
                wr = wr / 100.0
            break

    nt = None
    for k in (
        "buy",
        "txs",
        "tx_count",
        "total_trades",
        "trade_count",
        "n_trades",
        "buy_30d",
        "history_bought_cost",
    ):
        v = row.get(k)
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
    # buy+sell counts
    if nt is None:
        b = num(row.get("buy_tx_count") or row.get("buy_count"))
        s = num(row.get("sell_tx_count") or row.get("sell_count"))
        if b is not None or s is not None:
            nt = int((b or 0) + (s or 0))

    addr = None
    for k in ("address", "wallet_address", "walletAddress", "maker"):
        v = row.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            addr = v.lower()
            break
    return {
        "address": addr,
        "realized_pnl_usd": rp,
        "win_rate": wr,
        "n_trades": nt,
        "raw_keys": sorted(row.keys())[:40],
    }


def batch_vet(chain: str, addresses: list[str], remaining_cap: int) -> tuple[dict[str, dict], int, str | None]:
    """Call portfolio profits (batched) then fill gaps with portfolio stats. Returns stats, calls_used, err."""
    out: dict[str, dict] = {}
    if remaining_cap <= 0 or not addresses:
        return out, 0, None
    # prefer one profits call for many wallets
    batch = addresses[: min(len(addresses), remaining_cap, 50)]
    calls = 0
    err_kind = None

    # profits — one call counts as 1 toward cap (batch)
    args = ["portfolio", "profits", "--chain", chain, "--period", "30d"]
    for a in batch:
        args.extend(["--wallet", a])
    data, err = gmgn_raw(args, timeout=180)
    calls += 1
    if err == "rate_limited":
        return out, calls, "rate_limited"
    if err:
        print(f"[{chain}] portfolio profits fail: {err}")
        err_kind = err
    else:
        items = extract_list(data)
        if not items and isinstance(data, dict):
            # map keyed by address
            for a in batch:
                if isinstance(data.get(a), dict):
                    items.append({"address": a, **data[a]})
                elif isinstance(data.get(a.lower()), dict):
                    items.append({"address": a, **data[a.lower()]})
            # sometimes data.data is dict addr->stats
            dd = data.get("data")
            if isinstance(dd, dict) and not items:
                for k, v in dd.items():
                    if isinstance(v, dict):
                        items.append({"address": k, **v})
        for row in items:
            parsed = parse_portfolio_row(row if isinstance(row, dict) else {})
            addr = parsed.get("address")
            if not addr:
                continue
            out[addr] = parsed
        print(f"[{chain}] profits parsed={len(out)} / batch={len(batch)}")

    # fill missing with stats (each wallet or small batches) — count each call
    missing = [a for a in batch if a not in out or out[a].get("realized_pnl_usd") is None]
    for a in missing:
        if calls >= remaining_cap:
            break
        sargs = ["portfolio", "stats", "--chain", chain, "--wallet", a, "--period", "30d"]
        sdata, serr = gmgn_raw(sargs, timeout=90)
        calls += 1
        if serr == "rate_limited":
            return out, calls, "rate_limited"
        if serr or sdata is None:
            print(f"[{chain}] stats fail {a[:10]}… {serr}")
            continue
        # stats may return list or dict
        rows = extract_list(sdata)
        target = None
        if rows:
            target = rows[0] if isinstance(rows[0], dict) else None
        elif isinstance(sdata, dict):
            target = sdata.get("data") if isinstance(sdata.get("data"), dict) else sdata
        if not isinstance(target, dict):
            continue
        parsed = parse_portfolio_row({**target, "address": a})
        parsed["address"] = a
        out[a] = parsed
        time.sleep(0.4)

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


def tag_fomo_consistent(rh: dict[str, dict], min_realized: float) -> int:
    """Tag FOMO addrs already in RH with wr that pass consistency — no new API calls."""
    fomo_path = ROOT / "fomo-wallets" / "wallets_evm.jsonl"
    if not fomo_path.exists():
        return 0
    tagged = 0
    for line in fomo_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            fo = json.loads(line)
        except json.JSONDecodeError:
            continue
        addr = (fo.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
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
        for ep in ("fomo_leaderboard", "gmgn:track_kol"):
            if ep not in se:
                # only add fomo_leaderboard; kol endpoint only if already kol-ish
                if ep == "fomo_leaderboard" and ep not in se:
                    se.append(ep)
                    changed = True
        if changed:
            cur["tags"] = tags
            cur["sources"] = srcs
            cur["source_endpoints"] = se
            rh[addr] = cur
            tagged += 1
    return tagged


def main() -> int:
    limit = int(os.environ.get("KOL_LIMIT", "100"))
    vet_cap = int(os.environ.get("KOL_VET_CAP", "20"))
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
        f"harvest_kol_vetted limit={limit} vet_cap={vet_cap} "
        f"min_tokens={min_tokens} min_usd={min_usd} chains={chains} "
        f"skip_fetch={skip_fetch} skip_vet={skip_vet}"
    )

    remaining = vet_cap
    summary = {
        "candidates": 0,
        "vetted": 0,
        "passed": 0,
        "added_rh": 0,
        "updated_rh": 0,
        "added_arc": 0,
        "updated_arc": 0,
        "fomo_tagged": 0,
        "rate_limited": False,
    }

    rh_path = ROOT / "rh-wallets" / "wallets.jsonl"
    arc_path = ROOT / "arc-wallets" / "wallets.jsonl"
    arc_q_path = ROOT / "arc-wallets" / "wallets_quality.jsonl"
    rh = load_jsonl(rh_path)
    arc = load_jsonl(arc_path)
    arc_q = load_jsonl(arc_q_path) if arc_q_path.exists() else dict(arc)

    for chain in chains:
        cdir = chain_dir(chain)
        raw_dir = cdir / "raw"
        if skip_fetch:
            rows = load_raw_rows(raw_dir)
            print(f"[{chain}] skip_fetch reuse raw buys={len(rows)}")
        else:
            fetched, ferr = fetch_kol_track(chain, limit, raw_dir)
            if ferr == "rate_limited":
                summary["rate_limited"] = True
            # always merge with any other local raw (kol_buy etc.)
            rows = load_raw_rows(raw_dir)
            if not rows and fetched:
                rows = [r for r in fetched if (r.get("side") or "buy").lower() == "buy"]

        by = aggregate_makers(rows)
        cands = candidate_list(by, min_tokens, min_usd)
        summary["candidates"] += len(cands)
        print(f"[{chain}] makers={len(by)} candidates={len(cands)}")
        for c in cands[:15]:
            print(
                f"  cand {c['address'][:10]}… buys={c['n_buys']} "
                f"usd={c['sum_buy_usd']:.0f} toks={c['n_tokens']} "
                f"label={c['address_label']!r}"
            )

        if not cands:
            continue

        # Prefer not already in watch with good stats; still re-vet unknowns first
        need_vet = []
        already_ok = []
        watch = rh if chain == "robinhood" else arc
        for c in cands:
            cur = watch.get(c["address"])
            if cur and bot.wallet_passes_filter(cur, min_realized):
                already_ok.append((c, cur))
            else:
                need_vet.append(c)

        stats_map: dict[str, dict] = {}
        if not skip_vet and need_vet and remaining > 0:
            addrs = [c["address"] for c in need_vet]
            # budget: leave some for other chain
            use = remaining if chain == chains[-1] else max(1, remaining // max(1, len(chains) - chains.index(chain)))
            use = min(use, remaining, len(addrs))
            sm, used, verr = batch_vet(chain, addrs[:use], use)
            remaining -= used
            summary["vetted"] += len(sm)
            if verr == "rate_limited":
                summary["rate_limited"] = True
                print(f"[{chain}] rate-limited during vet; stop further calls")
            stats_map.update(sm)
        elif skip_vet:
            print(f"[{chain}] skip_vet")

        passed_rows: list[tuple[dict, dict]] = []
        for c, cur in already_ok:
            # already consistent — merge tags only, use existing stats
            st = {
                "realized_pnl_usd": cur.get("realized_pnl_usd"),
                "win_rate": cur.get("win_rate") or cur.get("gmgn_winrate"),
                "n_trades": cur.get("n_trades"),
            }
            passed_rows.append((c, st))

        for c in need_vet:
            st = stats_map.get(c["address"])
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
            # map gmgn fields for filter helpers
            if trial["win_rate"] is None and st.get("win_rate") is not None:
                trial["gmgn_winrate"] = st["win_rate"]
            ok = bot.wallet_passes_filter(trial, min_realized) and bot.wallet_is_consistent(trial)
            print(
                f"  vet {c['address'][:10]}… rp={st.get('realized_pnl_usd')} "
                f"wr={st.get('win_rate')} n={st.get('n_trades')} pass={ok}"
            )
            if ok:
                passed_rows.append((c, st))

        summary["passed"] += len(passed_rows)

        if chain == "robinhood":
            for c, st in passed_rows:
                prev = rh.get(c["address"])
                was = prev is not None
                rh[c["address"]] = merge_wallet(prev, c, st, chain)
                if was:
                    summary["updated_rh"] += 1
                else:
                    summary["added_rh"] += 1
        else:
            for c, st in passed_rows:
                prev = arc.get(c["address"])
                was = prev is not None
                merged = merge_wallet(prev, c, st, chain)
                arc[c["address"]] = merged
                # signal-arc prefers wallets_quality.jsonl
                prev_q = arc_q.get(c["address"])
                arc_q[c["address"]] = merge_wallet(prev_q, c, st, chain)
                if was:
                    summary["updated_arc"] += 1
                else:
                    summary["added_arc"] += 1

        if summary["rate_limited"]:
            break

    # FOMO: only tag addresses already in RH with wr that pass filters
    summary["fomo_tagged"] = tag_fomo_consistent(rh, min_realized)

    # Only rewrite files we actually touched (avoid noisy reorder commits)
    if summary["added_rh"] or summary["updated_rh"] or summary["fomo_tagged"]:
        write_jsonl(rh_path, rh)
    if summary["added_arc"] or summary["updated_arc"]:
        write_jsonl(arc_path, arc)
        write_jsonl(arc_q_path, arc_q)

    # early list: do not auto-add bare kol as early
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

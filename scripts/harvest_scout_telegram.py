#!/usr/bin/env python3
"""Scrape t.me/s/scoutrobinhood EARLY CALL Live buys → resolve trunc wallets via GMGN.

Env:
  SCOUT_TG_ENABLED=1     master switch (default 1 when run directly)
  SCOUT_TG_PAGES=5       how many ?before= pages to walk (0 = resolve-only, keep disk trunc)
  SCOUT_TG_RESOLVE=1     resolve 0xABCD…WXYZ via local/BS/GMGN
  SCOUT_TG_VET_CAP=40    max GMGN token-traders calls this run (GHA only)
  SCOUT_TG_MAX_TOKENS=8  max distinct token CAs for GMGN traders
  SCOUT_TG_BS_PAGES=20   Blockscout holders/transfers/tokentx page depth
  SCOUT_TG_BS_TOKEN_CAP  max BS fetch/deepen tokens per run (default 80; -1 = unlimited)
  SCOUT_TG_BS_TIME_BUDGET_SEC  stop NEW BS fetches after N seconds (default 1200; 0 = off)
  SCOUT_TG_BS_ALL=1      fetch unresolved tokens (still respects TOKEN_CAP / TIME_BUDGET)
  SCOUT_TG_BLOCKSCOUT=1  enable Blockscout token-scoped resolve
  SCOUT_TG_MULTI_UNION=1 union transfer graphs for pairs on ≥2 tokens
  SCOUT_TG_MEGA_UNION=1  unique-match against union of ALL token pools
  SCOUT_TG_BS_DEEPEN_MISS=1 re-fetch BS when reused pool misses pending truncs (counts toward TOKEN_CAP)
  SCOUT_TG_ELITE_ONLY=0  if 1, only resolve 💎 elite truncs
  SCOUT_TG_SKIP_WELL_RESOLVED=0.8  skip GMGN for tokens with >= this fraction resolved
  SCOUT_TG_BS_SLEEP_MS=400  pause between Blockscout token fetches
  GMGN_DISABLED=1        skip GMGN (box IP ban); local+Blockscout still run
  CHAIN=robinhood
  WATCHLIST_PATH=rh-wallets/wallets.jsonl

Resolve order (unique match only — never invent addresses):
  1) pair-level resolve cache (prefix|suffix → addr) applied across tokens
  2) token-scoped unique match (holders/transfers/traders of that token_ca)
  3) multi-token union unique match (pairs seen on ≥2 tokens)
  4) mega-union unique match across ALL token pools (no false positives)
  5) global unique match against expanded local pool
  6) optional: 2 global hits but only 1 in token set → accept that one

Writes:
  rh-wallets/raw/scout_tg_posts.jsonl
  rh-wallets/raw/scout_tg_trunc.jsonl
  rh-wallets/raw/scout_tg_hits.jsonl
  rh-wallets/raw/scout_tg_resolve_cache.jsonl
  rh-wallets/raw/scout_tg_token_pools.jsonl
  rh-wallets/raw/scout_tg_unresolved.jsonl
  rh-wallets/summary_scout_resolve.md
  merges resolved full addrs into WATCHLIST_PATH (no wipe)

Deep harvest tip: chunked BS — SCOUT_TG_BS_ALL=1 SCOUT_TG_BS_TOKEN_CAP=80 SCOUT_TG_BS_TIME_BUDGET_SEC=1500 SCOUT_TG_PAGES=0 (resolve-only) on GHA.
"""
from __future__ import annotations

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
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "rh-wallets" / "raw"
POSTS_PATH = RAW_DIR / "scout_tg_posts.jsonl"
TRUNC_PATH = RAW_DIR / "scout_tg_trunc.jsonl"
HITS_PATH = RAW_DIR / "scout_tg_hits.jsonl"
RESOLVE_CACHE = RAW_DIR / "scout_tg_resolve_cache.jsonl"
TOKEN_POOLS_PATH = RAW_DIR / "scout_tg_token_pools.jsonl"
UNRESOLVED_PATH = RAW_DIR / "scout_tg_unresolved.jsonl"
RESOLVE_SUMMARY_PATH = ROOT / "rh-wallets" / "summary_scout_resolve.md"
BASE_URL = "https://t.me/s/scoutrobinhood"
UA = "Mozilla/5.0 (compatible; ScoutTGHarvester/1.0; +https://github.com/meme-foundation)"

CA_RE = re.compile(r"0x[a-fA-F0-9]{40}")
TRUNC_RE = re.compile(
    r"(?P<tier_emoji>[💎✅])\s*\$?(?P<usd>[\d,.]+[kKmM]?)\s*[·•.\-–—]\s*"
    r"(?:<code>)?(?P<trunc>0x[a-fA-F0-9]{2,8}\s*[…\.…]{1,3}\s*[a-fA-F0-9]{2,8})(?:</code>)?",
    re.U,
)
TRUNC_PLAIN_RE = re.compile(
    r"(?P<tier>elite|good|💎|✅)\s*\$?(?P<usd>[\d,.]+[kKmM]?)\s*[·•.\-–—]\s*"
    r"(?P<trunc>0x[a-fA-F0-9]{2,8}\s*[…\.]{1,3}\s*[a-fA-F0-9]{2,8})",
    re.I,
)
TICKER_RE = re.compile(r"EARLY\s+CALL\s*[—\-–]\s*\$?([A-Za-z0-9_]{1,32})", re.I)
HIT_RE = re.compile(r"\$?([A-Za-z0-9_]{1,32})\s+hit\s+([\d.]+)\s*[xX]", re.I)
MSG_SPLIT_RE = re.compile(
    r'<div[^>]*class="tgme_widget_message[^"]*"[^>]*data-post="scoutrobinhood/(\d+)"[^>]*>',
    re.I,
)


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_usd(s: str) -> float | None:
    try:
        t = str(s).replace("$", "").replace(",", "").strip()
        if not t:
            return None
        mult = 1.0
        if t[-1] in "kK":
            mult = 1_000.0
            t = t[:-1]
        elif t[-1] in "mM":
            mult = 1_000_000.0
            t = t[:-1]
        return float(t) * mult
    except Exception:
        return None


def html_to_plain(chunk: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
    t = re.sub(r"</p>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return unescape(t)


def extract_token_ca(chunk: str) -> str | None:
    # Prefer gmgn / dexscreener / geckoterminal / basedbot start links
    for pat in (
        r"gmgn\.ai/robinhood/token/(?:scout_)?(0x[a-fA-F0-9]{40})",
        r"dexscreener\.com/robinhood/(0x[a-fA-F0-9]{40})",
        r"geckoterminal\.com/robinhood/tokens/(0x[a-fA-F0-9]{40})",
        r"based_eth_bot\?start=r_scout_b_(0x[a-fA-F0-9]{40})",
        r"x\.com/search\?q=(0x[a-fA-F0-9]{40})",
    ):
        m = re.search(pat, chunk, re.I)
        if m:
            return m.group(1).lower()
    # fallback: any full CA in hrefs (not trunc)
    for m in CA_RE.finditer(chunk):
        addr = m.group(0).lower()
        # skip if it looks like it's inside a trunc code span context — rare
        return addr
    return None


def parse_trunc_parts(trunc: str) -> tuple[str, str] | None:
    t = trunc.lower().replace(" ", "")
    t = t.replace("…", "...").replace("‥", "...").replace("⋯", "...")
    if "..." in t:
        pre, suf = t.split("...", 1)
    elif ".." in t:
        pre, suf = t.split("..", 1)
    else:
        # unicode ellipsis already normalized; bare mid-dot?
        m = re.match(r"(0x[a-f0-9]+).+([a-f0-9]+)$", t)
        if not m:
            return None
        pre, suf = m.group(1), m.group(2)
    if not pre.startswith("0x"):
        pre = "0x" + pre
    pre = pre[:6]  # 0x + 4 hex typical
    suf = suf[-4:]
    if len(pre) < 4 or len(suf) < 2:
        return None
    return pre, suf


def parse_live_buys(plain: str, html_chunk: str) -> list[dict]:
    rows: list[dict] = []
    # Prefer HTML with emoji markers near <code>
    for m in re.finditer(
        r"([💎✅])[^<]{0,40}?\$?\s*([\d,.]+[kKmM]?)\s*[·•.\-–—]\s*<code>\s*(0x[a-fA-F0-9]{2,8}\s*[…\.]{1,3}\s*[a-fA-F0-9]{2,8})\s*</code>",
        html_chunk,
        re.U,
    ):
        emoji, usd_s, trunc = m.group(1), m.group(2), m.group(3)
        tier = "elite" if emoji == "💎" else "good"
        parts = parse_trunc_parts(trunc)
        if not parts:
            continue
        rows.append(
            {
                "trunc": trunc.replace(" ", ""),
                "prefix": parts[0],
                "suffix": parts[1],
                "usd": parse_usd(usd_s),
                "tier": tier,
            }
        )
    if rows:
        return rows
    # plain fallback
    for line in plain.splitlines():
        if "0x" not in line or ("…" not in line and "..." not in line and ".." not in line):
            continue
        tier = None
        if "💎" in line or "elite" in line.lower():
            tier = "elite"
        elif "✅" in line or "good" in line.lower():
            tier = "good"
        else:
            continue
        tm = re.search(
            r"\$?\s*([\d,.]+[kKmM]?)\s*[·•.\-–—]\s*(0x[a-fA-F0-9]{2,8}\s*[…\.]{1,3}\s*[a-fA-F0-9]{2,8})",
            line,
        )
        if not tm:
            continue
        parts = parse_trunc_parts(tm.group(2))
        if not parts:
            continue
        rows.append(
            {
                "trunc": tm.group(2).replace(" ", ""),
                "prefix": parts[0],
                "suffix": parts[1],
                "usd": parse_usd(tm.group(1)),
                "tier": tier,
            }
        )
    return rows


def parse_message(msg_id: str, chunk: str) -> dict | None:
    text_m = re.search(
        r'class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',
        chunk,
        re.I,
    )
    if not text_m:
        return None
    html_text = text_m.group(1)
    plain = html_to_plain(html_text)
    if "EARLY CALL" not in plain.upper():
        return None
    dt_m = re.search(r'datetime="([^"]+)"', chunk)
    ticker_m = TICKER_RE.search(plain)
    ca = extract_token_ca(chunk)
    buys = parse_live_buys(plain, html_text)
    return {
        "msg_id": str(msg_id),
        "posted_at": dt_m.group(1) if dt_m else None,
        "ticker": (ticker_m.group(1).upper() if ticker_m else None),
        "token_ca": ca,
        "chain": "robinhood",
        "live_buys": buys,
        "n_elite": sum(1 for b in buys if b["tier"] == "elite"),
        "n_good": sum(1 for b in buys if b["tier"] == "good"),
        "source_url": f"https://t.me/scoutrobinhood/{msg_id}",
        "scraped_at": now_iso(),
        "plain_snip": plain[:400],
    }



def parse_hit(msg_id: str, chunk: str) -> dict | None:
    """Parse "$TICKER hit Nx" posts for token performance context."""
    text_m = re.search(
        r'class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',
        chunk,
        re.I,
    )
    if not text_m:
        return None
    plain = html_to_plain(text_m.group(1))
    up = plain.upper()
    if "HIT" not in up or "EARLY CALL" in up:
        return None
    hm = HIT_RE.search(plain)
    if not hm:
        hm = re.search(
            r"\$([A-Za-z0-9_]{1,32})[^\n]{0,40}?hit\s+([\d.]+)\s*[xX]",
            plain,
            re.I,
        )
    if not hm:
        return None
    try:
        mult = float(hm.group(2))
    except ValueError:
        mult = None
    dt_m = re.search(r'datetime="([^"]+)"', chunk)
    return {
        "msg_id": str(msg_id),
        "kind": "hit",
        "posted_at": dt_m.group(1) if dt_m else None,
        "ticker": hm.group(1).upper(),
        "hit_mult": mult,
        "token_ca": extract_token_ca(chunk),
        "chain": "robinhood",
        "source_url": f"https://t.me/scoutrobinhood/{msg_id}",
        "scraped_at": now_iso(),
        "plain_snip": plain[:300],
    }


def fetch_page(url: str, timeout: float = 45.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def scrape_pages(n_pages: int) -> tuple[list[dict], list[dict]]:
    """Walk ?before= pages. Returns (early_call_posts, hit_posts)."""
    posts: dict[str, dict] = {}
    hits: dict[str, dict] = {}
    url = BASE_URL
    before: str | None = None
    stale = 0
    for page in range(max(1, n_pages)):
        if before:
            url = f"{BASE_URL}?before={before}"
        try:
            html = fetch_page(url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"scout_tg fetch fail page={page} {type(e).__name__}", file=sys.stderr)
            break
        matches = list(MSG_SPLIT_RE.finditer(html))
        if not matches:
            print(f"scout_tg page={page} no messages")
            break
        min_id = None
        new_early = new_hits = 0
        for i, m in enumerate(matches):
            msg_id = m.group(1)
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
            chunk = html[start:end]
            try:
                mid = int(msg_id)
            except ValueError:
                mid = None
            if mid is not None and (min_id is None or mid < min_id):
                min_id = mid
            rec = parse_message(msg_id, chunk)
            if rec and rec.get("msg_id"):
                prev = posts.get(rec["msg_id"])
                if prev is None:
                    new_early += 1
                if prev is None or len(rec.get("live_buys") or []) >= len(prev.get("live_buys") or []):
                    posts[rec["msg_id"]] = rec
                continue
            hit = parse_hit(msg_id, chunk)
            if hit and hit.get("msg_id"):
                if hit["msg_id"] not in hits:
                    new_hits += 1
                hits[hit["msg_id"]] = hit
        print(
            f"scout_tg page={page} msgs={len(matches)} early={len(posts)} hits={len(hits)} "
            f"new_early={new_early} new_hits={new_hits} before={before}"
        )
        if new_early == 0 and new_hits == 0:
            stale += 1
            if stale >= 3:
                print(f"scout_tg stop: {stale} stale pages")
                break
        else:
            stale = 0
        if min_id is None:
            break
        before = str(min_id)
        time.sleep(0.55)
    early = sorted(posts.values(), key=lambda r: int(r.get("msg_id") or 0), reverse=True)
    hit_list = sorted(hits.values(), key=lambda r: int(r.get("msg_id") or 0), reverse=True)
    return early, hit_list


def load_jsonl_map(path: Path, key: str = "address") -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = o.get(key) or o.get("msg_id") or o.get("trunc_key")
        if k:
            out[str(k).lower() if key == "address" else str(k)] = o
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_jsonl_map(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rows[a], ensure_ascii=False) for a in sorted(rows)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def persist_posts(posts: list[dict]) -> tuple[int, int]:
    """Merge posts + flatten trunc rows. Returns (n_posts, n_trunc)."""
    existing = {}
    if POSTS_PATH.exists():
        for line in POSTS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = str(o.get("msg_id") or "")
            if mid:
                existing[mid] = o
    for p in posts:
        existing[str(p["msg_id"])] = p
    ordered = sorted(existing.values(), key=lambda r: int(r.get("msg_id") or 0), reverse=True)
    # drop bulky snip on disk? keep short
    write_jsonl(POSTS_PATH, ordered)

    trunc_rows: list[dict] = []
    seen: set[str] = set()
    for p in ordered:
        ca = (p.get("token_ca") or "").lower()
        for b in p.get("live_buys") or []:
            key = f"{ca}|{b.get('prefix')}|{b.get('suffix')}|{b.get('tier')}|{p.get('msg_id')}"
            if key in seen:
                continue
            seen.add(key)
            trunc_rows.append(
                {
                    "trunc_key": key,
                    "trunc": b.get("trunc"),
                    "prefix": b.get("prefix"),
                    "suffix": b.get("suffix"),
                    "usd": b.get("usd"),
                    "tier": b.get("tier"),
                    "token_ca": ca,
                    "ticker": p.get("ticker"),
                    "msg_id": p.get("msg_id"),
                    "posted_at": p.get("posted_at"),
                    "source_url": p.get("source_url"),
                    "scraped_at": now_iso(),
                }
            )
    write_jsonl(TRUNC_PATH, trunc_rows)
    return len(ordered), len(trunc_rows)


def persist_hits(hits: list[dict]) -> int:
    existing: dict[str, dict] = {}
    if HITS_PATH.exists():
        for line in HITS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = str(o.get("msg_id") or "")
            if mid:
                existing[mid] = o
    for h in hits:
        existing[str(h["msg_id"])] = h
    ordered = sorted(existing.values(), key=lambda r: int(r.get("msg_id") or 0), reverse=True)
    write_jsonl(HITS_PATH, ordered)
    return len(ordered)


def gmgn_raw(args: list[str], timeout: int = 90) -> tuple[object | None, str | None]:
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
        return None, f"rc={p.returncode}:{(err[:200])}"
    out = (p.stdout or "").strip()
    if not out:
        return None, "empty"
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        i = min([x for x in (out.find("{"), out.find("[")) if x >= 0], default=-1)
        if i < 0:
            return None, "bad_json"
        try:
            return json.loads(out[i:]), None
        except json.JSONDecodeError:
            return None, "bad_json"


def extract_traders(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for k in ("list", "data", "traders", "result", "items"):
        v = data.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            for k2 in ("list", "traders", "holders", "data", "items"):
                if isinstance(v.get(k2), list):
                    return [x for x in v[k2] if isinstance(x, dict)]
    return []


def trader_addr(row: dict) -> str | None:
    for k in ("address", "wallet_address", "walletAddress", "maker", "owner"):
        v = row.get(k)
        if isinstance(v, str) and v.startswith("0x") and len(v) >= 42:
            return v.lower()[:42]
        if isinstance(v, dict):
            a = v.get("address") or v.get("wallet_address")
            if isinstance(a, str) and a.startswith("0x"):
                return a.lower()[:42]
    mi = row.get("maker_info")
    if isinstance(mi, dict):
        a = mi.get("address")
        if isinstance(a, str) and a.startswith("0x"):
            return a.lower()[:42]
    return None


def match_trunc(addr: str, prefix: str, suffix: str) -> bool:
    a = addr.lower()
    return a.startswith(prefix.lower()) and a.endswith(suffix.lower())


def pair_key(prefix: str | None, suffix: str | None) -> str:
    return f"{(prefix or '').lower()}|{(suffix or '').lower()}"


def load_resolve_cache() -> tuple[dict[str, dict], dict[str, str]]:
    """Return (by_trunc_key, pair_to_addr) where pair_to_addr is unique prefix|suffix → address."""
    by_key: dict[str, dict] = {}
    pair_addrs: dict[str, set[str]] = {}
    if not RESOLVE_CACHE.exists():
        return by_key, {}
    for line in RESOLVE_CACHE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        addr = (o.get("address") or "").lower()
        if not (addr.startswith("0x") and len(addr) == 42):
            continue
        k = o.get("trunc_key") or o.get("cache_key")
        if k:
            by_key[str(k)] = o
        pk = pair_key(o.get("prefix"), o.get("suffix"))
        if pk != "|":
            pair_addrs.setdefault(pk, set()).add(addr)
        # also accept explicit pair_key field
        ep = o.get("pair_key")
        if isinstance(ep, str) and "|" in ep:
            pair_addrs.setdefault(ep.lower(), set()).add(addr)
    pair_to_addr = {pk: next(iter(addrs)) for pk, addrs in pair_addrs.items() if len(addrs) == 1}
    return by_key, pair_to_addr


def append_resolve_cache(rows: list[dict]) -> None:
    if not rows:
        return
    RESOLVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with RESOLVE_CACHE.open("a", encoding="utf-8") as f:
        for r in rows:
            if "pair_key" not in r:
                r = dict(r)
                r["pair_key"] = pair_key(r.get("prefix"), r.get("suffix"))
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _absorb_addr(out: set[str], a) -> None:
    if isinstance(a, str) and a.startswith("0x") and len(a) >= 42:
        out.add(a.lower()[:42])


def _absorb_obj_addrs(out: set[str], o: dict) -> None:
    for k in ("address", "evm", "wallet_address", "walletAddress", "maker", "owner", "from", "to"):
        v = o.get(k)
        if isinstance(v, str):
            _absorb_addr(out, v)
        elif isinstance(v, dict):
            _absorb_addr(out, v.get("hash") or v.get("address") or v.get("wallet_address"))
    mi = o.get("maker_info")
    if isinstance(mi, dict):
        _absorb_addr(out, mi.get("address"))


def _load_addrs_from_jsonl_path(path: Path, out: set[str]) -> None:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            _absorb_obj_addrs(out, o)


def _load_addrs_from_json_path(path: Path, out: set[str]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for k in ("list", "data", "traders", "holders", "result", "items"):
            v = data.get(k)
            if isinstance(v, list):
                rows = v
                break
            if isinstance(v, dict):
                for k2 in ("list", "traders", "holders", "data", "items"):
                    if isinstance(v.get(k2), list):
                        rows = v[k2]
                        break
                if rows:
                    break
    for r in rows:
        if isinstance(r, dict):
            _absorb_obj_addrs(out, r)


def iter_known_addresses() -> set[str]:
    """Expanded local free pool for trunc matching (no API). Includes bak dumps."""
    out: set[str] = set()
    fixed = [
        ROOT / "rh-wallets" / "wallets.jsonl",
        ROOT / "rh-wallets" / "wallets_onchain.jsonl",
        ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl",
        ROOT / "rh-wallets" / "wallets_ranked_all.jsonl",
        ROOT / "fomo-wallets" / "wallets_evm.jsonl",
        ROOT / "fomo-wallets" / "leaderboard.jsonl",
        ROOT / "arc-wallets" / "wallets.jsonl",
        ROOT / "arc-wallets" / "wallets_quality.jsonl",
        ROOT / "arc-wallets" / "wallets_early.jsonl",
        RESOLVE_CACHE,
    ]
    for path in fixed:
        if path.exists() and path.is_file():
            if path.suffix == ".json":
                _load_addrs_from_json_path(path, out)
            else:
                _load_addrs_from_jsonl_path(path, out)

    # bak / alternate dumps under rh/arc/fomo
    for folder in (ROOT / "rh-wallets", ROOT / "arc-wallets", ROOT / "fomo-wallets"):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".jsonl") or ".bak" in name or name.endswith(".jsonl.bak") or ".bak_" in name:
                if path.suffix in (".jsonl",) or ".jsonl" in name or name.endswith(".bak") or ".bak_" in name:
                    # treat as line-json if looks like jsonl/bak of wallets
                    if path.suffix == ".json":
                        _load_addrs_from_json_path(path, out)
                    else:
                        _load_addrs_from_jsonl_path(path, out)

    # GMGN raw lists + any other raw json/jsonl
    if RAW_DIR.is_dir():
        for path in RAW_DIR.iterdir():
            if not path.is_file():
                continue
            if path.suffix == ".json":
                _load_addrs_from_json_path(path, out)
            elif path.suffix == ".jsonl" or ".bak" in path.name:
                _load_addrs_from_jsonl_path(path, out)
    return out


def match_hits(pool: set[str] | list[str], prefix: str, suffix: str) -> list[str]:
    pref, suf = prefix.lower(), suffix.lower()
    if not pref or not suf:
        return []
    hits = [a for a in pool if a.startswith(pref) and a.endswith(suf)]
    return list(dict.fromkeys(hits))


def make_resolve_rec(t: dict, addr: str, source: str, label: str = "") -> dict:
    pref = (t.get("prefix") or "").lower()
    suf = (t.get("suffix") or "").lower()
    return {
        "cache_key": t.get("trunc_key"),
        "trunc_key": t.get("trunc_key"),
        "pair_key": pair_key(pref, suf),
        "address": addr.lower()[:42],
        "trunc": t.get("trunc"),
        "prefix": pref,
        "suffix": suf,
        "tier": t.get("tier"),
        "usd": t.get("usd"),
        "token_ca": t.get("token_ca"),
        "ticker": t.get("ticker"),
        "msg_id": t.get("msg_id"),
        "posted_at": t.get("posted_at"),
        "address_label": label or "",
        "resolved_at": now_iso(),
        "source": source,
    }


def http_get_json(url: str, timeout: float = 25.0, retries: int = 3) -> tuple[object | None, str | None]:
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            if raw.lstrip().startswith("<"):
                return None, "html"
            return json.loads(raw), None
        except urllib.error.HTTPError as e:
            last_err = f"http_{e.code}"
            # 422 = bad query params — don't retry same URL
            if e.code in (400, 404, 422):
                return None, last_err
            if e.code == 429:
                time.sleep(1.5 * (2 ** attempt))
                continue
            # CF 403 is often transient on GHA — backoff, don't hard-kill host yet
            if e.code == 403:
                time.sleep(1.2 * (attempt + 1))
                continue
            if e.code in (402, 401):
                return None, last_err
            time.sleep(0.45 * (attempt + 1))
        except Exception as e:
            last_err = type(e).__name__
            time.sleep(0.45 * (attempt + 1))
    return None, last_err or "error"


# Soft host quarantine. NEVER permanently kill on 429 — only cooldown.
_BS_HOST_FAILS: dict[str, int] = {}
_BS_DEAD_HOSTS: set[str] = set()  # only 401/402 paywall
_BS_HOST_COOLDOWN_UNTIL: dict[str, float] = {}
_BS_HOST_FAIL_LIMIT = 8
_BS_RATE_EVENTS = 0


def _bs_note_fail(host: str, err: str | None) -> None:
    global _BS_RATE_EVENTS
    if not err:
        _BS_HOST_FAILS[host] = 0
        return
    if err in ("http_401", "http_402"):
        _BS_DEAD_HOSTS.add(host)
        return
    if err == "http_429":
        _BS_RATE_EVENTS += 1
        # cooldown grows with consecutive rate events (cap 90s)
        cool = min(90.0, 12.0 * (2 ** min(3, _BS_RATE_EVENTS - 1)))
        _BS_HOST_COOLDOWN_UNTIL[host] = time.time() + cool
        print(f"scout_tg blockscout cooldown {host.split('//')[-1][:40]}… {cool:.0f}s (429)", flush=True)
        time.sleep(cool)
        return
    if err in ("http_403", "html"):
        n = _BS_HOST_FAILS.get(host, 0) + 1
        _BS_HOST_FAILS[host] = n
        cool = min(45.0, 4.0 * n)
        _BS_HOST_COOLDOWN_UNTIL[host] = time.time() + cool
        if n >= _BS_HOST_FAIL_LIMIT:
            # temporary quarantine, not forever — clear after long sleep
            print(f"scout_tg blockscout soft-quarantine {host.split('//')[-1][:40]}… fails={n}", flush=True)
            time.sleep(30.0)
            _BS_HOST_FAILS[host] = max(0, n - 3)
        return
    # soft: don't kill on transient network


def _bs_note_ok(host: str) -> None:
    global _BS_RATE_EVENTS
    _BS_HOST_FAILS[host] = 0
    _BS_DEAD_HOSTS.discard(host)
    _BS_HOST_COOLDOWN_UNTIL.pop(host, None)
    _BS_RATE_EVENTS = max(0, _BS_RATE_EVENTS - 1)


def _bs_host_available(host: str) -> bool:
    if not host or host in _BS_DEAD_HOSTS:
        return False
    until = _BS_HOST_COOLDOWN_UNTIL.get(host, 0)
    if until and time.time() < until:
        return False
    return True


def _bs_wait_for_any(hosts: list[str], max_wait: float = 120.0) -> list[str]:
    """Return currently available hosts; if all cooling, sleep until one frees."""
    t0 = time.time()
    while True:
        avail = [h for h in hosts if _bs_host_available(h)]
        if avail:
            return avail
        # all cooling or dead
        lives = [h for h in hosts if h not in _BS_DEAD_HOSTS]
        if not lives:
            return []
        waits = [_BS_HOST_COOLDOWN_UNTIL.get(h, 0) - time.time() for h in lives]
        waits = [w for w in waits if w > 0]
        if not waits or time.time() - t0 > max_wait:
            # force-clear cooldowns after max_wait
            for h in lives:
                _BS_HOST_COOLDOWN_UNTIL.pop(h, None)
            return lives
        time.sleep(min(5.0, max(0.5, min(waits))))


def load_token_pools() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if not TOKEN_POOLS_PATH.exists():
        return out
    for line in TOKEN_POOLS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ca = (o.get("token_ca") or "").lower()
        addrs = o.get("addresses") or []
        if not ca.startswith("0x"):
            continue
        pool = {a.lower()[:42] for a in addrs if isinstance(a, str) and a.startswith("0x") and len(a) >= 42}
        if pool:
            out[ca] = pool | out.get(ca, set())
    return out


def save_token_pools(pools: dict[str, set[str]]) -> None:
    TOKEN_POOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for ca in sorted(pools):
        addrs = sorted(pools[ca])
        if not addrs:
            continue
        rows.append(
            {
                "token_ca": ca,
                "n": len(addrs),
                "addresses": addrs,
                "updated_at": now_iso(),
            }
        )
    write_jsonl(TOKEN_POOLS_PATH, rows)


def fetch_blockscout_addrs(token_ca: str, max_pages: int | None = None) -> tuple[list[str], str | None]:
    """Deep holders/transfers + etherscan-compat tokentx. Returns (addrs, last_err)."""
    if max_pages is None:
        max_pages = int(os.environ.get("SCOUT_TG_BS_PAGES", "20"))
    hosts_v2_all = [
        h
        for h in [
            os.environ.get("RH_BLOCKSCOUT_API_V2", "https://robinhoodchain.blockscout.com/api/v2"),
            "https://api.blockscout.com/4663/api/v2",
        ]
        if h
    ]
    hosts_es_all = [
        h
        for h in [
            os.environ.get("RH_BLOCKSCOUT_API", "https://robinhoodchain.blockscout.com/api"),
            "https://api.blockscout.com/4663/api",
        ]
        if h
    ]
    hosts_v2 = _bs_wait_for_any(hosts_v2_all, max_wait=float(os.environ.get("SCOUT_TG_BS_COOLDOWN_WAIT", "90")))
    hosts_es = _bs_wait_for_any(hosts_es_all, max_wait=float(os.environ.get("SCOUT_TG_BS_COOLDOWN_WAIT", "90")))
    if not hosts_v2 and not hosts_es:
        return [], "all_hosts_dead"

    addrs: list[str] = []
    seen: set[str] = set()
    last_err: str | None = None

    def _absorb(items: list) -> None:
        for it in items:
            if not isinstance(it, dict):
                continue
            cands = []
            for key in ("address", "from", "to", "token_holder", "owner"):
                v = it.get(key)
                if isinstance(v, dict):
                    cands.append(v.get("hash") or v.get("address"))
                elif isinstance(v, str):
                    cands.append(v)
            for a in cands:
                if isinstance(a, str) and a.startswith("0x") and len(a) >= 42:
                    al = a.lower()[:42]
                    if al not in seen:
                        seen.add(al)
                        addrs.append(al)

    def _paginate_v2(host: str, kind: str, extra_q: str | None = None) -> bool:
        nonlocal last_err
        base = f"{host.rstrip('/')}/tokens/{token_ca}/{kind}"
        url = base + (("?" + extra_q) if extra_q else "")
        got = False
        for _page in range(max(1, max_pages)):
            if not _bs_host_available(host):
                break
            data, err = http_get_json(url, timeout=25.0, retries=4)
            if err:
                last_err = err
                _bs_note_fail(host, err)
                if host in _BS_DEAD_HOSTS:
                    return got
                if err == "http_429":
                    # cooldown slept inside note_fail — retry same page once
                    data2, err2 = http_get_json(url, timeout=25.0, retries=3)
                    if err2:
                        last_err = err2
                        _bs_note_fail(host, err2)
                        break
                    data, err = data2, None
                else:
                    break
            _bs_note_ok(host)
            if not isinstance(data, dict):
                last_err = "bad_json"
                break
            items = data.get("items")
            if not isinstance(items, list) or not items:
                break
            before = len(addrs)
            _absorb(items)
            if len(addrs) > before:
                got = True
            nxt = data.get("next_page_params")
            if not nxt:
                break
            q = urllib.parse.urlencode({k: v for k, v in nxt.items() if v is not None})
            url = base + "?" + q
            time.sleep(0.28)
        return got

    # 1) v2 holders + transfers (no type filter first — type=token_transfer often 422)
    for host in list(hosts_v2):
        if host in _BS_DEAD_HOSTS:
            continue
        got_any = False
        if _paginate_v2(host, "holders"):
            got_any = True
        if _paginate_v2(host, "transfers"):
            got_any = True
        # soft retry with type only if bare transfers yielded nothing
        if not got_any:
            if _paginate_v2(host, "transfers", "type=ERC-20"):
                got_any = True
        if got_any:
            break

    # 2) etherscan-compat tokentx — deepen even when holders already nonempty
    es_offset = int(os.environ.get("SCOUT_TG_BS_ES_OFFSET", "100"))
    es_always = env_bool("SCOUT_TG_BS_ES_ALWAYS", True)
    if es_always or len(addrs) < max(50, max_pages * 5):
        for host in list(hosts_es):
            if host in _BS_DEAD_HOSTS:
                continue
            es_got = False
            for page in range(1, max(1, max_pages) + 1):
                if not _bs_host_available(host):
                    break
                qs = urllib.parse.urlencode(
                    {
                        "module": "account",
                        "action": "tokentx",
                        "contractaddress": token_ca,
                        "page": str(page),
                        "offset": str(es_offset),
                        "sort": "desc",
                    }
                )
                data, err = http_get_json(f"{host.rstrip('/')}?{qs}", timeout=25.0, retries=4)
                if err:
                    last_err = err
                    _bs_note_fail(host, err)
                    if host in _BS_DEAD_HOSTS:
                        break
                    if err == "http_429":
                        data2, err2 = http_get_json(f"{host.rstrip('/')}?{qs}", timeout=25.0, retries=3)
                        if err2:
                            last_err = err2
                            _bs_note_fail(host, err2)
                            break
                        data, err = data2, None
                    else:
                        break
                _bs_note_ok(host)
                if not isinstance(data, dict):
                    break
                if str(data.get("status")) != "1":
                    # message may explain; keep last_err soft
                    msg = str(data.get("message") or data.get("result") or "")[:80]
                    if msg:
                        last_err = f"es_{msg}"
                    break
                result = data.get("result") or []
                if not isinstance(result, list) or not result:
                    break
                before = len(addrs)
                _absorb(result)
                if len(addrs) > before:
                    es_got = True
                time.sleep(0.22)
            if es_got:
                break

    if not addrs and last_err is None:
        last_err = "empty"
    return addrs, last_err


class ResolveStats:
    __slots__ = (
        "attempted",
        "token_scoped_hits",
        "global_hits",
        "pair_cache_hits",
        "multi_union_hits",
        "mega_pool_hits",
        "collisions_skipped",
        "two_hit_token_accept",
        "gmgn_calls",
        "bs_tokens",
        "bs_tokens_nonempty",
        "bs_api_fail",
        "unresolved_remaining",
        "unique_pairs_total",
        "unique_pairs_resolved",
        "reason_no_token_pool",
        "reason_multi_match",
        "reason_api_fail",
        "reason_no_match",
    )

    def __init__(self) -> None:
        self.attempted = 0
        self.token_scoped_hits = 0
        self.global_hits = 0
        self.pair_cache_hits = 0
        self.multi_union_hits = 0
        self.mega_pool_hits = 0
        self.collisions_skipped = 0
        self.two_hit_token_accept = 0
        self.gmgn_calls = 0
        self.bs_tokens = 0
        self.bs_tokens_nonempty = 0
        self.bs_api_fail = 0
        self.unresolved_remaining = 0
        self.unique_pairs_total = 0
        self.unique_pairs_resolved = 0
        self.reason_no_token_pool = 0
        self.reason_multi_match = 0
        self.reason_api_fail = 0
        self.reason_no_match = 0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def resolve_truncs(
    trunc_rows: list[dict],
    call_cap: int,
    max_tokens: int,
    elite_only: bool,
) -> tuple[dict[str, dict], int, str | None, ResolveStats]:
    """Return (resolved_by_addr, calls_used, err_kind, stats).

    Prefer token-scoped unique match, then global unique. Never invent addresses.
    """
    stats = ResolveStats()
    cache, pair_to_addr = load_resolve_cache()
    resolved: dict[str, dict] = {}
    new_cache: list[dict] = []
    # track which trunc_keys / pairs we resolved this run
    resolved_keys: set[str] = set()
    # seed addresses from cache
    for _k, o in cache.items():
        addr = (o.get("address") or "").lower()
        if addr.startswith("0x") and len(addr) == 42:
            resolved[addr] = _merge_resolved(resolved.get(addr), o)
            if o.get("trunc_key"):
                resolved_keys.add(str(o["trunc_key"]))

    def pending_rows() -> list[dict]:
        out = []
        for t in trunc_rows:
            if elite_only and t.get("tier") != "elite":
                continue
            ck = t.get("trunc_key") or ""
            if ck in resolved_keys:
                continue
            if ck in cache and cache[ck].get("address"):
                continue
            out.append(t)
        return out

    def accept(t: dict, addr: str, source: str, label: str = "", bucket: str = "global") -> None:
        nonlocal pair_to_addr
        rec = make_resolve_rec(t, addr, source, label=label)
        new_cache.append(rec)
        resolved[addr] = _merge_resolved(resolved.get(addr), rec)
        ck = t.get("trunc_key") or ""
        if ck:
            resolved_keys.add(ck)
            cache[ck] = rec
        pk = pair_key(t.get("prefix"), t.get("suffix"))
        if pk != "|":
            # only set if unique / consistent
            existing = pair_to_addr.get(pk)
            if existing and existing != addr:
                return
            pair_to_addr[pk] = addr
        if bucket == "token":
            stats.token_scoped_hits += 1
        elif bucket == "multi":
            stats.multi_union_hits += 1
        elif bucket == "mega":
            stats.mega_pool_hits += 1
        elif bucket == "pair_cache":
            stats.pair_cache_hits += 1
        elif bucket == "two_hit":
            stats.two_hit_token_accept += 1
            stats.token_scoped_hits += 1
        else:
            stats.global_hits += 1

    # --- Pass 0: apply pair-level cache across ALL trunc rows (cross-token) ---
    for t in pending_rows():
        pk = pair_key(t.get("prefix"), t.get("suffix"))
        addr = pair_to_addr.get(pk)
        if not addr:
            continue
        accept(t, addr, "scout_tg_pair_cache", bucket="pair_cache")
    if stats.pair_cache_hits:
        print(f"scout_tg pair_cache applied_rows={stats.pair_cache_hits} unique_pairs={len(pair_to_addr)}")

    known = iter_known_addresses()
    print(f"scout_tg known_pool={len(known)}")

    # --- Pass 1: token-scoped pools (Blockscout first — free / GHA-friendly) ---
    by_ca: dict[str, list[dict]] = {}
    for t in pending_rows():
        ca = (t.get("token_ca") or "").lower()
        if ca.startswith("0x"):
            by_ca.setdefault(ca, []).append(t)

    def ca_score(items: list[dict]) -> tuple:
        n_elite = sum(1 for x in items if x.get("tier") == "elite")
        return (-n_elite, -len(items))

    skip_frac = float(os.environ.get("SCOUT_TG_SKIP_WELL_RESOLVED", "0.8"))
    cas_ordered = sorted(by_ca.keys(), key=lambda c: ca_score(by_ca[c]))

    calls = 0
    err_kind = None
    gmgn_off = env_bool("GMGN_DISABLED", False)
    chain = os.environ.get("CHAIN", "robinhood")

    def token_resolved_frac(ca: str) -> float:
        items = by_ca.get(ca) or []
        if not items:
            return 1.0
        done = 0
        for t in items:
            ck = t.get("trunc_key") or ""
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            if ck in resolved_keys or pk in pair_to_addr:
                done += 1
        return done / max(1, len(items))

    # load persisted token pools (cross-run)
    token_pools = load_token_pools()
    if token_pools:
        print(f"scout_tg loaded_token_pools={len(token_pools)} addrs={sum(len(v) for v in token_pools.values())}")

    bs_sleep = max(0, int(os.environ.get("SCOUT_TG_BS_SLEEP_MS", "400"))) / 1000.0
    bs_all = env_bool("SCOUT_TG_BS_ALL", False)
    # Cap fetch/deepen ops per run so GHA can commit progress (default 80).
    # -1 = unlimited (legacy). bs_all=1 no longer implies unlimited.
    bs_cap_raw = os.environ.get("SCOUT_TG_BS_TOKEN_CAP", "80")
    try:
        bs_cap = int(str(bs_cap_raw).strip() or "80")
    except ValueError:
        bs_cap = 80 if bs_all else max(1, max_tokens * 3)
    try:
        bs_budget = float(os.environ.get("SCOUT_TG_BS_TIME_BUDGET_SEC", "1200") or "1200")
    except ValueError:
        bs_budget = 1200.0
    bs_deadline = (time.monotonic() + bs_budget) if bs_budget > 0 else None

    if env_bool("SCOUT_TG_BLOCKSCOUT", True):
        # Prefer tokens tied to prior no_match truncs (elite first), then multi-token union leverage.
        no_match_ca_score: dict[str, int] = {}
        if UNRESOLVED_PATH.exists():
            try:
                with UNRESOLVED_PATH.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if o.get("reason") != "no_match":
                            continue
                        tier_w = 10 if o.get("tier") == "elite" else 1
                        for ca0 in o.get("token_cas") or []:
                            ca_l = str(ca0).lower()
                            if ca_l.startswith("0x"):
                                no_match_ca_score[ca_l] = no_match_ca_score.get(ca_l, 0) + tier_w
            except OSError as e:
                print(f"scout_tg unresolved load skip: {type(e).__name__}", flush=True)

        _pcs: dict[str, set[str]] = {}
        for t in trunc_rows:
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            if pk == "|" or pk in pair_to_addr:
                continue
            ca = (t.get("token_ca") or "").lower()
            if ca.startswith("0x"):
                _pcs.setdefault(pk, set()).add(ca)
        ca_multi_score: dict[str, int] = {}
        for pk, cas in _pcs.items():
            if len(cas) >= 2:
                for ca in cas:
                    ca_multi_score[ca] = ca_multi_score.get(ca, 0) + len(cas)

        pending_cas = []
        for ca in cas_ordered:
            pending = [
                t
                for t in by_ca[ca]
                if (t.get("trunc_key") or "") not in resolved_keys
                and pair_key(t.get("prefix"), t.get("suffix")) not in pair_to_addr
            ]
            if pending:
                pending_cas.append(ca)
        pending_cas.sort(
            key=lambda c: (
                -no_match_ca_score.get(c, 0),
                -ca_multi_score.get(c, 0),
                ca_score(by_ca[c]),
            )
        )
        # Walk ALL pending tokens for cheap reuse matches, but only fetch/deepen up to cap.
        print(
            f"scout_tg blockscout pending={len(pending_cas)} "
            f"fetch_cap={bs_cap} budget_sec={bs_budget:g} "
            f"no_match_cas={len(no_match_ca_score)} "
            f"pages={os.environ.get('SCOUT_TG_BS_PAGES', '20')}",
            flush=True,
        )
        bs_fetch_n = 0
        bs_reuse_n = 0
        bs_skipped_cap = 0
        bs_stopped_budget = False
        for i, ca in enumerate(pending_cas):
            if bs_deadline is not None and time.monotonic() >= bs_deadline:
                bs_stopped_budget = True
                print(
                    f"scout_tg blockscout time budget exhausted "
                    f"after fetch={bs_fetch_n} reuse={bs_reuse_n} "
                    f"seen={i}/{len(pending_cas)}",
                    flush=True,
                )
                break
            pending = [
                t
                for t in by_ca[ca]
                if (t.get("trunc_key") or "") not in resolved_keys
                and pair_key(t.get("prefix"), t.get("suffix")) not in pair_to_addr
            ]
            if not pending:
                continue
            # reuse rich pool if already deep
            prior = token_pools.get(ca) or set()
            min_reuse = int(os.environ.get("SCOUT_TG_BS_REUSE_MIN", "80"))
            force = env_bool("SCOUT_TG_BS_FORCE", False)
            reuse_ok = bool(prior) and len(prior) >= min_reuse and not force
            need_deepen = False
            # If pending truncs have zero hits in the reused pool, deepen (counts toward cap).
            if reuse_ok and env_bool("SCOUT_TG_BS_DEEPEN_MISS", True):
                miss = 0
                for t in pending:
                    pref = (t.get("prefix") or "").lower()
                    suf = (t.get("suffix") or "").lower()
                    if not match_hits(prior, pref, suf):
                        miss += 1
                if miss:
                    need_deepen = True
                    reuse_ok = False
                    print(
                        f"scout_tg blockscout deepen-miss {ca[:10]}… "
                        f"prior={len(prior)} miss={miss}/{len(pending)}",
                        flush=True,
                    )
            will_fetch = (not reuse_ok) or force
            if will_fetch:
                if bs_cap >= 0 and bs_fetch_n >= bs_cap:
                    bs_skipped_cap += 1
                    # Still apply prior pool matches if any (no network).
                    if prior:
                        pool = set(prior)
                        for t in pending:
                            pref, suf = (t.get("prefix") or "").lower(), (t.get("suffix") or "").lower()
                            hits = match_hits(pool, pref, suf)
                            if len(hits) == 1:
                                accept(t, hits[0], "scout_tg_blockscout", bucket="token")
                            elif len(hits) > 1:
                                stats.collisions_skipped += 1
                    continue
                if bs_deadline is not None and time.monotonic() >= bs_deadline:
                    bs_stopped_budget = True
                    print(
                        f"scout_tg blockscout time budget before fetch "
                        f"fetch={bs_fetch_n} seen={i}/{len(pending_cas)}",
                        flush=True,
                    )
                    break
                addrs, err = [], None
                attempts = int(os.environ.get("SCOUT_TG_BS_TOKEN_RETRIES", "3"))
                for attempt in range(max(1, attempts)):
                    addrs, err = fetch_blockscout_addrs(ca)
                    if addrs or err in ("empty", "all_hosts_dead", None):
                        break
                    if err in ("http_429", "http_403"):
                        time.sleep(float(os.environ.get("SCOUT_TG_BS_429_PAUSE", "25")))
                        continue
                    break
                bs_fetch_n += 1
                stats.bs_tokens += 1
                if err and err not in ("empty",) and not addrs:
                    stats.bs_api_fail += 1
                if need_deepen:
                    print(
                        f"scout_tg blockscout deepen-fetch {ca[:10]}… "
                        f"[{bs_fetch_n}/{bs_cap if bs_cap >= 0 else '∞'}]",
                        flush=True,
                    )
            else:
                addrs, err = list(prior), None
                bs_reuse_n += 1
                print(
                    f"scout_tg blockscout reuse {ca[:10]}… n={len(addrs)} pending={len(pending)}",
                    flush=True,
                )
            pool = set(addrs) | prior
            if pool:
                token_pools[ca] = pool
            if addrs:
                stats.bs_tokens_nonempty += 1
            if not addrs and not prior:
                print(f"scout_tg blockscout empty {ca[:10]}… err={err}")
                if err == "all_hosts_dead":
                    # hard paywall — stop BS early to avoid burning the run
                    print("scout_tg blockscout abort: all_hosts_dead (401/402)", flush=True)
                    break
                if err == "http_429":
                    time.sleep(float(os.environ.get("SCOUT_TG_BS_429_PAUSE", "25")))
                elif bs_sleep:
                    time.sleep(bs_sleep)
                continue
            print(
                f"scout_tg blockscout {ca[:10]}… n={len(pool)} pending={len(pending)} "
                f"fetch={bs_fetch_n} reuse={bs_reuse_n} [{i+1}/{len(pending_cas)}]",
                flush=True,
            )
            for t in pending:
                pref, suf = (t.get("prefix") or "").lower(), (t.get("suffix") or "").lower()
                hits = match_hits(pool, pref, suf)
                if len(hits) == 1:
                    accept(t, hits[0], "scout_tg_blockscout", bucket="token")
                elif len(hits) > 1:
                    stats.collisions_skipped += 1
            if will_fetch and bs_sleep:
                time.sleep(bs_sleep)
            # periodic pool flush every 25 fetches (or every 50 seen)
            if will_fetch and bs_fetch_n > 0 and bs_fetch_n % 25 == 0:
                save_token_pools(token_pools)
            elif (i + 1) % 50 == 0:
                save_token_pools(token_pools)

        print(
            f"scout_tg blockscout done fetch={bs_fetch_n} reuse={bs_reuse_n} "
            f"skipped_cap={bs_skipped_cap} budget_stop={int(bs_stopped_budget)} "
            f"cap={bs_cap}",
            flush=True,
        )

        # --- Pass 2: GMGN traders (GHA; skip well-resolved tokens; don't burn box) ---
    if gmgn_off:
        print("scout_tg GMGN resolve skip: GMGN_DISABLED=1", flush=True)
        err_kind = "gmgn_disabled"
    else:
        gmgn_cas = []
        for ca in cas_ordered:
            if token_resolved_frac(ca) >= skip_frac:
                continue
            pending = [
                t
                for t in by_ca.get(ca, [])
                if (t.get("trunc_key") or "") not in resolved_keys
                and pair_key(t.get("prefix"), t.get("suffix")) not in pair_to_addr
            ]
            if pending:
                gmgn_cas.append(ca)
        for ca in gmgn_cas[: max(1, max_tokens)]:
            if stats.gmgn_calls >= call_cap:
                break
            pending = [
                t
                for t in by_ca[ca]
                if (t.get("trunc_key") or "") not in resolved_keys
                and pair_key(t.get("prefix"), t.get("suffix")) not in pair_to_addr
            ]
            if not pending:
                continue
            data, err = gmgn_raw(
                [
                    "token",
                    "traders",
                    "--chain",
                    chain,
                    "--address",
                    ca,
                    "--limit",
                    "100",
                    "--order-by",
                    "profit",
                ],
                timeout=90,
            )
            calls += 1
            stats.gmgn_calls += 1
            if err == "rate_limited":
                print(f"scout_tg traders rate-limited after calls={calls}", file=sys.stderr)
                err_kind = "rate_limited"
                break
            if err:
                print(f"scout_tg traders fail {ca[:10]}… {err}", file=sys.stderr)
                err_kind = err
                if stats.gmgn_calls < call_cap:
                    data2, err2 = gmgn_raw(
                        [
                            "token",
                            "holders",
                            "--chain",
                            chain,
                            "--address",
                            ca,
                            "--limit",
                            "100",
                        ],
                        timeout=90,
                    )
                    calls += 1
                    stats.gmgn_calls += 1
                    if err2 == "rate_limited":
                        err_kind = "rate_limited"
                        break
                    if not err2:
                        data, err = data2, None
                    else:
                        continue
                else:
                    continue
            rows = extract_traders(data)
            addrs_meta: list[tuple[str, dict]] = []
            for r in rows:
                a = trader_addr(r)
                if a:
                    addrs_meta.append((a, r))
            pool = {a for a, _ in addrs_meta}
            token_pools[ca] = pool | token_pools.get(ca, set())
            print(f"scout_tg traders {ca[:10]}… n={len(pool)} pending_trunc={len(pending)}")
            for t in pending:
                pref, suf = (t.get("prefix") or "").lower(), (t.get("suffix") or "").lower()
                hits = match_hits(pool, pref, suf)
                if len(hits) == 1:
                    meta_row = next((r for a, r in addrs_meta if a == hits[0]), {})
                    label = ""
                    mi = meta_row.get("maker_info") if isinstance(meta_row, dict) else None
                    if isinstance(mi, dict):
                        label = str(mi.get("name") or "")
                    accept(t, hits[0], "scout_tg", label=label, bucket="token")
                elif len(hits) > 1:
                    stats.collisions_skipped += 1
            time.sleep(1.0)

    # --- Pass 2.5: multi-token union for pairs appearing on ≥2 tokens ---
    if env_bool("SCOUT_TG_MULTI_UNION", True):
        pair_to_cas: dict[str, set[str]] = {}
        for t in trunc_rows:
            if elite_only and t.get("tier") != "elite":
                continue
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            if pk == "|" or pk in pair_to_addr:
                continue
            ca = (t.get("token_ca") or "").lower()
            if ca.startswith("0x"):
                pair_to_cas.setdefault(pk, set()).add(ca)
        multi_pairs = {pk: cas for pk, cas in pair_to_cas.items() if len(cas) >= 2}
        print(f"scout_tg multi_union candidate_pairs={len(multi_pairs)}")
        # representative trunc row per pair
        rep: dict[str, dict] = {}
        for t in trunc_rows:
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            if pk in multi_pairs and pk not in rep:
                rep[pk] = t
        for pk, cas in sorted(multi_pairs.items(), key=lambda x: -len(x[1])):
            if pk in pair_to_addr:
                continue
            union: set[str] = set()
            for ca in cas:
                union |= token_pools.get(ca) or set()
            if len(union) < 2:
                continue
            pre, suf = pk.split("|", 1)
            hits = match_hits(union, pre, suf)
            if len(hits) == 1:
                t = rep.get(pk)
                if t:
                    accept(t, hits[0], "scout_tg_multi_union", bucket="multi")
                    # cross-fill remaining rows via pair cache loop later
            elif len(hits) > 1:
                stats.collisions_skipped += 1
        # re-apply pair cache after union accepts
        for t in pending_rows():
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            addr = pair_to_addr.get(pk)
            if addr:
                accept(t, addr, "scout_tg_pair_cache", bucket="pair_cache")

    # --- Pass 2.75: mega-union of ALL token pools (unique prefix|suffix only) ---
    # Wallets often appear in another Scout token's holders/transfers graph.
    if env_bool("SCOUT_TG_MEGA_UNION", True) and token_pools:
        mega: set[str] = set()
        for _ca, pool in token_pools.items():
            mega |= pool
        print(f"scout_tg mega_union pool_addrs={len(mega)} tokens={len(token_pools)}")
        for t in pending_rows():
            pref = (t.get("prefix") or "").lower()
            suf = (t.get("suffix") or "").lower()
            pk = pair_key(pref, suf)
            if pk in pair_to_addr or pk == "|":
                continue
            hits = match_hits(mega, pref, suf)
            if len(hits) == 1:
                accept(t, hits[0], "scout_tg_mega_union", bucket="mega")
            elif len(hits) > 1:
                stats.collisions_skipped += 1
        for t in pending_rows():
            pk = pair_key(t.get("prefix"), t.get("suffix"))
            addr = pair_to_addr.get(pk)
            if addr:
                accept(t, addr, "scout_tg_pair_cache", bucket="pair_cache")
        if stats.mega_pool_hits:
            print(f"scout_tg mega_union hits={stats.mega_pool_hits}")

    # --- Pass 3: global unique against local pool (+ optional 2-hit ∩ token) ---
    still = pending_rows()
    stats.attempted = len(
        [
            t
            for t in trunc_rows
            if not (elite_only and t.get("tier") != "elite")
        ]
    )
    # Also re-check rows that may have been filled via pair during earlier accepts
    for t in still:
        pref = (t.get("prefix") or "").lower()
        suf = (t.get("suffix") or "").lower()
        pk = pair_key(pref, suf)
        if pk in pair_to_addr:
            accept(t, pair_to_addr[pk], "scout_tg_pair_cache", bucket="pair_cache")
            continue
        hits = match_hits(known, pref, suf)
        if len(hits) == 1:
            accept(t, hits[0], "scout_tg_local_cache", bucket="global")
            continue
        if len(hits) > 1:
            # optional: exactly 2 global hits, only 1 in token transfer set
            ca = (t.get("token_ca") or "").lower()
            tpool = token_pools.get(ca) or set()
            if len(hits) == 2 and tpool:
                in_tok = [a for a in hits if a in tpool]
                if len(in_tok) == 1:
                    accept(t, in_tok[0], "scout_tg_global2_token", bucket="two_hit")
                    continue
            stats.collisions_skipped += 1

    # Pass 3b: after new global accepts, re-apply pair cache to remaining
    for t in pending_rows():
        pk = pair_key(t.get("prefix"), t.get("suffix"))
        addr = pair_to_addr.get(pk)
        if addr:
            accept(t, addr, "scout_tg_pair_cache", bucket="pair_cache")

    # Also index newly resolved pairs as standalone cache rows (dedupe by pair)
    pair_index_rows = []
    seen_pair_write: set[str] = set()
    for r in new_cache:
        pk = r.get("pair_key") or pair_key(r.get("prefix"), r.get("suffix"))
        if pk in seen_pair_write or pk == "|":
            continue
        seen_pair_write.add(pk)
        pair_index_rows.append(
            {
                "cache_key": f"pair|{pk}",
                "trunc_key": f"pair|{pk}",
                "pair_key": pk,
                "address": r["address"],
                "trunc": r.get("trunc"),
                "prefix": r.get("prefix"),
                "suffix": r.get("suffix"),
                "tier": r.get("tier"),
                "usd": r.get("usd"),
                "token_ca": "",
                "ticker": "",
                "msg_id": "",
                "posted_at": None,
                "address_label": "",
                "resolved_at": now_iso(),
                "source": "scout_tg_pair_index",
            }
        )

    append_resolve_cache(new_cache + pair_index_rows)
    save_token_pools(token_pools)
    calls = stats.gmgn_calls

    # final unresolved stats + reason report (unique pairs)
    all_pairs = {
        pair_key(t.get("prefix"), t.get("suffix"))
        for t in trunc_rows
        if not (elite_only and t.get("tier") != "elite")
        and pair_key(t.get("prefix"), t.get("suffix")) != "|"
    }
    stats.unique_pairs_total = len(all_pairs)
    stats.unique_pairs_resolved = sum(1 for pk in all_pairs if pk in pair_to_addr)

    # build pair → tokens + sample row
    pair_cas: dict[str, set[str]] = {}
    pair_row: dict[str, dict] = {}
    for t in trunc_rows:
        if elite_only and t.get("tier") != "elite":
            continue
        pk = pair_key(t.get("prefix"), t.get("suffix"))
        if pk == "|":
            continue
        ca = (t.get("token_ca") or "").lower()
        if ca:
            pair_cas.setdefault(pk, set()).add(ca)
        if pk not in pair_row:
            pair_row[pk] = t

    unresolved_rows: list[dict] = []
    for pk in sorted(all_pairs):
        if pk in pair_to_addr:
            continue
        pre, suf = pk.split("|", 1)
        cas = pair_cas.get(pk) or set()
        union: set[str] = set()
        api_fail_tokens = 0
        for ca in cas:
            pool = token_pools.get(ca) or set()
            union |= pool
            if ca not in token_pools:
                api_fail_tokens += 1
        global_hits = match_hits(known, pre, suf)
        token_hits = match_hits(union, pre, suf) if union else []
        if len(global_hits) > 1 or len(token_hits) > 1:
            reason = "multi_match"
            stats.reason_multi_match += 1
        elif not cas:
            reason = "no_token_ca"
            stats.reason_no_token_pool += 1
        elif not union:
            reason = "api_fail" if api_fail_tokens == len(cas) else "no_token_pool"
            if reason == "api_fail":
                stats.reason_api_fail += 1
            else:
                stats.reason_no_token_pool += 1
        else:
            reason = "no_match"
            stats.reason_no_match += 1
        t = pair_row.get(pk) or {}
        unresolved_rows.append(
            {
                "pair_key": pk,
                "prefix": pre,
                "suffix": suf,
                "trunc": t.get("trunc"),
                "tier": t.get("tier"),
                "reason": reason,
                "n_tokens": len(cas),
                "token_cas": sorted(cas)[:30],
                "union_pool_size": len(union),
                "global_hits": len(global_hits),
                "token_hits": len(token_hits),
            }
        )

    write_jsonl(UNRESOLVED_PATH, unresolved_rows)
    stats.unresolved_remaining = sum(
        1
        for t in trunc_rows
        if not (elite_only and t.get("tier") != "elite")
        and (t.get("trunc_key") or "") not in resolved_keys
        and pair_key(t.get("prefix"), t.get("suffix")) not in pair_to_addr
    )

    # markdown summary
    try:
        lines = [
            "# Scout TG trunc resolve summary",
            "",
            f"- Updated (UTC): {now_iso()}",
            f"- Unique pairs: **{stats.unique_pairs_resolved}/{stats.unique_pairs_total}** "
            f"({(100.0 * stats.unique_pairs_resolved / max(1, stats.unique_pairs_total)):.1f}%)",
            f"- Trunc rows still unresolved: **{stats.unresolved_remaining}**",
            f"- Methods: pair_cache={stats.pair_cache_hits} token_scoped={stats.token_scoped_hits} "
            f"multi_union={stats.multi_union_hits} mega={stats.mega_pool_hits} "
            f"global={stats.global_hits} two_hit={stats.two_hit_token_accept}",
            f"- Blockscout: tokens_fetched={stats.bs_tokens} nonempty={stats.bs_tokens_nonempty} "
            f"api_fail={stats.bs_api_fail} gmgn_calls={stats.gmgn_calls}",
            f"- Unresolved reasons (unique pairs): no_token_pool={stats.reason_no_token_pool} "
            f"api_fail={stats.reason_api_fail} multi_match={stats.reason_multi_match} "
            f"no_match={stats.reason_no_match}",
            "",
            "## Top unresolved blockers",
            "",
        ]
        from collections import Counter as _Counter

        rc = _Counter(r["reason"] for r in unresolved_rows)
        for reason, n in rc.most_common():
            lines.append(f"- `{reason}`: {n}")
        lines.append("")
        lines.append("### Sample unresolved (up to 40)")
        lines.append("")
        for r in unresolved_rows[:40]:
            lines.append(
                f"- `{r['pair_key']}` reason={r['reason']} tokens={r['n_tokens']} "
                f"union={r['union_pool_size']} tier={r.get('tier')}"
            )
        RESOLVE_SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"scout_tg summary write fail: {type(e).__name__}", file=sys.stderr)

    print(
        "scout_tg resolve_stats "
        + json.dumps(stats.as_dict(), ensure_ascii=False)
    )
    return resolved, calls, err_kind, stats


def _merge_resolved(prev: dict | None, cur: dict) -> dict:
    if not prev:
        out = dict(cur)
        out["scout_hit_count"] = 1
        out["scout_elite_count"] = 1 if cur.get("tier") == "elite" else 0
        out["scout_good_count"] = 1 if cur.get("tier") == "good" else 0
        out["scout_sum_buy_usd"] = float(cur.get("usd") or 0)
        out["scout_msg_ids"] = [cur["msg_id"]] if cur.get("msg_id") else []
        out["scout_token_cas"] = [cur["token_ca"]] if cur.get("token_ca") else []
        out["scout_tickers"] = [cur["ticker"]] if cur.get("ticker") else []
        return out
    out = dict(prev)
    out["scout_hit_count"] = int(out.get("scout_hit_count") or 0) + 1
    if cur.get("tier") == "elite":
        out["scout_elite_count"] = int(out.get("scout_elite_count") or 0) + 1
    if cur.get("tier") == "good":
        out["scout_good_count"] = int(out.get("scout_good_count") or 0) + 1
    try:
        out["scout_sum_buy_usd"] = float(out.get("scout_sum_buy_usd") or 0) + float(cur.get("usd") or 0)
    except (TypeError, ValueError):
        pass
    for key, field in (
        ("scout_msg_ids", "msg_id"),
        ("scout_token_cas", "token_ca"),
        ("scout_tickers", "ticker"),
    ):
        lst = list(out.get(key) or [])
        v = cur.get(field)
        if v and v not in lst:
            lst.append(v)
        out[key] = lst[-50:]
    # keep strongest tier label
    if cur.get("tier") == "elite" or out.get("tier") == "elite":
        out["tier"] = "elite"
    elif cur.get("tier"):
        out["tier"] = cur.get("tier") or out.get("tier")
    if cur.get("address_label") and not out.get("address_label"):
        out["address_label"] = cur["address_label"]
    out["address"] = (cur.get("address") or out.get("address") or "").lower()
    return out


def merge_into_watchlist(resolved: dict[str, dict], watch_path: Path) -> tuple[int, int]:
    """Merge resolved wallets. Returns (added, tagged)."""
    existing = load_jsonl_map(watch_path, "address")
    added = tagged = 0
    now = now_iso()
    for addr, r in resolved.items():
        addr = addr.lower()
        if not addr.startswith("0x") or len(addr) != 42:
            continue
        tier = r.get("tier") or "good"
        tags = ["scout_tg", "scout_tg_early"]
        if tier == "elite":
            tags.append("scout_elite")
        else:
            tags.append("scout_good")
        src_eps = ["telegram:scoutrobinhood"]
        if addr not in existing:
            row = {
                "address": addr,
                "address_label": r.get("address_label")
                or f"scout_{tier} [{addr[:6]}…{addr[-4:]}]",
                "realized_pnl_usd": None,  # unknown until ranked
                "win_rate": None,
                "n_trades": None,
                "tags": tags,
                "gmgn_tags": list(tags),
                "sources": ["scout_tg"],
                "source_endpoints": src_eps,
                "scout_tier": tier,
                "scout_hit_count": int(r.get("scout_hit_count") or 1),
                "scout_elite_count": int(r.get("scout_elite_count") or (1 if tier == "elite" else 0)),
                "scout_good_count": int(r.get("scout_good_count") or (1 if tier == "good" else 0)),
                "scout_sum_buy_usd": float(r.get("scout_sum_buy_usd") or r.get("usd") or 0),
                "scout_msg_ids": list(r.get("scout_msg_ids") or ([r["msg_id"]] if r.get("msg_id") else [])),
                "scout_token_cas": list(r.get("scout_token_cas") or ([r["token_ca"]] if r.get("token_ca") else [])),
                "pass_pnl": False,  # not yet ranked
                "chain": "robinhood",
                "list_tier": "scout",
                "quality_reason": "scout_tg_resolved_pending_rank",
                "collected_at": now,
            }
            existing[addr] = row
            added += 1
        else:
            o = existing[addr]
            changed = False
            tags_o = list(o.get("tags") or [])
            for t in tags:
                if t not in tags_o:
                    tags_o.append(t)
                    changed = True
            o["tags"] = tags_o
            gtags = list(o.get("gmgn_tags") or [])
            for t in tags:
                if t not in gtags:
                    gtags.append(t)
                    changed = True
            o["gmgn_tags"] = gtags
            eps = list(o.get("source_endpoints") or [])
            for e in src_eps:
                if e not in eps:
                    eps.append(e)
                    changed = True
            o["source_endpoints"] = eps
            srcs = list(o.get("sources") or [])
            if "scout_tg" not in srcs:
                srcs.append("scout_tg")
                changed = True
            o["sources"] = srcs
            # enrich scout counters (max/sum carefully)
            for fld in ("scout_hit_count", "scout_elite_count", "scout_good_count"):
                try:
                    nv = int(r.get(fld) or 0)
                    ov = int(o.get(fld) or 0)
                    if nv > ov:
                        o[fld] = nv
                        changed = True
                except (TypeError, ValueError):
                    pass
            try:
                nv = float(r.get("scout_sum_buy_usd") or 0)
                ov = float(o.get("scout_sum_buy_usd") or 0)
                if nv > ov:
                    o["scout_sum_buy_usd"] = nv
                    changed = True
            except (TypeError, ValueError):
                pass
            if r.get("tier") == "elite":
                o["scout_tier"] = "elite"
                changed = True
            elif not o.get("scout_tier"):
                o["scout_tier"] = tier
                changed = True
            for fld in ("scout_msg_ids", "scout_token_cas"):
                lst = list(o.get(fld) or [])
                for v in r.get(fld) or []:
                    if v not in lst:
                        lst.append(v)
                        changed = True
                o[fld] = lst[-50:]
            if changed:
                tagged += 1
            existing[addr] = o
    write_jsonl_map(watch_path, existing)
    return added, tagged


def main() -> int:
    if not env_bool("SCOUT_TG_ENABLED", True):
        print("scout_tg disabled (SCOUT_TG_ENABLED=0)")
        return 0
    pages = int(os.environ.get("SCOUT_TG_PAGES", "5"))
    do_resolve = env_bool("SCOUT_TG_RESOLVE", True)
    vet_cap = int(os.environ.get("SCOUT_TG_VET_CAP", "40"))
    max_tokens = int(os.environ.get("SCOUT_TG_MAX_TOKENS", "8"))
    elite_only = env_bool("SCOUT_TG_ELITE_ONLY", False)
    watch_path = Path(os.environ.get("WATCHLIST_PATH", str(ROOT / "rh-wallets" / "wallets.jsonl")))
    if not watch_path.is_absolute():
        watch_path = ROOT / watch_path

    print(f"scout_tg scrape pages={pages} resolve={do_resolve} cap={vet_cap} max_tokens={max_tokens}")
    if pages <= 0:
        print("scout_tg scrape skipped (SCOUT_TG_PAGES<=0) — resolve-only from disk")
        posts, hits = [], []
        n_posts = n_trunc = n_hits = n_early = n_with_ca = 0
        if POSTS_PATH.exists():
            n_posts = sum(1 for line in POSTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
        if TRUNC_PATH.exists():
            n_trunc = sum(1 for line in TRUNC_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
        if HITS_PATH.exists():
            n_hits = sum(1 for line in HITS_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
        print(f"scout_tg disk posts={n_posts} trunc_rows={n_trunc} hits={n_hits}")
    else:
        posts, hits = scrape_pages(pages)
        n_posts, n_trunc = persist_posts(posts)
        n_hits = persist_hits(hits)
        n_early = sum(1 for p in posts if p.get("live_buys"))
        n_with_ca = sum(1 for p in posts if p.get("token_ca"))
        print(
            f"scout_tg scraped early_posts={n_early} with_ca={n_with_ca} hits={n_hits} "
            f"persisted_posts={n_posts} trunc_rows={n_trunc}"
        )

    resolved_n = 0
    added = tagged = 0
    err = None
    resolve_stats: dict = {}
    if do_resolve and n_trunc > 0:
        # reload trunc from disk (merged history)
        trunc_rows = []
        if TRUNC_PATH.exists():
            for line in TRUNC_PATH.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    trunc_rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # prefer elite first in list order
        trunc_rows.sort(
            key=lambda t: (0 if t.get("tier") == "elite" else 1, -(t.get("usd") or 0))
        )
        resolved, calls, err, rstats = resolve_truncs(trunc_rows, vet_cap, max_tokens, elite_only)
        resolved_n = len(resolved)
        print(f"scout_tg resolved_full={resolved_n} gmgn_calls={calls} err={err}")
        if resolved:
            added, tagged = merge_into_watchlist(resolved, watch_path)
            print(f"scout_tg watch merge added={added} tagged={tagged} path={watch_path}")
        resolve_stats = rstats.as_dict()
    elif do_resolve:
        print("scout_tg resolve skip: no trunc rows")
    else:
        print("scout_tg resolve skipped (SCOUT_TG_RESOLVE=0)")

    print(
        json.dumps(
            {
                "ok": True,
                "pages": pages,
                "posts": n_posts,
                "early": n_early,
                "hits": n_hits,
                "trunc": n_trunc,
                "resolved": resolved_n,
                "added": added,
                "tagged": tagged,
                "err": err,
                "resolve_stats": resolve_stats,
            }
        )
    )
    return 0 if err != "rate_limited" else 0  # soft — still ship scraper artifacts


if __name__ == "__main__":
    raise SystemExit(main())

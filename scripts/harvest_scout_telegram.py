#!/usr/bin/env python3
"""Scrape t.me/s/scoutrobinhood EARLY CALL Live buys → resolve trunc wallets via GMGN.

Env:
  SCOUT_TG_ENABLED=1     master switch (default 1 when run directly)
  SCOUT_TG_PAGES=5       how many ?before= pages to walk
  SCOUT_TG_RESOLVE=1     call gmgn token traders to expand 0xABCD…WXYZ
  SCOUT_TG_VET_CAP=40    max GMGN token-traders / portfolio calls this run
  SCOUT_TG_MAX_TOKENS=8  max distinct token CAs to query traders for
  SCOUT_TG_ELITE_ONLY=0  if 1, only resolve 💎 elite truncs
  GMGN_DISABLED=1        skip resolve (box IP ban); scrape still runs
  CHAIN=robinhood
  WATCHLIST_PATH=rh-wallets/wallets.jsonl

Writes:
  rh-wallets/raw/scout_tg_posts.jsonl
  rh-wallets/raw/scout_tg_trunc.jsonl
  rh-wallets/raw/scout_tg_hits.jsonl
  merges resolved full addrs into WATCHLIST_PATH (no wipe)

Deep harvest tip: SCOUT_TG_PAGES=40..80 (stops early on 3 stale pages).
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


def load_resolve_cache() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not RESOLVE_CACHE.exists():
        return out
    for line in RESOLVE_CACHE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = o.get("trunc_key") or o.get("cache_key")
        if k and o.get("address"):
            out[str(k)] = o
    return out


def append_resolve_cache(rows: list[dict]) -> None:
    if not rows:
        return
    RESOLVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with RESOLVE_CACHE.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")



def iter_known_addresses() -> set[str]:
    """Local free pool for trunc matching (no API)."""
    out: set[str] = set()
    paths = [
        ROOT / "rh-wallets" / "wallets.jsonl",
        ROOT / "rh-wallets" / "wallets_onchain.jsonl",
        ROOT / "rh-wallets" / "wallets_scout_ranked.jsonl",
        ROOT / "rh-wallets" / "wallets_ranked_all.jsonl",
        ROOT / "fomo-wallets" / "wallets_evm.jsonl",
        ROOT / "fomo-wallets" / "leaderboard.jsonl",
        ROOT / "arc-wallets" / "wallets.jsonl",
        RESOLVE_CACHE,
    ]
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            for k in ("address", "evm"):
                a = (o.get(k) or "").lower()
                if a.startswith("0x") and len(a) >= 42:
                    out.add(a[:42])
    for name in ("gmgn_kol_buy.json", "gmgn_track_kol.json"):
        path = RAW_DIR / name
        if not path.exists() or path.stat().st_size < 2:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows = data if isinstance(data, list) else []
        if isinstance(data, dict):
            rows = data.get("list") or data.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            for k in ("maker", "address", "wallet_address"):
                a = r.get(k)
                if isinstance(a, str) and a.startswith("0x") and len(a) >= 42:
                    out.add(a.lower()[:42])
            mi = r.get("maker_info")
            if isinstance(mi, dict):
                a = mi.get("address")
                if isinstance(a, str) and a.startswith("0x") and len(a) >= 42:
                    out.add(a.lower()[:42])
    return out


def resolve_from_known(trunc_rows: list[dict], known: set[str]) -> list[dict]:
    """Unique prefix/suffix match against local address pool."""
    out: list[dict] = []
    for t in trunc_rows:
        pref, suf = (t.get("prefix") or "").lower(), (t.get("suffix") or "").lower()
        if not pref or not suf:
            continue
        hits = [a for a in known if a.startswith(pref) and a.endswith(suf)]
        uniq = list(dict.fromkeys(hits))
        if len(uniq) != 1:
            continue
        addr = uniq[0]
        out.append(
            {
                "cache_key": t.get("trunc_key"),
                "trunc_key": t.get("trunc_key"),
                "address": addr,
                "trunc": t.get("trunc"),
                "prefix": pref,
                "suffix": suf,
                "tier": t.get("tier"),
                "usd": t.get("usd"),
                "token_ca": t.get("token_ca"),
                "ticker": t.get("ticker"),
                "msg_id": t.get("msg_id"),
                "posted_at": t.get("posted_at"),
                "address_label": "",
                "resolved_at": now_iso(),
                "source": "scout_tg_local_cache",
            }
        )
    return out


def fetch_blockscout_addrs(token_ca: str, max_pages: int | None = None) -> list[str]:
    """Best-effort RH Blockscout holders/transfers with pagination (GHA-friendly)."""
    if max_pages is None:
        max_pages = int(os.environ.get("SCOUT_TG_BS_PAGES", "8"))
    hosts = [
        os.environ.get("RH_BLOCKSCOUT_API_V2", "https://robinhoodchain.blockscout.com/api/v2"),
        "https://api.blockscout.com/4663/api/v2",
    ]
    addrs: list[str] = []
    seen: set[str] = set()

    def _absorb(items: list) -> None:
        for it in items:
            if not isinstance(it, dict):
                continue
            cands = []
            for key in ("address", "from", "to", "token_holder"):
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

    for host in hosts:
        got_any = False
        for kind in ("holders", "transfers"):
            url = f"{host.rstrip('/')}/tokens/{token_ca}/{kind}"
            if kind == "transfers":
                url += "?type=token_transfer"
            for _page in range(max(1, max_pages)):
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": UA, "Accept": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    if raw.lstrip().startswith("<"):
                        break
                    data = json.loads(raw)
                except Exception:
                    break
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list) or not items:
                    break
                before = len(addrs)
                _absorb(items)
                if len(addrs) > before:
                    got_any = True
                nxt = data.get("next_page_params") if isinstance(data, dict) else None
                if not nxt:
                    break
                q = urllib.parse.urlencode({k: v for k, v in nxt.items() if v is not None})
                base = f"{host.rstrip('/')}/tokens/{token_ca}/{kind}"
                url = base + "?" + q
                time.sleep(0.2)
        if got_any:
            return addrs
    return addrs

def resolve_truncs(
    trunc_rows: list[dict],
    call_cap: int,
    max_tokens: int,
    elite_only: bool,
) -> tuple[dict[str, dict], int, str | None]:
    """Return (resolved_by_addr, calls_used, err_kind)."""
    cache = load_resolve_cache()
    resolved: dict[str, dict] = {}
    # seed from cache
    for k, o in cache.items():
        addr = (o.get("address") or "").lower()
        if addr.startswith("0x") and len(addr) == 42:
            prev = resolved.get(addr)
            if prev is None:
                resolved[addr] = dict(o)
            else:
                # merge frequency
                resolved[addr] = _merge_resolved(prev, o)

    # Free local unique matches (watchlist / onchain / fomo / kol raw)
    pending_for_local = [
        t
        for t in trunc_rows
        if not (elite_only and t.get("tier") != "elite")
        and (t.get("trunc_key") not in cache or not (cache.get(t.get("trunc_key") or "") or {}).get("address"))
    ]
    known = iter_known_addresses()
    local_hits = resolve_from_known(pending_for_local, known)
    new_cache: list[dict] = []
    if local_hits:
        for rec in local_hits:
            new_cache.append(rec)
            resolved[rec["address"]] = _merge_resolved(resolved.get(rec["address"]), rec)
        append_resolve_cache(local_hits)
        print(f"scout_tg local_cache resolved_rows={len(local_hits)} addrs={len({r['address'] for r in local_hits})} known_pool={len(known)}")
    else:
        print(f"scout_tg local_cache hits=0 known_pool={len(known)}")

    if env_bool("GMGN_DISABLED", False):
        print("scout_tg GMGN resolve skip: GMGN_DISABLED=1", flush=True)
        # still try blockscout if enabled
        if not env_bool("SCOUT_TG_BLOCKSCOUT", True):
            return resolved, 0, "gmgn_disabled"

    # group pending truncs by token_ca
    by_ca: dict[str, list[dict]] = {}
    for t in trunc_rows:
        if elite_only and t.get("tier") != "elite":
            continue
        ca = (t.get("token_ca") or "").lower()
        if not ca.startswith("0x"):
            continue
        ck = t.get("trunc_key") or ""
        if ck in cache and cache[ck].get("address"):
            continue
        by_ca.setdefault(ca, []).append(t)

    # prefer tokens with more elite buys
    def ca_score(items: list[dict]) -> tuple:
        n_elite = sum(1 for x in items if x.get("tier") == "elite")
        n = len(items)
        return (-n_elite, -n)

    cas_ordered = sorted(by_ca.keys(), key=lambda c: ca_score(by_ca[c]))
    calls = 0
    err_kind = None
    # new_cache already seeded from local hits
    chain = os.environ.get("CHAIN", "robinhood")
    gmgn_off = env_bool("GMGN_DISABLED", False)

    if gmgn_off:
        err_kind = "gmgn_disabled"
        cas_ordered = []  # skip GMGN traders
    for ca in cas_ordered[: max(1, max_tokens)]:
        if calls >= call_cap:
            break
        pending = by_ca[ca]
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
        if err == "rate_limited":
            print(f"scout_tg traders rate-limited after calls={calls}", file=sys.stderr)
            err_kind = "rate_limited"
            break
        if err:
            print(f"scout_tg traders fail {ca[:10]}… {err}", file=sys.stderr)
            err_kind = err
            # try holders as soft fallback once
            if calls < call_cap:
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
        addrs = []
        for r in rows:
            a = trader_addr(r)
            if a:
                addrs.append((a, r))
        print(f"scout_tg traders {ca[:10]}… n={len(addrs)} pending_trunc={len(pending)}")
        for t in pending:
            pref, suf = t.get("prefix") or "", t.get("suffix") or ""
            hits = [a for a, _ in addrs if match_trunc(a, pref, suf)]
            # unique match only
            uniq = list(dict.fromkeys(hits))
            if len(uniq) != 1:
                continue
            addr = uniq[0]
            meta_row = next((r for a, r in addrs if a == addr), {})
            label = ""
            mi = meta_row.get("maker_info") if isinstance(meta_row, dict) else None
            if isinstance(mi, dict):
                label = str(mi.get("name") or "")
            rec = {
                "cache_key": t.get("trunc_key"),
                "trunc_key": t.get("trunc_key"),
                "address": addr,
                "trunc": t.get("trunc"),
                "prefix": pref,
                "suffix": suf,
                "tier": t.get("tier"),
                "usd": t.get("usd"),
                "token_ca": ca,
                "ticker": t.get("ticker"),
                "msg_id": t.get("msg_id"),
                "posted_at": t.get("posted_at"),
                "address_label": label,
                "resolved_at": now_iso(),
                "source": "scout_tg",
            }
            new_cache.append(rec)
            resolved[addr] = _merge_resolved(resolved.get(addr), rec)
        time.sleep(1.0)

    # Blockscout fallback when GMGN rate-limited / disabled (unique match only)
    if env_bool("SCOUT_TG_BLOCKSCOUT", True) and (
        err_kind in ("rate_limited", "gmgn_disabled", None) or gmgn_off
    ):
        still_pending: dict[str, list[dict]] = {}
        cached_keys = {r.get("trunc_key") for r in new_cache} | set(cache.keys())
        for t in trunc_rows:
            if elite_only and t.get("tier") != "elite":
                continue
            ck = t.get("trunc_key") or ""
            if ck in cached_keys:
                continue
            ca = (t.get("token_ca") or "").lower()
            if ca.startswith("0x"):
                still_pending.setdefault(ca, []).append(t)
        bs_cas = sorted(
            still_pending.keys(),
            key=lambda c: (
                -sum(1 for x in still_pending[c] if x.get("tier") == "elite"),
                -len(still_pending[c]),
            ),
        )[: max(1, max_tokens)]
        for ca in bs_cas:
            if calls >= call_cap:
                break
            addrs = fetch_blockscout_addrs(ca)
            calls += 1
            if not addrs:
                print(f"scout_tg blockscout empty {ca[:10]}…")
                continue
            print(f"scout_tg blockscout {ca[:10]}… n={len(addrs)}")
            for t in still_pending[ca]:
                pref, suf = t.get("prefix") or "", t.get("suffix") or ""
                hits = [a for a in addrs if match_trunc(a, pref, suf)]
                uniq = list(dict.fromkeys(hits))
                if len(uniq) != 1:
                    continue
                rec = {
                    "cache_key": t.get("trunc_key"),
                    "trunc_key": t.get("trunc_key"),
                    "address": uniq[0],
                    "trunc": t.get("trunc"),
                    "prefix": pref,
                    "suffix": suf,
                    "tier": t.get("tier"),
                    "usd": t.get("usd"),
                    "token_ca": ca,
                    "ticker": t.get("ticker"),
                    "msg_id": t.get("msg_id"),
                    "posted_at": t.get("posted_at"),
                    "address_label": "",
                    "resolved_at": now_iso(),
                    "source": "scout_tg_blockscout",
                }
                new_cache.append(rec)
                resolved[uniq[0]] = _merge_resolved(resolved.get(uniq[0]), rec)
            time.sleep(0.4)

    # only append GMGN/blockscout rows not already flushed from local
    to_append = [r for r in new_cache if r.get("source") != "scout_tg_local_cache"]
    append_resolve_cache(to_append)
    return resolved, calls, err_kind


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

    print(f"scout_tg scrape pages={pages} resolve={do_resolve} cap={vet_cap}")
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
        resolved, calls, err = resolve_truncs(trunc_rows, vet_cap, max_tokens, elite_only)
        resolved_n = len(resolved)
        print(f"scout_tg resolved_full={resolved_n} gmgn_calls={calls} err={err}")
        if resolved:
            added, tagged = merge_into_watchlist(resolved, watch_path)
            print(f"scout_tg watch merge added={added} tagged={tagged} path={watch_path}")
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
            }
        )
    )
    return 0 if err != "rate_limited" else 0  # soft — still ship scraper artifacts


if __name__ == "__main__":
    raise SystemExit(main())

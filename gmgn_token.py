"""GMGN token info + security via gmgn-cli. Numbers come only from GMGN."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

try:
    from load_secrets import write_gmgn_dotenv
except ImportError:
    def write_gmgn_dotenv(path=None):  # type: ignore
        return bool((os.environ.get("GMGN_API_KEY") or "").strip())


_CACHE: dict[str, tuple[float, dict | None, str | None]] = {}
_CACHE_TTL = 90.0


_CHAIN_SLUG = {
    "solana": "sol",
    "sol": "sol",
    "bsc": "bsc",
    "eth": "eth",
    "base": "base",
    "robinhood": "robinhood",
    "rh": "robinhood",
    "arc": "arc",
    "stable": "stable",
}


def token_app_url(chain: str, ca: str, provided: str | None = None) -> str:
    """HTTPS universal link that opens the GMGN iOS/Android app on the token."""
    ca = (ca or "").strip()
    if provided:
        u = provided.strip()
        if u.startswith("https://gmgn.ai/") and "/token/" in u:
            return u.split("?")[0]
    slug = _CHAIN_SLUG.get((chain or "").lower(), chain or "robinhood")
    return f"https://gmgn.ai/{slug}/token/{ca}"


def _num(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _yesno(v) -> str:
    s = str(v if v is not None else "").strip().lower()
    if s in ("yes", "true", "1"):
        return "yes"
    if s in ("no", "false", "0"):
        return "no"
    return ""


def _tax(v) -> float | None:
    n = _num(v)
    if n is None:
        return None
    if n > 1:
        n = n / 100.0
    if n < 0:
        n = 0.0
    return n


def _parse_reset_wait(err: str) -> float:
    """Seconds to wait from RATE_LIMIT message / reset_at. Cap 90s."""
    m = re.search(r"reset_at[\"']?\s*[:=]\s*(\d{9,})", err)
    if m:
        wait = int(m.group(1)) - time.time()
        return max(1.0, min(90.0, wait + 1))
    m = re.search(r"resets? at ([^\n]+)", err, re.I)
    # fall back short pause
    if "RATE_LIMIT" in err.upper() or "429" in err:
        return 8.0
    return 0.0


def gmgn_cli_json(args: list[str], timeout: float = 40, retries: int = 1) -> tuple[dict | None, str | None]:
    """Run gmgn-cli ... --raw. Returns (data, err_kind). err: auth|rate|other."""
    write_gmgn_dotenv()
    key = (os.environ.get("GMGN_API_KEY") or "").strip()
    if not key or len(key) < 16:
        return None, "auth"
    cli = shutil.which("gmgn-cli")
    if not cli:
        print("gmgn-cli not found on PATH", flush=True)
        return None, "other"
    cmd = [cli, *args, "--raw"]
    cache_key = " ".join(args)
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL and hit[1] is not None:
        return hit[1], hit[2]

    attempt = 0
    while True:
        attempt += 1
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ})
        except subprocess.TimeoutExpired:
            print("gmgn-cli token timeout", flush=True)
            return None, "other"
        except Exception as e:
            print(f"gmgn-cli token spawn fail: {type(e).__name__}", flush=True)
            return None, "other"
        err = (proc.stderr or "") + "\n" + (proc.stdout or "")
        err_u = err.upper()
        kind: str | None = None
        if "AUTH_KEY_INVALID" in err_u or "API KEY INVALID" in err_u or " 401 " in err or err_u.startswith("401"):
            kind = "auth"
        elif "RATE_LIMIT" in err_u or "429" in err:
            kind = "rate"
        elif proc.returncode != 0:
            kind = "other"
            safe = re.sub(r"(GMGN_API_KEY|apikey|api[_-]?key)[=:\s]+\S+", r"\1=***", err, flags=re.I)
            print(f"gmgn-cli token fail: {safe.strip()[:300]}", flush=True)
        data = None
        if kind is None:
            out = (proc.stdout or "").strip()
            if not out:
                kind = "other"
            else:
                try:
                    parsed = json.loads(out)
                except json.JSONDecodeError:
                    kind = "other"
                    parsed = None
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    kind = "other"
        if kind == "rate" and attempt <= retries:
            wait = _parse_reset_wait(err)
            print(f"gmgn-cli rate; sleep {wait:.0f}s then retry {attempt}/{retries}", flush=True)
            time.sleep(wait)
            continue
        # Cache successes and non-rate failures briefly so we don't hammer
        _CACHE[cache_key] = (time.time(), data, kind)
        return data, kind


def fetch_token_info(chain: str, ca: str) -> tuple[dict | None, str | None]:
    return gmgn_cli_json(["token", "info", "--chain", chain, "--address", ca])


def fetch_token_security(chain: str, ca: str) -> tuple[dict | None, str | None]:
    return gmgn_cli_json(["token", "security", "--chain", chain, "--address", ca])


def parse_info(info: dict) -> dict:
    price_obj = info.get("price")
    if isinstance(price_obj, dict):
        price = _num(price_obj.get("price"))
        # Some payloads nest market_cap under price
        nested_mcap = _num(
            price_obj.get("market_cap")
            or price_obj.get("usd_market_cap")
            or price_obj.get("mc")
        )
    else:
        price = _num(price_obj)
        nested_mcap = None
    circ = _num(info.get("circulating_supply"))
    if circ is None:
        circ = _num(info.get("total_supply"))
    # Prefer GMGN-provided market cap fields when present (matches app better)
    mcap = _num(
        info.get("market_cap")
        or info.get("usd_market_cap")
        or info.get("mc")
        or info.get("marketcap")
    )
    if mcap is None:
        mcap = nested_mcap
    if mcap is None and price is not None and circ is not None and circ > 0:
        mcap = price * circ
    fdv = _num(info.get("fdv") or info.get("fully_diluted_valuation"))
    if fdv is None and price is not None:
        mx = _num(info.get("max_supply") or info.get("total_supply"))
        if mx and mx > 0:
            fdv = price * mx
    liq = _num(info.get("liquidity"))
    pool = info.get("pool") if isinstance(info.get("pool"), dict) else {}
    if liq is None:
        liq = _num(pool.get("liquidity"))
    link = info.get("link") if isinstance(info.get("link"), dict) else {}
    gmgn_url = (link.get("gmgn") or "").strip() or None
    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    tags = info.get("wallet_tags_stat") if isinstance(info.get("wallet_tags_stat"), dict) else {}
    return {
        "symbol": (info.get("symbol") or "").strip() or None,
        "name": (info.get("name") or "").strip() or None,
        "price_usd": price,
        "mcap_usd": mcap,
        "fdv": fdv if fdv is not None else mcap,
        "liq_usd": liq,
        "holder_count": _num(info.get("holder_count") or stat.get("holder_count")),
        "locked_ratio": _num(info.get("locked_ratio")),
        "top10": _num(stat.get("top_10_holder_rate") or info.get("top_10_holder_rate")),
        "gmgn_url": gmgn_url,
        "exchange": pool.get("exchange"),
        "smart_wallets": tags.get("smart_wallets"),
        "renowned_wallets": tags.get("renowned_wallets"),
    }


def parse_security(sec: dict) -> dict:
    burn = str(sec.get("burn_status") or "").strip().lower()
    locked_alt = None
    for k in ("liquidity_locked", "is_locked", "lp_locked", "locked_ratio"):
        if k in sec:
            v = sec.get(k)
            if isinstance(v, (int, float)) or (isinstance(v, str) and str(v).replace(".", "", 1).isdigit()):
                locked_alt = _num(v)
            elif _yesno(v) == "yes":
                locked_alt = 1.0
            elif _yesno(v) == "no":
                locked_alt = 0.0
            break
    cts = str(sec.get("creator_token_status") or "").strip().lower()
    return {
        "honeypot": _yesno(sec.get("is_honeypot")),
        "open_source": _yesno(sec.get("open_source")) or str(sec.get("open_source") or "").strip().lower(),
        "owner_renounced": _yesno(sec.get("owner_renounced")) or str(sec.get("owner_renounced") or "").strip().lower(),
        "renounced_mint": sec.get("renounced_mint"),
        "renounced_freeze_account": sec.get("renounced_freeze_account"),
        "buy_tax": _tax(sec.get("buy_tax")),
        "sell_tax": _tax(sec.get("sell_tax")),
        "rug_ratio": _num(sec.get("rug_ratio")),
        "top10": _num(sec.get("top_10_holder_rate")),
        "burn_status": burn,
        "locked_ratio": locked_alt,
        "is_wash_trading": sec.get("is_wash_trading"),
        "creator_token_status": cts,
        "sniper_count": _num(sec.get("sniper_count")),
        "dev_team_hold_rate": _num(sec.get("dev_team_hold_rate")),
    }


def gmgn_security_checklist(sec: dict, chain: str) -> list[dict]:
    """GMGN Token Quick Scoring Card. status: ok | warn | danger | na | missing."""
    is_sol = (chain or "").lower() in ("sol", "solana")
    items: list[dict] = []

    def add(key: str, label: str, status: str, detail: str = ""):
        items.append({"key": key, "label": label, "status": status, "detail": detail})

    hp = sec.get("honeypot") or ""
    if is_sol and not hp:
        add("honeypot", "honeypot", "na", "sol")
    elif hp == "no":
        add("honeypot", "honeypot", "ok", "no")
    elif hp == "yes":
        add("honeypot", "honeypot", "danger", "yes")
    else:
        add("honeypot", "honeypot", "missing", hp or "-")

    osrc = sec.get("open_source") or ""
    if osrc == "yes":
        add("open_source", "ソース公開", "ok", "yes")
    elif osrc == "no":
        add("open_source", "ソース公開", "danger", "no")
    elif osrc in ("unknown",):
        add("open_source", "ソース公開", "warn", "unknown")
    else:
        add("open_source", "ソース公開", "missing", osrc or "-")

    own = sec.get("owner_renounced") or ""
    if own == "yes":
        add("owner_renounced", "オーナー放棄", "ok", "yes")
    elif own == "no":
        add("owner_renounced", "オーナー放棄", "danger", "no")
    elif own == "unknown":
        add("owner_renounced", "オーナー放棄", "warn", "unknown")
    else:
        add("owner_renounced", "オーナー放棄", "missing", own or "-")

    if is_sol:
        for key, label in (("renounced_mint", "ミント放棄"), ("renounced_freeze_account", "凍結放棄")):
            v = sec.get(key)
            if v is True or str(v).lower() in ("true", "1", "yes"):
                add(key, label, "ok", "true")
            elif v is False or str(v).lower() in ("false", "0", "no"):
                add(key, label, "danger", "false")
            else:
                add(key, label, "missing", str(v))

    for tax_key, label in (("buy_tax", "買い税"), ("sell_tax", "売り税")):
        tax = sec.get(tax_key)
        if tax is None:
            add(tax_key, label, "missing", "-")
        elif tax == 0 or tax < 0.005:
            add(tax_key, label, "ok", f"{tax:.0%}")
        elif tax <= 0.05:
            add(tax_key, label, "warn", f"{tax:.0%}")
        else:
            add(tax_key, label, "danger", f"{tax:.0%}")

    rug = sec.get("rug_ratio")
    if rug is None:
        add("rug_ratio", "rug", "missing", "-")
    elif rug < 0.10:
        add("rug_ratio", "rug", "ok", f"{rug:.2f}")
    elif rug <= 0.30:
        add("rug_ratio", "rug", "warn", f"{rug:.2f}")
    else:
        add("rug_ratio", "rug", "danger", f"{rug:.2f}")

    top10 = sec.get("top10")
    if top10 is None:
        add("top10", "top10", "missing", "-")
    elif top10 < 0.20:
        add("top10", "top10", "ok", f"{top10:.0%}")
    elif top10 <= 0.50:
        add("top10", "top10", "warn", f"{top10:.0%}")
    else:
        add("top10", "top10", "danger", f"{top10:.0%}")

    cts = (sec.get("creator_token_status") or "").lower()
    if cts in ("creator_close", "close", "sold"):
        add("creator", "作成者保有", "ok", cts or "close")
    elif cts in ("creator_hold", "hold"):
        add("creator", "作成者保有", "danger", cts)
    elif not cts:
        add("creator", "作成者保有", "missing", "-")
    else:
        add("creator", "作成者保有", "warn", cts)

    sn = sec.get("sniper_count")
    if sn is None:
        add("sniper", "スナイパー", "missing", "-")
    elif sn < 5:
        add("sniper", "スナイパー", "ok", str(int(sn)))
    elif sn <= 20:
        add("sniper", "スナイパー", "warn", str(int(sn)))
    else:
        add("sniper", "スナイパー", "danger", str(int(sn)))

    return items


def checklist_all_ok(items: list[dict]) -> tuple[bool, list[str]]:
    """Legacy: True only when every applicable field is ok. Prefer checklist_no_danger."""
    fails: list[str] = []
    for it in items:
        st = it["status"]
        if st == "na":
            continue
        if st == "ok":
            continue
        fails.append(f"audit_{it['key']}:{st}:{it.get('detail') or ''}")
    return (len(fails) == 0, fails)


def checklist_no_danger(items: list[dict]) -> tuple[bool, list[str]]:
    """Pass unless any checklist item is danger (🚫). warn/missing/ok/na do not fail the gate."""
    fails: list[str] = []
    for it in items:
        if it["status"] == "danger":
            fails.append(f"audit_{it['key']}:danger:{it.get('detail') or ''}")
    return (len(fails) == 0, fails)


def market_snapshot(chain: str, ca: str) -> dict:
    """Market dict sourced only from GMGN token info."""
    info, err = fetch_token_info(chain, ca)
    if not info:
        return {
            "ok": False,
            "reason": f"gmgn_{err or 'fail'}",
            "liq_usd": None,
            "mcap_usd": None,
            "fdv": None,
            "price_usd": None,
            "url": None,
            "pair": None,
            "source": "gmgn",
            "fetch_failed": True,
        }
    p = parse_info(info)
    url = token_app_url(chain, ca, p.get("gmgn_url"))
    ok = p.get("price_usd") is not None or p.get("liq_usd") is not None
    return {
        "ok": bool(ok),
        "reason": None if ok else "gmgn_empty",
        "liq_usd": p.get("liq_usd"),
        "mcap_usd": p.get("mcap_usd"),
        "fdv": p.get("fdv") or p.get("mcap_usd"),
        "price_usd": p.get("price_usd"),
        "url": url,
        "pair": None,
        "chainId": chain,
        "symbol": p.get("symbol"),
        "source": "gmgn",
        "fetch_failed": False,
    }


def evaluate(
    chain: str,
    ca: str,
    *,
    liq_mcap_min: float = 0.20,
    lp_lock_min: float = 0.01,
    tax_max: float = 0.10,
    rug_max: float = 0.30,
) -> dict[str, Any]:
    """Safety + numbers. GMGN only. Never invent."""
    info, info_err = fetch_token_info(chain, ca)
    # Pace security behind info so GHA IP is less likely to trip leaky-bucket
    if info:
        time.sleep(1.2)
    sec, sec_err = fetch_token_security(chain, ca)
    fail: list[str] = []
    fetch_failed = False
    parsed_info = parse_info(info) if info else {}
    parsed_sec = parse_security(sec) if sec else {}

    if not info:
        fail.append(f"gmgn_info:{info_err or 'fail'}")
        fetch_failed = True
    if not sec:
        fail.append(f"gmgn_security:{sec_err or 'fail'}")
        fetch_failed = True

    price = parsed_info.get("price_usd")
    mcap = parsed_info.get("mcap_usd")
    liq = parsed_info.get("liq_usd")
    ratio = None
    if liq is not None and mcap and mcap > 0:
        ratio = liq / mcap

    if info and mcap is None:
        fail.append("no_mcap")
    elif info and (ratio is None or ratio < liq_mcap_min):
        fail.append(f"liq_ratio={ratio:.2f}" if ratio is not None else "liq_ratio=na")

    # Fill top10 from info if security omitted it
    if parsed_sec.get("top10") is None and parsed_info.get("top10") is not None:
        parsed_sec["top10"] = parsed_info.get("top10")

    checklist: list[dict] = []
    if sec:
        checklist = gmgn_security_checklist(parsed_sec, chain)
        no_danger, audit_fails = checklist_no_danger(checklist)
        if not no_danger:
            fail.extend(audit_fails)

    hp = parsed_sec.get("honeypot") or ""
    bt = parsed_sec.get("buy_tax")
    st = parsed_sec.get("sell_tax")
    rug = parsed_sec.get("rug_ratio")
    top10 = parsed_sec.get("top10")

    burn = parsed_sec.get("burn_status") or ""
    locked = parsed_info.get("locked_ratio")
    if locked is None:
        locked = parsed_sec.get("locked_ratio")
    burn_ok = burn in ("burn", "burned", "yes", "1", "true")
    lock_ok = locked is not None and locked >= lp_lock_min
    # LP is display-only unless LP_LOCK_REQUIRED=1
    require_lp = str(os.environ.get("LP_LOCK_REQUIRED", "0")).strip().lower() in ("1", "true", "yes")
    if sec and info:
        if burn_ok or lock_ok:
            lp_status = "pass"
            lp_reason = "burn" if burn_ok else "locked"
        elif locked is None and not burn:
            lp_status = "unknown"
            lp_reason = "lp_unknown"
            if require_lp:
                fail.append("lp_unknown")
        else:
            lp_status = "unlocked"
            lp_reason = "lp_unlocked"
            if require_lp:
                fail.append("lp_unlocked")
    else:
        lp_status = "unavailable"
        lp_reason = "gmgn_lp_unavailable"
        if require_lp:
            fail.append("gmgn_lp_unavailable")

    ok = len(fail) == 0
    audit = _audit_checklist_jp(checklist, parsed_info)
    if ok:
        ratio_txt = f"流動性/時価≈{ratio:.0%}" if ratio is not None else "流動性OK"
        jp = f"通過（{ratio_txt}・危険項目なし）"
    else:
        jp_bits = []
        for r in fail:
            if r == "no_mcap":
                jp_bits.append("時価総額なし")
            elif r.startswith("liq_ratio"):
                jp_bits.append("流動性が薄い")
            elif r.startswith("audit_honeypot"):
                jp_bits.append("honeypot")
            elif r.startswith("audit_open_source"):
                jp_bits.append("ソース未公開")
            elif r.startswith("audit_owner"):
                jp_bits.append("オーナー未放棄")
            elif r.startswith("audit_buy_tax") or r.startswith("audit_sell_tax"):
                jp_bits.append("手数料")
            elif r.startswith("audit_rug"):
                jp_bits.append("rug")
            elif r.startswith("audit_top10"):
                jp_bits.append("上位集中")
            elif r.startswith("audit_creator"):
                jp_bits.append("作成者保有")
            elif r.startswith("audit_sniper"):
                jp_bits.append("スナイパー多")
            elif r.startswith("audit_"):
                jp_bits.append("監査未達")
            elif r.startswith("gmgn_"):
                jp_bits.append("GMGN取得失敗")
            else:
                jp_bits.append("検査NG")
        # unique preserve order
        seen_b = set()
        uniq = []
        for b in jp_bits:
            if b not in seen_b:
                seen_b.add(b)
                uniq.append(b)
        jp = "見送り（" + "・".join(uniq) + "）"

    gmgn_url = token_app_url(chain, ca, parsed_info.get("gmgn_url"))
    print(
        f"safety ca={ca[:10]}… ok={ok} src=gmgn ratio={ratio} "
        f"lp={lp_status}:{lp_reason} locked={locked} burn={burn or '-'} "
        f"mcap={mcap} liq={liq} fetch_failed={fetch_failed} "
        f"reasons={fail or ['ok']}",
        flush=True,
    )
    return {
        "ok": ok,
        "reasons": fail,
        "ratio": ratio,
        "liq_usd": liq,
        "mcap_usd": mcap,
        "fdv": parsed_info.get("fdv") or mcap,
        "price_usd": price,
        "dex_url": gmgn_url,
        "gmgn_url": gmgn_url,
        "goplus": "unused",
        "jp": jp,
        "audit_jp": audit,
        "symbol_hint": parsed_info.get("symbol"),
        "source": "gmgn",
        "honeypot": hp,
        "buy_tax": bt,
        "sell_tax": st,
        "rug_ratio": rug,
        "burn_status": burn,
        "locked_ratio": locked,
        "top10": top10,
        "holder_count": parsed_info.get("holder_count"),
        "lp_status": lp_status,
        "info_err": info_err,
        "sec_err": sec_err,
        "fetch_failed": fetch_failed,
        "checklist": checklist,
    }


def _audit_checklist_jp(checklist: list[dict], info: dict) -> str:
    mark = {"ok": "✅", "warn": "⚠️", "danger": "🚫", "missing": "？", "na": "—"}
    bits = []
    for it in checklist:
        bits.append(f"{mark.get(it['status'], '?')}{it['label']}")
    holders = info.get("holder_count")
    if holders is not None:
        bits.append(f"保有{int(holders)}人")
    return " · ".join(bits) if bits else "GMGN監査なし"


def _pct(x: float) -> str:
    return f"{x*100:.0f}%"

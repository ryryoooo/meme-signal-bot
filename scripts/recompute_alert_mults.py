#!/usr/bin/env python3
"""Recompute alert multiples from DexScreener — do NOT trust milestones_hit alone."""
from __future__ import annotations
import json, time, urllib.request
from pathlib import Path

def dex_price(ca: str) -> float | None:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    req = urllib.request.Request(url, headers={"User-Agent": "alert-mult/1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    p = max(pairs, key=lambda x: float(((x.get("liquidity") or {}).get("usd") or 0) or 0))
    try:
        return float(p.get("priceUsd") or 0) or None
    except (TypeError, ValueError):
        return None

def main() -> int:
    state_path = Path("state-arc.json")
    log_path = Path("paper_log_arc.jsonl")
    alerts = {}
    if state_path.exists():
        st = json.loads(state_path.read_text())
        for a in st.get("open_alerts") or []:
            ca = (a.get("ca") or "").lower()
            if ca:
                alerts[ca] = a
    if log_path.exists():
        for ln in log_path.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            if not r.get("posted"):
                continue
            ca = (r.get("ca") or "").lower()
            if not ca:
                continue
            alerts.setdefault(ca, {"ca": ca, "symbol": r.get("symbol"), "alert_price_usd": r.get("alert_price_usd")})
            if not alerts[ca].get("alert_price_usd"):
                alerts[ca]["alert_price_usd"] = r.get("alert_price_usd")
    rows = []
    for ca, a in alerts.items():
        try:
            alert = float(a["alert_price_usd"]) if a.get("alert_price_usd") is not None else None
        except (TypeError, ValueError):
            alert = None
        now = dex_price(ca)
        time.sleep(0.12)
        mult = (now / alert) if (now and alert and alert > 0) else None
        rows.append({
            "symbol": a.get("symbol"),
            "ca": ca,
            "alert_price": alert,
            "price_now": now,
            "mult_now": mult,
            "milestones_hit": a.get("milestones_hit"),
            "ge2": bool(mult and mult >= 2),
        })
    rows.sort(key=lambda r: (r["mult_now"] is not None, r["mult_now"] or 0), reverse=True)
    out = Path("alert_mult_scan.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ge2 = [r for r in rows if r["ge2"]]
    print(f"alerts={len(rows)} ge2_now={len(ge2)}")
    for r in rows:
        m = r["mult_now"]
        print(f"{'>=2x' if r['ge2'] else '    '}\t{r['symbol']}\t{m if m is None else round(m,2)}\t{r['ca'][:12]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

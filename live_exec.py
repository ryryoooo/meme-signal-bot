"""Thin gmgn-cli swap wrapper for Arc live trades. Never logs secrets."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

ARC_USDC = "0x3600000000000000000000000000000000000000"
ARC_USDC_DECIMALS = 6

_SECRET_RE = re.compile(
    r"(GMGN_API_KEY|API[_-]?KEY|PRIVATE[_-]?KEY|WALLET_PRIVATE_KEY|apikey)"
    r"[=:\s]+\S+",
    re.I,
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text or "")


def wallet_address() -> str:
    return (os.environ.get("LIVE_WALLET_ADDRESS") or "").strip()


def usdc_raw_from_usd(usd: float) -> str:
    """USDC 6 decimals → integer string for --amount."""
    if usd <= 0:
        return "0"
    raw = int(round(float(usd) * (10**ARC_USDC_DECIMALS)))
    return str(max(0, raw))


def usd_from_usdc_raw(raw) -> float | None:
    try:
        return float(raw) / (10**ARC_USDC_DECIMALS)
    except (TypeError, ValueError):
        return None


def _parse_balance_usd(data: dict | None) -> float | None:
    if not isinstance(data, dict):
        return None
    # Common shapes: balance / amount / ui_amount / usd_value / data.balance
    nested = data.get("data") if isinstance(data.get("data"), dict) else data
    for key in ("usd_value", "usd", "balance_usd", "value_usd"):
        v = nested.get(key) if isinstance(nested, dict) else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    for key in ("ui_amount", "uiAmount", "balance_ui", "amount_ui"):
        v = nested.get(key) if isinstance(nested, dict) else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    for key in ("balance", "amount", "raw_balance", "token_balance"):
        v = nested.get(key) if isinstance(nested, dict) else None
        if v is None:
            continue
        # Prefer human amount if float-like small; else treat as raw
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 1e9:  # likely raw 6-dec
            u = usd_from_usdc_raw(f)
            if u is not None:
                return u
        return f
    return None


def fetch_usdc_balance_usd(chain: str = "arc", timeout: float = 35) -> tuple[float | None, str | None]:
    """On-chain Arc USDC balance in USD units. Soft-fail → (None, err)."""
    try:
        from load_secrets import write_gmgn_dotenv
    except ImportError:
        write_gmgn_dotenv = None  # type: ignore
    if write_gmgn_dotenv:
        write_gmgn_dotenv()
    w = wallet_address()
    if not w:
        return None, "no_wallet"
    cli = shutil.which("gmgn-cli")
    if not cli:
        return None, "no_cli"
    cmd = [
        cli,
        "portfolio",
        "token-balance",
        "--chain",
        chain,
        "--wallet",
        w,
        "--token",
        ARC_USDC,
        "--raw",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ})
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, type(e).__name__
    if proc.returncode != 0:
        err = _redact((proc.stderr or "") + " " + (proc.stdout or ""))
        kind = "rate" if "RATE_LIMIT" in err.upper() or "429" in err else "other"
        print(f"live_exec usdc_balance fail kind={kind}: {err.strip()[:240]}", flush=True)
        return None, kind
    out = (proc.stdout or "").strip()
    if not out:
        return None, "empty"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, "bad_json"
    bal = _parse_balance_usd(data if isinstance(data, dict) else None)
    return bal, None if bal is not None else "parse"


def _run_swap(args: list[str], timeout: float = 90) -> tuple[dict | None, str | None]:
    """Run gmgn-cli swap --raw --yes. Returns (json|None, err_kind|None)."""
    try:
        from load_secrets import write_gmgn_dotenv
    except ImportError:
        write_gmgn_dotenv = None  # type: ignore
    if write_gmgn_dotenv:
        write_gmgn_dotenv()
    if not (os.environ.get("GMGN_API_KEY") or "").strip():
        return None, "auth"
    allow = (os.environ.get("GMGN_ALLOW_AUTOMATED_TRADES") or "").strip().lower()
    if allow not in ("1", "true", "yes", "on"):
        return None, "allow_flag"
    cli = shutil.which("gmgn-cli")
    if not cli:
        return None, "no_cli"
    w = wallet_address()
    if not w:
        return None, "no_wallet"
    cmd = [cli, "swap", *args, "--from", w, "--yes", "--raw", "--auto-slippage"]
    # Never log full cmd with secrets; args have no keys
    print(f"live_exec swap {' '.join(args[:8])}…", flush=True)
    try:
        env = {**os.environ, "GMGN_ALLOW_AUTOMATED_TRADES": "1"}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        print("live_exec swap timeout", flush=True)
        return None, "timeout"
    except Exception as e:
        print(f"live_exec swap spawn fail: {type(e).__name__}", flush=True)
        return None, "spawn"
    err = _redact((proc.stderr or "") + "\n" + (proc.stdout or ""))
    err_u = err.upper()
    if "AUTH_KEY_INVALID" in err_u or "API KEY INVALID" in err_u:
        print(f"live_exec swap auth fail: {err.strip()[:240]}", flush=True)
        return None, "auth"
    if "RATE_LIMIT" in err_u or "429" in err_u or "BANNED" in err_u:
        print(f"live_exec swap rate/ban: {err.strip()[:240]}", flush=True)
        return None, "rate"
    if "BIND" in err_u or "BINDING" in err_u or "NOT BOUND" in err_u:
        print(f"live_exec swap binding: {err.strip()[:240]}", flush=True)
        return None, "binding"
    if proc.returncode != 0:
        print(f"live_exec swap fail rc={proc.returncode}: {err.strip()[:300]}", flush=True)
        return None, "other"
    out = (proc.stdout or "").strip()
    if not out:
        return None, "empty"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print(f"live_exec swap non-json: {_redact(out)[:200]}", flush=True)
        return None, "bad_json"
    if not isinstance(data, dict):
        return None, "bad_shape"
    # Soft success: some APIs nest under data / code
    code = data.get("code")
    if code is not None and str(code) not in ("0", "200", "ok", "OK"):
        msg = _redact(str(data.get("message") or data.get("error") or code))
        print(f"live_exec swap api_code={code} msg={msg[:200]}", flush=True)
        return data, "api"
    return data, None


def swap_buy_usdc_to_token(
    chain: str,
    token: str,
    amount_usd: float,
    *,
    timeout: float = 90,
) -> tuple[dict | None, str | None]:
    """Buy token with Arc USDC. amount_usd is human USD."""
    raw = usdc_raw_from_usd(amount_usd)
    if raw == "0":
        return None, "zero_amount"
    token = (token or "").strip()
    if not token.startswith("0x"):
        return None, "bad_token"
    return _run_swap(
        [
            "--chain",
            chain,
            "--input-token",
            ARC_USDC,
            "--output-token",
            token,
            "--amount",
            raw,
        ],
        timeout=timeout,
    )


def swap_sell_token_to_usdc(
    chain: str,
    token: str,
    percent: float,
    *,
    timeout: float = 90,
) -> tuple[dict | None, str | None]:
    """Sell percent of token holdings to Arc USDC (1–100)."""
    token = (token or "").strip()
    if not token.startswith("0x"):
        return None, "bad_token"
    pct = max(1, min(100, int(round(float(percent)))))
    return _run_swap(
        [
            "--chain",
            chain,
            "--input-token",
            token,
            "--output-token",
            ARC_USDC,
            "--percent",
            str(pct),
        ],
        timeout=timeout,
    )


def summarize_swap_result(data: dict | None) -> dict[str, Any]:
    """Safe fields for book/discord (no secrets)."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("hash", "tx_hash", "txid", "order_id", "id", "status", "code"):
        if data.get(k) is not None:
            out[k] = data.get(k)
    nested = data.get("data")
    if isinstance(nested, dict):
        for k in ("hash", "tx_hash", "txid", "order_id", "id", "status"):
            if nested.get(k) is not None and k not in out:
                out[k] = nested.get(k)
    return out

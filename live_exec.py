"""Arc live swap execution via Uniswap V4 Universal Router (no gmgn-cli swap).

Never logs secrets. Interface kept stable for live_tick / live_trade:
  swap_buy_usdc_to_token / swap_sell_token_to_usdc / fetch_usdc_balance_usd
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ARC_USDC = "0x3600000000000000000000000000000000000000"
ARC_USDC_DECIMALS = 6
ARC_CHAIN_ID = 5042
ARC_RPC = os.environ.get("ARC_RPC") or "https://rpc.mainnet.arc.io"

ROOT = Path(__file__).resolve().parent
SWAP_SCRIPT = ROOT / "arc_swap" / "swap_v4.cjs"

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


def _ensure_pk() -> bool:
    """Load GMGN_PRIVATE_KEY from box-secrets / dotenv into env. Never print it."""
    try:
        from load_secrets import load, write_gmgn_dotenv
    except ImportError:
        load = None  # type: ignore
        write_gmgn_dotenv = None  # type: ignore
    if load:
        load(["GMGN_PRIVATE_KEY", "GMGN_API_KEY"])
    if write_gmgn_dotenv:
        write_gmgn_dotenv()
    if (os.environ.get("GMGN_PRIVATE_KEY") or "").strip():
        return True
    # fallback: live-arc/.gmgn.env or ~/.config/gmgn/.env
    for path in (
        Path("/workspace/meme-foundation/live-arc/.gmgn.env"),
        Path.home() / ".config" / "gmgn" / ".env",
    ):
        if not path.exists():
            continue
        try:
            for ln in path.read_text(encoding="utf-8").splitlines():
                if ln.startswith("GMGN_PRIVATE_KEY=") and ln.split("=", 1)[1].strip():
                    os.environ["GMGN_PRIVATE_KEY"] = ln.split("=", 1)[1].strip()
                    return True
                if ln.startswith("PRIVATE_KEY=") and ln.split("=", 1)[1].strip():
                    os.environ["GMGN_PRIVATE_KEY"] = ln.split("=", 1)[1].strip()
                    return True
        except OSError:
            continue
    return bool((os.environ.get("GMGN_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY") or "").strip())


def fetch_usdc_balance_usd(chain: str = "arc", timeout: float = 35) -> tuple[float | None, str | None]:
    """On-chain Arc USDC balance in USD units. Soft-fail → (None, err)."""
    if chain and chain != "arc":
        return None, "unsupported_chain"
    w = wallet_address()
    if not w:
        return None, "no_wallet"
    # eth_call balanceOf via cast/curl-style node one-liner (no pk needed)
    script = f"""
const {{ethers}}=require('ethers');
(async()=>{{
  const p=new ethers.JsonRpcProvider({json.dumps(ARC_RPC)},{ARC_CHAIN_ID});
  const c=new ethers.Contract({json.dumps(ARC_USDC)},['function balanceOf(address) view returns (uint256)'],p);
  const b=await c.balanceOf({json.dumps(w)});
  console.log(b.toString());
}})().catch(e=>{{console.error(e.message);process.exit(1);}});
"""
    try:
        proc = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT / "arc_swap"),
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, type(e).__name__
    if proc.returncode != 0:
        err = _redact((proc.stderr or "") + " " + (proc.stdout or ""))
        print(f"live_exec usdc_balance fail: {err.strip()[:240]}", flush=True)
        return None, "rpc"
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return None, "empty"
    try:
        raw = int(out[-1].strip())
    except ValueError:
        return None, "bad_json"
    return raw / (10**ARC_USDC_DECIMALS), None


def _parse_swap_stdout(stdout: str) -> dict | None:
    """Take last JSON line with step=done or ok/hash."""
    done = None
    for ln in (stdout or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("step") == "done" or obj.get("ok") is True or obj.get("hash") or obj.get("tx_hash"):
            done = obj
    return done


def _run_v4_swap(env_extra: dict[str, str], timeout: float = 120) -> tuple[dict | None, str | None]:
    if not _ensure_pk():
        return None, "no_key"
    if not SWAP_SCRIPT.exists():
        print(f"live_exec missing swap script: {SWAP_SCRIPT}", flush=True)
        return None, "no_script"
    env = {**os.environ, **env_extra}
    # Prefer GMGN_PRIVATE_KEY name the script already reads
    if not env.get("GMGN_PRIVATE_KEY") and env.get("PRIVATE_KEY"):
        env["GMGN_PRIVATE_KEY"] = env["PRIVATE_KEY"]
    side = env_extra.get("SIDE", "?")
    token = (env_extra.get("TOKEN") or "")[:12]
    print(f"live_exec v4_swap side={side} token={token}…", flush=True)
    try:
        proc = subprocess.run(
            ["node", str(SWAP_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(SWAP_SCRIPT.parent),
        )
    except subprocess.TimeoutExpired:
        print("live_exec v4_swap timeout", flush=True)
        return None, "timeout"
    except Exception as e:
        print(f"live_exec v4_swap spawn fail: {type(e).__name__}", flush=True)
        return None, "spawn"
    out = proc.stdout or ""
    err = _redact((proc.stderr or "") + "\n" + out)
    tail = err.strip()[-800:] if err.strip() else ""
    # Always echo step lines (no secrets in them)
    for ln in out.splitlines():
        if ln.startswith("{"):
            print(f"live_exec {ln[:300]}", flush=True)
    if proc.returncode != 0:
        print(f"live_exec v4_swap fail rc={proc.returncode}: {tail}", flush=True)
        # classify
        if "insufficient" in tail.lower():
            return None, "insufficient"
        if "missing private key" in tail.lower():
            return None, "no_key"
        if "no_v4_pool" in tail.lower():
            return None, "no_v4_pool"
        return None, "other"
    data = _parse_swap_stdout(out)
    if not data:
        print(f"live_exec v4_swap no done line: {tail}", flush=True)
        return None, "empty"
    if data.get("ok") is False or data.get("status") not in (1, "1", None):
        if data.get("status") not in (1, "1"):
            print(f"live_exec v4_swap bad status: {data}", flush=True)
            return data, "revert"
    # Normalize fields live_trade expects
    if "tx_hash" not in data and data.get("hash"):
        data["tx_hash"] = data["hash"]
    if "hash" not in data and data.get("tx_hash"):
        data["hash"] = data["tx_hash"]
    return data, None


def swap_buy_usdc_to_token(
    chain: str,
    token: str,
    amount_usd: float,
    *,
    timeout: float = 120,
) -> tuple[dict | None, str | None]:
    """Buy token with Arc USDC. amount_usd is human USD."""
    if chain and chain != "arc":
        return None, "unsupported_chain"
    raw = usdc_raw_from_usd(amount_usd)
    if raw == "0":
        return None, "zero_amount"
    token = (token or "").strip()
    if not token.startswith("0x"):
        return None, "bad_token"
    return _run_v4_swap(
        {
            "SIDE": "buy",
            "TOKEN": token,
            "AMOUNT_USD": str(amount_usd),
            "SLIPPAGE_BPS": os.environ.get("LIVE_SLIPPAGE_BPS") or "5000",
            "DISCOVER_POOL": os.environ.get("LIVE_DISCOVER_POOL") or "1",
            "RPC": ARC_RPC,
        },
        timeout=timeout,
    )


def swap_sell_token_to_usdc(
    chain: str,
    token: str,
    percent: float,
    *,
    timeout: float = 120,
) -> tuple[dict | None, str | None]:
    """Sell percent of token holdings to Arc USDC (1–100)."""
    if chain and chain != "arc":
        return None, "unsupported_chain"
    token = (token or "").strip()
    if not token.startswith("0x"):
        return None, "bad_token"
    pct = max(1, min(100, int(round(float(percent)))))
    return _run_v4_swap(
        {
            "SIDE": "sell",
            "TOKEN": token,
            "PERCENT": str(pct),
            "SLIPPAGE_BPS": os.environ.get("LIVE_SLIPPAGE_BPS") or "5000",
            "DISCOVER_POOL": os.environ.get("LIVE_DISCOVER_POOL") or "1",
            "RPC": ARC_RPC,
        },
        timeout=timeout,
    )


def summarize_swap_result(data: dict | None) -> dict[str, Any]:
    """Safe fields for book/discord (no secrets)."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for k in (
        "hash", "tx_hash", "txid", "order_id", "id", "status", "code",
        "side", "amountIn", "amountOutReceived", "amountInSpent", "fee", "tickSpacing",
        "decimals", "tokenDecimals", "quoteKind", "poolIsNative", "quoteIsNative",
        "fillPriceUsd", "amountOutQuoted",
    ):
        if data.get(k) is not None:
            out[k] = data.get(k)
    nested = data.get("data")
    if isinstance(nested, dict):
        for k in ("hash", "tx_hash", "txid", "order_id", "id", "status"):
            if nested.get(k) is not None and k not in out:
                out[k] = nested.get(k)
    return out

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
POOL_CACHE_PATH = Path(
    os.environ.get("LIVE_POOL_CACHE")
    or "/workspace/meme-foundation/live-arc/pool_cache.json"
)
POOL_CACHE_MAX_AGE_SEC = float(os.environ.get("LIVE_POOL_CACHE_MAX_AGE_SEC") or str(24 * 3600))

_SECRET_RE = re.compile(
    r"(GMGN_API_KEY|API[_-]?KEY|PRIVATE[_-]?KEY|WALLET_PRIVATE_KEY|apikey)"
    r"[=:\s]+\S+",
    re.I,
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text or "")


def _pool_cache_load() -> dict:
    try:
        if POOL_CACHE_PATH.exists():
            raw = json.loads(POOL_CACHE_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _pool_cache_save(cache: dict) -> None:
    try:
        POOL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = POOL_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(POOL_CACHE_PATH)
    except OSError as e:
        print(f"live_exec pool_cache save fail: {type(e).__name__}", flush=True)


def get_pool_cache(token: str) -> dict | None:
    """Return fresh cached pool key for token, or None if miss/stale."""
    import time as _time

    key = (token or "").strip().lower()
    if not key:
        return None
    ent = _pool_cache_load().get(key)
    if not isinstance(ent, dict):
        return None
    try:
        updated = float(ent.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated = 0.0
    if updated and (_time.time() - updated) > POOL_CACHE_MAX_AGE_SEC:
        return None
    if ent.get("fee") is None or ent.get("tickSpacing") is None:
        return None
    return ent


def put_pool_cache(token: str, data: dict | None, *, source: str = "swap") -> None:
    """Persist pool key from a successful swap result."""
    import time as _time

    if not isinstance(data, dict):
        return
    key = (token or "").strip().lower()
    if not key:
        return
    fee = data.get("fee")
    tick = data.get("tickSpacing")
    if fee is None or tick is None:
        return
    c0 = data.get("currency0")
    c1 = data.get("currency1")
    hooks = data.get("hooks") or "0x0000000000000000000000000000000000000000"
    pool_native = data.get("poolIsNative")
    if pool_native is None and data.get("quoteIsNative") is not None:
        pool_native = data.get("quoteIsNative")
    ent = {
        "fee": int(fee),
        "tickSpacing": int(tick),
        "hooks": hooks,
        "currency0": c0,
        "currency1": c1,
        "poolIsNative": bool(pool_native) if pool_native is not None else None,
        "updated_at": _time.time(),
        "source": source,
    }
    cache = _pool_cache_load()
    cache[key] = ent
    _pool_cache_save(cache)
    print(
        f"live_exec pool_cache_write token={key[:12]}… fee={ent['fee']} "
        f"tick={ent['tickSpacing']} native={ent['poolIsNative']}",
        flush=True,
    )


def _pool_env_from_cache(token: str) -> tuple[dict[str, str], bool]:
    """Build node env overrides from cache. Returns (env, hit_with_currencies)."""
    ent = get_pool_cache(token)
    if not ent:
        return {}, False
    out: dict[str, str] = {
        "POOL_FEE": str(int(ent["fee"])),
        "TICK_SPACING": str(int(ent["tickSpacing"])),
    }
    hooks = ent.get("hooks")
    if hooks:
        out["HOOKS"] = str(hooks)
    c0, c1 = ent.get("currency0"), ent.get("currency1")
    full = bool(c0 and c1)
    if full:
        out["CURRENCY0"] = str(c0)
        out["CURRENCY1"] = str(c1)
        out["DISCOVER_POOL"] = "0"
        if ent.get("poolIsNative") is True:
            out["POOL_IS_NATIVE"] = "1"
            out["POOL_QUOTE"] = "native"
        elif ent.get("poolIsNative") is False:
            out["POOL_IS_NATIVE"] = "0"
            out["POOL_QUOTE"] = "usdc"
        print(
            f"live_exec pool_cache_hit token={(token or '')[:12]}… "
            f"fee={out['POOL_FEE']} tick={out['TICK_SPACING']}",
            flush=True,
        )
    else:
        # Partial (fee/tick only): prefer those but still allow discover/fallback
        if ent.get("poolIsNative") is True:
            out["POOL_QUOTE"] = "native"
        elif ent.get("poolIsNative") is False:
            out["POOL_QUOTE"] = "usdc"
        out["DISCOVER_POOL"] = "1"
        print(
            f"live_exec pool_cache_partial token={(token or '')[:12]}… "
            f"fee={out['POOL_FEE']} tick={out['TICK_SPACING']}",
            flush=True,
        )
    return out, full


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
        low = tail.lower()
        # classify
        if (
            "zero sell amount" in low
            or '"tokenbal":"0"' in low.replace(" ", "")
            or '"tokenbal": "0"' in low
            or "tokenbal=0" in low.replace(" ", "")
            or ("tokenbal" in low and ': "0"' in low)
            or ("tokenbal" in low and ':"0"' in low.replace(" ", ""))
        ):
            return None, "already_flat"
        if "insufficient" in low:
            return None, "insufficient"
        if "missing private key" in low:
            return None, "no_key"
        if "no_v4_pool" in low:
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


def _swap_with_pool_cache(
    *,
    side: str,
    token: str,
    base_env: dict[str, str],
    timeout: float = 120,
) -> tuple[dict | None, str | None]:
    """Run V4 swap; prefer cached pool; rediscover on quote/cache miss failure."""
    cache_env, full_hit = _pool_env_from_cache(token)
    env = {
        "SIDE": side,
        "TOKEN": token,
        "SLIPPAGE_BPS": os.environ.get("LIVE_SLIPPAGE_BPS") or "5000",
        "DISCOVER_POOL": os.environ.get("LIVE_DISCOVER_POOL") or "1",
        "RPC": ARC_RPC,
        **base_env,
        **cache_env,
    }
    data, err = _run_v4_swap(env, timeout=timeout)
    if data is not None and err is None:
        put_pool_cache(token, data, source=f"swap_{side}")
        return data, None
    # Cached full key failed (quote/revert) → one rediscover retry
    if full_hit and err not in ("already_flat", "no_key", "insufficient", "zero_amount", "bad_token"):
        print(
            f"live_exec pool_cache_miss_retry token={token[:12]}… err={err} — rediscover",
            flush=True,
        )
        retry = {
            "SIDE": side,
            "TOKEN": token,
            "SLIPPAGE_BPS": os.environ.get("LIVE_SLIPPAGE_BPS") or "5000",
            "DISCOVER_POOL": "1",
            "RPC": ARC_RPC,
            **base_env,
        }
        data2, err2 = _run_v4_swap(retry, timeout=timeout)
        if data2 is not None and err2 is None:
            put_pool_cache(token, data2, source=f"swap_{side}_rediscover")
            return data2, None
        return data2, err2
    return data, err


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
    return _swap_with_pool_cache(
        side="buy",
        token=token,
        base_env={"AMOUNT_USD": str(amount_usd)},
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
    return _swap_with_pool_cache(
        side="sell",
        token=token,
        base_env={"PERCENT": str(pct)},
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
        "hooks", "currency0", "currency1",
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

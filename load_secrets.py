"""Load API secrets into os.environ without printing values."""
from __future__ import annotations

import json
import os
from pathlib import Path

SECRETS_PATH = Path("/home/box/sand-data/box-secrets.json")
MAP = {
    "NANSEN_API_KEY": ("card", "NANSEN_API_KEY"),
    "GMGN_API_KEY": ("card", "GMGN_API_KEY"),
    "DISCORD_WEBHOOK_URL": ("card", "DISCORD_WEBHOOK_URL"),
    "DISCORD_PAPER_WEBHOOK_URL": ("card", "DISCORD_PAPER_WEBHOOK_URL"),
    "DISCORD_ARC_WEBHOOK_URL": ("card", "DISCORD_ARC_WEBHOOK_URL"),
    "DISCORD_ARC_PAPER": ("card", "DISCORD_ARC_PAPER"),
    "DISCORD_ARC_LIVE_WEBHOOK_URL": ("card", "DISCORD_ARC_LIVE_WEBHOOK_URL"),
    "DISCORD_XBTSCOUT_WEBHOOK_URL": ("card", "DISCORD_XBTSCOUT_WEBHOOK_URL"),
    "DISCORD_PRIORITY_WEBHOOK_URL": ("card", "DISCORD_PRIORITY_WEBHOOK_URL"),
    "FOMO_API_KEY": ("card", "FOMO_API_KEY"),
    "GMGN_PRIVATE_KEY": ("card", "GMGN_PRIVATE_KEY"),
}


def load(names: list[str] | None = None) -> dict[str, bool]:
    status: dict[str, bool] = {}
    data: dict = {}
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    wanted = names or list(MAP)
    for name in wanted:
        if name not in MAP:
            status[name] = bool(os.environ.get(name))
            continue
        section, key = MAP[name]
        val = (data.get(section) or {}).get(key)
        if not (isinstance(val, str) and val.strip()) and name == "GMGN_PRIVATE_KEY":
            for sec, k in (
                ("card", "GMGN_PRIVATE_KEY"),
                ("card", "PRIVATE_KEY"),
                ("desktop", "WALLET_PRIVATE_KEY"),
                ("desktop", "GMGN_PRIVATE_KEY"),
            ):
                v = (data.get(sec) or {}).get(k)
                if isinstance(v, str) and v.strip():
                    val = v
                    break
        if name == "GMGN_API_KEY":
            # Prefer real gmgn_… keys; skip desktop junk like "Inst…"
            candidates = []
            for sec, k in (("card", "GMGN_API_KEY"), ("desktop", "GMGN_API_KEY")):
                v = (data.get(sec) or {}).get(k)
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())
            env_v = (os.environ.get("GMGN_API_KEY") or "").strip()
            if env_v:
                candidates.insert(0, env_v)
            picked = None
            for c in candidates:
                if c.lower().startswith("gmgn") or len(c) >= 20:
                    # reject obvious placeholders
                    if c.lower().startswith("inst"):
                        continue
                    picked = c
                    break
            if picked:
                val = picked
        ok = isinstance(val, str) and bool(val.strip())
        if ok:
            cur = (os.environ.get(name) or "").strip()
            # overwrite missing OR obviously bad API placeholders already in env
            bad_api = name == "GMGN_API_KEY" and (
                not cur or cur.lower().startswith("inst") or not (
                    cur.lower().startswith("gmgn") or len(cur) >= 20
                )
            )
            if name not in os.environ or bad_api or (name == "GMGN_PRIVATE_KEY" and not cur):
                os.environ[name] = val.strip()
        status[name] = bool(os.environ.get(name))
    return status


def write_gmgn_dotenv(path: Path | None = None) -> bool:
    """Write ~/.config/gmgn/.env from env/box-secrets. Never print the key."""
    load(["GMGN_API_KEY", "GMGN_PRIVATE_KEY"])
    key = (os.environ.get("GMGN_API_KEY") or "").strip()
    if not key:
        return False
    dest = path or (Path.home() / ".config" / "gmgn" / ".env")
    dest.parent.mkdir(parents=True, exist_ok=True)
    pk = (os.environ.get("GMGN_PRIVATE_KEY") or "").strip()
    if not pk and dest.exists():
        for ln in dest.read_text(encoding="utf-8").splitlines():
            if ln.startswith("GMGN_PRIVATE_KEY=") and ln.split("=", 1)[1].strip():
                pk = ln.split("=", 1)[1].strip()
                os.environ["GMGN_PRIVATE_KEY"] = pk
                break
    lines = [f"GMGN_API_KEY={key}"]
    if pk:
        lines.append(f"GMGN_PRIVATE_KEY={pk}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return True



if __name__ == "__main__":
    st = load()
    for k, v in st.items():
        print(f"{k}: {'ok' if v else 'missing'}")
    ok = write_gmgn_dotenv()
    print(f"gmgn_dotenv: {'written' if ok else 'skipped'}")

"""Load API secrets into os.environ without printing values."""
from __future__ import annotations

import json
import os
from pathlib import Path

SECRETS_PATH = Path("/home/box/sand-data/box-secrets.json")
MAP = {
    "NANSEN_API_KEY": ("card", "NANSEN_API_KEY"),
    "GMGN_API_KEY": ("desktop", "GMGN_API_KEY"),
    "DISCORD_WEBHOOK_URL": ("card", "DISCORD_WEBHOOK_URL"),
    "DISCORD_PAPER_WEBHOOK_URL": ("card", "DISCORD_PAPER_WEBHOOK_URL"),
    "DISCORD_ARC_WEBHOOK_URL": ("card", "DISCORD_ARC_WEBHOOK_URL"),
    "DISCORD_ARC_PAPER": ("card", "DISCORD_ARC_PAPER"),
    "DISCORD_ARC_LIVE_WEBHOOK_URL": ("card", "DISCORD_ARC_LIVE_WEBHOOK_URL"),
    "FOMO_API_KEY": ("card", "FOMO_API_KEY"),
    "GMGN_PRIVATE_KEY": ("desktop", "GMGN_PRIVATE_KEY"),
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
        ok = isinstance(val, str) and bool(val.strip())
        if ok and name not in os.environ:
            os.environ[name] = val.strip()
        status[name] = bool(os.environ.get(name))
    return status


def write_gmgn_dotenv(path: Path | None = None) -> bool:
    """Write ~/.config/gmgn/.env from env/box-secrets. Never print the key."""
    load(["GMGN_API_KEY", "GMGN_PRIVATE_KEY"])
    key = (os.environ.get("GMGN_API_KEY") or "").strip()
    if not key:
        return False
    dest = path or (Path.home() / ".config/gmgn/.env")
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"GMGN_API_KEY={key}"]
    pk = (os.environ.get("GMGN_PRIVATE_KEY") or "").strip()
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

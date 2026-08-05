"""Load .env into environment then run bot.py (no external deps)."""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / ".env"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        print(f"[start] .env not found: {path}")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    load_dotenv(ENV_FILE)
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        print("[start] WARNING: DASHSCOPE_API_KEY is empty! Chat will not work.")
    else:
        print(f"[start] DASHSCOPE_API_KEY loaded (len={len(key)})")
    py = sys.executable
    os.chdir(BASE)
    raise SystemExit(subprocess.call([py, "bot.py"], env=os.environ))


if __name__ == "__main__":
    main()

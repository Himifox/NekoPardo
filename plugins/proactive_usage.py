import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from plugins.config import PROACTIVE_USAGE_FILE
from plugins.file_lock import file_lock

BASE_DIR = Path(__file__).resolve().parent.parent


def _today() -> str:
    return datetime.now().date().isoformat()


def _usage_path() -> Path:
    path = Path(PROACTIVE_USAGE_FILE)
    return path if path.is_absolute() else BASE_DIR / path


def _read_count(path: Path, day: str) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("date"), str):
        raise ValueError("Invalid proactive usage record")
    count = data.get("count")
    if type(count) is not int or count < 0:
        raise ValueError("Invalid proactive usage count")
    return count if data["date"] == day else 0


def get_proactive_daily_count() -> int:
    return _read_count(_usage_path(), _today())


def increment_proactive_daily_count() -> int:
    path = _usage_path()
    with file_lock(Path(str(path) + ".lock")):
        day = _today()
        count = _read_count(path, day) + 1
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"date": day, "count": count}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return count

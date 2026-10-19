import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from virtualme.config import LONG_TERM_MEMORY_FILE

logger = logging.getLogger(__name__)
from virtualme.paths import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT


def memory_file_path() -> Path:
    path = Path(LONG_TERM_MEMORY_FILE)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def build_memory_record(
    *,
    content: str,
    role: str,
    group_id: int,
    sender: str,
    user_id: Optional[int | str] = None,
    timestamp: Optional[int | float] = None,
    account_id: Optional[str] = None,
    session_type: Optional[str] = None,
    message_id: Optional[str] = None,
    raw_content: Optional[str] = None,
    segments: Optional[tuple] = None,
    reply_to: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    clean_content = (content or "").strip()
    if not clean_content:
        return None

    now = time.time() if timestamp is None else timestamp
    try:
        unix_timestamp = float(now)
    except (TypeError, ValueError):
        unix_timestamp = time.time()

    record = {
        "id": str(uuid.uuid4()),
        "content": clean_content,
        "role": role,
        "sender": sender or "unknown",
        "user_id": str(user_id or ""),
        "group_id": int(group_id),
        "timestamp": unix_timestamp,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(unix_timestamp)),
    }
    for key, value in {'account_id': account_id, 'session_type': session_type,
                       'message_id': message_id, 'raw_content': raw_content,
                       'segments': segments, 'reply_to': reply_to}.items():
        if value is not None:
            record[key] = value
    return record


def append_memory_record(record: Optional[dict[str, Any]]) -> bool:
    if not record:
        return False

    try:
        path = memory_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:
        logger.exception("Failed to append long-term memory record")
        return False


def save_memory_entry(
    *,
    content: str,
    role: str,
    group_id: int,
    sender: str,
    user_id: Optional[int | str] = None,
    timestamp: Optional[int | float] = None,
    **metadata,
) -> bool:
    record = build_memory_record(
        content=content,
        role=role,
        group_id=group_id,
        sender=sender,
        user_id=user_id,
        timestamp=timestamp,
        **metadata,
    )
    return append_memory_record(record)


def iter_memory_records():
    path = memory_file_path()
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def count_memory_records(group_id: Optional[int] = None) -> int:
    count = 0
    for record in iter_memory_records() or []:
        if group_id is not None and int(record.get("group_id", -1)) != int(group_id):
            continue
        count += 1
    return count

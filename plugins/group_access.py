"""Shared, atomic whitelist updates for the bot and admin processes."""

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from plugins.file_lock import file_lock

BASE_DIR = Path(__file__).resolve().parent.parent


def group_file_path(path: str | Path) -> Path:
    path = Path(path)
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


def _group_ids(values: Iterable) -> set[int]:
    result = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("Invalid group ID")
        group_id = int(value)
        if group_id <= 0:
            raise ValueError("Group IDs must be positive")
        result.add(group_id)
    return result


def read_active_groups(path: str | Path) -> set[int]:
    path = group_file_path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Whitelist must be an array")
    return _group_ids(data)


def update_active_groups(path: str | Path, *, add: Iterable = (), remove: Iterable = ()) -> set[int]:
    path = group_file_path(path)
    additions, removals = _group_ids(add), _group_ids(remove)
    with file_lock(path.with_name(path.name + ".lock")):
        groups = (read_active_groups(path) | additions) - removals
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as file:
                temporary = Path(file.name)
                json.dump(sorted(groups), file)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return groups

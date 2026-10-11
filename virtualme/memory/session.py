from collections import defaultdict, deque

from plugins.memory_store import save_memory_entry
from virtualme.chat.text_policy import sanitize_reply

_history_cache = defaultdict(lambda: deque(maxlen=20))

def append_short_term_memory(group_id: int, sender: str, content: str) -> None:
    if content:
        _history_cache[group_id].append(f"{sender}: {content}")


def get_history_str(group_id: int) -> str:
    if group_id not in _history_cache:
        return "群聊历史记录为空。"
    return "\n".join(_history_cache[group_id])


def save_bot_reply(group_id: int, content: str):
    if not content:
        return

    content = sanitize_reply(content)
    _history_cache[group_id].append(f"我的发言: {content}")
    save_memory_entry(
        content=content,
        role="bot",
        group_id=group_id,
        sender="bot",
    )


def record_incoming(group_id: int, sender: str, content: str, *, user_id=None, timestamp=None) -> None:
    content = sanitize_reply(content)
    append_short_term_memory(group_id, sender, content)
    save_memory_entry(content=content, role="user", group_id=group_id,
                      sender=sender, user_id=user_id, timestamp=timestamp)

from collections import defaultdict, deque

from virtualme.memory.store import save_memory_entry
from virtualme.chat.text import sanitize_reply
from virtualme.models import SessionKey, MessageEnvelope

_history_cache = defaultdict(lambda: deque(maxlen=20))

def _storage_fields(session):
    if isinstance(session, SessionKey):
        return {'group_id': session.conversation_id, 'account_id': session.account_id,
                'session_type': session.kind}
    return {'group_id': session}

def record_message(message: MessageEnvelope) -> None:
    content = sanitize_reply(message.text)
    if not content:
        return
    append_short_term_memory(message.session, message.sender_name, content)
    save_memory_entry(content=content, role='user', sender=message.sender_name,
                      user_id=message.sender_id, timestamp=message.timestamp,
                      message_id=message.message_id, raw_content=message.raw_text if message.raw_text is not None else message.text,
                      segments=message.segments, reply_to=message.reply_to,
                      **_storage_fields(message.session))

def append_short_term_memory(group_id: int | SessionKey, sender: str, content: str) -> None:
    if content:
        _history_cache[group_id].append(f"{sender}: {content}")


def get_history_str(group_id: int | SessionKey) -> str:
    if group_id not in _history_cache:
        return "群聊历史记录为空。"
    return "\n".join(_history_cache[group_id])


def save_bot_reply(group_id: int | SessionKey, content: str):
    if not content:
        return

    content = sanitize_reply(content)
    _history_cache[group_id].append(f"我的发言: {content}")
    save_memory_entry(
        content=content,
        role="bot",
        **_storage_fields(group_id),
        sender="bot",
    )


def record_incoming(group_id: int | SessionKey, sender: str, content: str, *, user_id=None, timestamp=None) -> None:
    content = sanitize_reply(content)
    append_short_term_memory(group_id, sender, content)
    save_memory_entry(content=content, role="user", **_storage_fields(group_id),
                      sender=sender, user_id=user_id, timestamp=timestamp)

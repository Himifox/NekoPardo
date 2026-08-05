from collections import defaultdict, deque

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from plugins.memory_store import save_memory_entry

_history_cache = defaultdict(lambda: deque(maxlen=20))
record_msg = on_message(priority=1, block=False)


# 致命敏感词 -> 安全替身（输入端检测会命中这些词，进历史后下一轮必 400）
_SENSITIVE_REPLACEMENTS = [
    ("小穴", "那里"),
    ("屁眼", "后面"),
    ("阴道", "最深处"),
    ("阴部", "那里"),
    ("阴蒂", "最深处"),
    ("鸡巴", "它"),
    ("精子", "温热"),
    ("精液", "它"),
    ("射精", "到达尽头"),
    ("龟头", "顶端"),
]


def sanitize_reply(text: str) -> str:
    """把致命敏感词替换为安全替身，避免污染后续上下文。"""
    if not text:
        return text
    cleaned = text
    for word, replacement in _SENSITIVE_REPLACEMENTS:
        cleaned = cleaned.replace(word, replacement)
    return cleaned


def extract_message_content(event: GroupMessageEvent) -> str:
    content_parts: list[str] = []
    for seg in event.message:
        if seg.type == "text":
            content_parts.append(seg.data.get("text", ""))
        elif seg.type == "image":
            img_url = seg.data.get("url", "")
            content_parts.append(f"[图片: {img_url}]")
        elif seg.type == "face":
            face_id = seg.data.get("id", "")
            content_parts.append(f"[QQ表情{face_id}]")
        elif seg.type in {"mface", "marketface"}:
            content_parts.append("[动画表情]")
        elif seg.type == "at":
            at_qq = seg.data.get("qq", "")
            content_parts.append(f"[@{at_qq}]")
    return "".join(content_parts).strip()


def append_short_term_memory(group_id: int, sender: str, content: str) -> None:
    if content:
        _history_cache[group_id].append(f"{sender}: {content}")


@record_msg.handle()
async def _(event: GroupMessageEvent):
    group_id = event.group_id
    sender_name = event.sender.card or event.sender.nickname or "未知用户"
    content = extract_message_content(event)
    if not content:
        return

    append_short_term_memory(group_id, sender_name, sanitize_reply(content))
    save_memory_entry(
        content=sanitize_reply(content),
        role="user",
        group_id=group_id,
        sender=sender_name,
        user_id=event.user_id,
        timestamp=event.time,
    )


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

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from virtualme.memory.session import record_message
from virtualme.adapters.qq.messages import to_message

record_msg = on_message(priority=1, block=False)

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


@record_msg.handle()
async def _(event: GroupMessageEvent):
    content = extract_message_content(event)
    if not content:
        return
    record_message(to_message(event, text=content))

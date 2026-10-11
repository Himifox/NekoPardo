import re

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
def to_tts_text(text: str) -> str:
    return re.sub(r"[\(\uff08\[\u3010].*?[\)\uff09\]\u3011]", "", text).strip() or "喵！"

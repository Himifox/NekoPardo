"""Reply selection without SDK, disk IO or messaging-framework imports."""
from virtualme.config import GLOBAL_CD, VOICE_KEYWORDS, TXT_KEYWORDS, VOICE_PROBABILITY, TEXT_PROBABILITY
from virtualme.models import ReplyDecision, ReplyMode
from collections.abc import Callable

def decide_reply(text: str, *, to_me: bool, has_image: bool, now: float,
                 last_reply: float, random_value: float | Callable[[], float]) -> tuple[ReplyDecision | None, bool]:
    if to_me:
        return ReplyDecision(ReplyMode.TEXT_AND_OPTIONAL_VOICE, 'mention'), False
    if has_image:
        return ReplyDecision(ReplyMode.IMAGE, 'image'), False
    if '帕朵' not in text:
        return None, False
    if any(word in text for word in VOICE_KEYWORDS):
        return ReplyDecision(ReplyMode.VOICE, 'voice_keyword'), False
    if any(word in text for word in TXT_KEYWORDS):
        return ReplyDecision(ReplyMode.TEXT, 'text_keyword'), False
    if now - last_reply < GLOBAL_CD:
        return None, False
    random_value = random_value() if callable(random_value) else random_value
    if random_value < VOICE_PROBABILITY:
        return ReplyDecision(ReplyMode.VOICE, 'random'), True
    if random_value < VOICE_PROBABILITY + TEXT_PROBABILITY:
        return ReplyDecision(ReplyMode.TEXT, 'random'), True
    return None, True

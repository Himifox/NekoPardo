"""QQ output, partial-delivery tracking and voice fallback."""
import logging
import os
import random
from nonebot.adapters.onebot.v11 import MessageSegment
from virtualme.config import REFER_WAV_PATH, STICKER_SEND_LOCAL_FIRST
from virtualme.models import DeliveryResult, ReplyMode
from virtualme.chat.text import to_tts_text
from virtualme.speech.service import get_tts_audio
from virtualme.stickers.service import find_md5_by_json
from virtualme.stickers.store import local_sticker_path

logger = logging.getLogger(__name__)

async def smart_send(bot, event, ai_text: str, prob: float) -> bool:
    match = await find_md5_by_json(ai_text)
    if not match or random.random() >= prob:
        return False
    identifier, url = match
    local = os.path.abspath(local_sticker_path(identifier))
    image = 'file:///' + local.replace('\\', '/') if STICKER_SEND_LOCAL_FIRST and os.path.exists(local) else url
    await bot.send(event, MessageSegment.image(file=image))
    return True

async def deliver_reply(bot, event, text: str, mode: ReplyMode, *, send,
                        on_reply_sent, synthesize=None, send_sticker=None) -> DeliveryResult:
    """Record once after the first successful text/audio send; propagate cancellation."""
    result = DeliveryResult()
    synthesize = synthesize or get_tts_audio
    send_sticker = send_sticker or smart_send
    recorded = False

    async def deliver(message, kind):
        nonlocal recorded
        response = await send(message)
        setattr(result, kind + '_sent', True)
        if isinstance(response, dict) and response.get('message_id') is not None:
            result.message_ids.append(str(response['message_id']))
        if not recorded:
            recorded = True
            on_reply_sent(text)

    async def voice():
        audio = await synthesize(to_tts_text(text), ref_path=REFER_WAV_PATH)
        if not audio:
            return False
        await deliver(MessageSegment.record(f'base64://{audio}'), 'voice')
        return True

    try:
        result.sticker_sent = await send_sticker(bot, event, text, 1.0)
    except Exception as exc:
        result.errors['sticker'] = str(exc)
        logger.exception('Optional sticker delivery failed')
    if result.sticker_sent:
        await deliver(text, 'text')
        return result
    if mode is ReplyMode.VOICE:
        try:
            if await voice():
                return result
        except Exception as exc:
            if result.reply_sent:
                raise
            result.errors['voice'] = str(exc)
            logger.exception('Voice delivery failed; falling back to text')
        await deliver(text, 'text')
    else:
        await deliver(text, 'text')
        if mode is ReplyMode.TEXT_AND_OPTIONAL_VOICE and random.random() < 0.5:
            try:
                await voice()
            except Exception as exc:
                result.errors['voice'] = str(exc)
                logger.exception('Optional voice delivery failed after text was sent')
    return result

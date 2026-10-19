"""QQ chat adapter: authorization, message conversion and delivery."""
import logging
import random
import time
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.permission import PRIVATE
from nonebot.exception import FinishedException

from virtualme.config import ADMIN_UID, ACTIVATE_COMMAND, REFER_WAV_PATH, WHITE_LIST_FILE
from virtualme.access import active_groups, save_white_list, is_group_active, get_active_group_ids
from virtualme.chat.policy import decide_reply
from virtualme.chat.service import generate_pardo_reply, record_delivered_reply as save_reply_and_maybe_index
from virtualme.memory.session import record_message
from virtualme.adapters.qq.messages import to_message
from virtualme.models import ReplyMode, SessionKey
from virtualme.stickers.service import qwen_recognize_sticker
from virtualme.speech.service import get_tts_audio
from virtualme.adapters.qq.delivery import deliver_reply, smart_send

logger = logging.getLogger(__name__)
last_reply_time: dict[SessionKey, float] = {}
mimic_chat = on_message(priority=2, block=False)
private_chat = on_message(permission=PRIVATE, priority=2, block=True)

@mimic_chat.handle()
async def handle_chat(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    text = event.get_plaintext().strip()
    if str(event.user_id).strip() == ADMIN_UID and ACTIVATE_COMMAND in text:
        if is_group_active(group_id):
            await mimic_chat.finish('老板，咱一直都在这儿呢！')
        try:
            save_white_list({group_id})
        except (OSError, ValueError, TypeError):
            logger.exception('Failed to activate group %s', group_id)
            await mimic_chat.finish('启用失败：白名单无法写入，请检查文件后重试。')
        await mimic_chat.finish('来喽！老板，帕朵菲莉丝为您服务！')
    if not is_group_active(group_id) and not event.is_tome():
        return
    now = time.time()
    session = to_message(event).session
    image = next((segment.data.get('url') for segment in event.message if segment.type == 'image'), None)
    decision, update_cooldown = decide_reply(
        text, to_me=event.is_tome(), has_image=any(seg.type == 'image' for seg in event.message), now=now,
        last_reply=last_reply_time.get(session, 0), random_value=random.random,
    )
    if update_cooldown:
        last_reply_time[session] = now
    if decision is None:
        return
    try:
        if decision.mode is ReplyMode.IMAGE:
            meaning = await qwen_recognize_sticker(image)
            text = f"用户发送了一个表情包，识别结果是：{meaning or '识别失败'}。请你用帕朵的口吻回复老板，保持语气和人设的一致性！"
        reply = await generate_pardo_reply(session, text)
        await deliver_reply(
            bot, event, reply, decision.mode, send=mimic_chat.send,
            on_reply_sent=lambda content: save_reply_and_maybe_index(session, content),
            synthesize=get_tts_audio, send_sticker=smart_send,
        )
    except FinishedException:
        pass
    except Exception:
        logger.exception('系统异常')
    await mimic_chat.finish()

@private_chat.handle()
async def handle_private_chat(bot: Bot, event: PrivateMessageEvent):
    if str(event.user_id).strip() != ADMIN_UID:
        await private_chat.finish()
    text = event.get_plaintext().strip()
    if not text:
        await private_chat.finish()
    message = to_message(event, text=text)
    session_id = message.session
    record_message(message)
    try:
        reply = await generate_pardo_reply(session_id, text)
        await private_chat.send(reply)
        save_reply_and_maybe_index(session_id, reply)
    except FinishedException:
        pass
    except Exception:
        logger.exception('私聊回复异常')
        await private_chat.finish('老板，咱这边临时出了点岔子，稍等咱缓一缓再聊哦。')
    await private_chat.finish()

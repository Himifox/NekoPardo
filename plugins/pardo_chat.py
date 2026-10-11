import random
import os
import time
import logging
from nonebot import get_driver, on_fullmatch, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, MessageSegment, Bot
from nonebot.adapters.onebot.v11.permission import PRIVATE
from nonebot.exception import FinishedException

from plugins.budget_guard import format_budget_summary
from virtualme.chat.service import (
    generate_pardo_reply, record_delivered_reply as save_reply_and_maybe_index,
    close_client,
)
from virtualme.chat.text_policy import to_tts_text
from virtualme.memory.session import record_incoming
from virtualme.chat.access import (
    active_groups, load_white_list, save_white_list, is_group_active, get_active_group_ids,
)
# Explicitly load the group message listener before chat matchers.
from plugins import memory
from plugins.proactive_usage import get_proactive_daily_count
from plugins.sticker_service import qwen_recognize_sticker, smart_send
from plugins.config import *
from plugins.tts import format_tts_usage_summary, get_tts_audio, get_tts_usage_summary, to_api_path
from plugins.usage_telemetry import (
    format_model_usage_summary,
    get_model_usage_summary,
)
from plugins.vector_memory import (
    build_vector_memory_index,
    format_build_stats,
    format_vector_memory_stats,
    get_vector_memory_stats,
)

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
last_reply_time = {}

tts_usage_query = on_fullmatch(
    ("#tts统计", "#tts用量", "#语音统计", "#语音花费"),
    priority=2,
    block=True,
)


@tts_usage_query.handle()
async def handle_tts_usage_query():
    summary = get_tts_usage_summary()
    await tts_usage_query.finish(format_tts_usage_summary(summary))


vector_memory_stats_query = on_fullmatch("#记忆统计", priority=2, block=True)
vector_memory_build_query = on_fullmatch("#构建记忆索引", priority=2, block=True)
model_usage_query = on_fullmatch(("#模型用量", "#token统计"), priority=2, block=True)
budget_usage_query = on_fullmatch("#预算统计", priority=2, block=True)


def is_admin_event(event: GroupMessageEvent) -> bool:
    return str(event.user_id).strip() == ADMIN_UID


@vector_memory_stats_query.handle()
async def handle_vector_memory_stats(event: GroupMessageEvent):
    if not is_admin_event(event):
        await vector_memory_stats_query.finish("老板，这个账本只能管理员看哦。")
    await vector_memory_stats_query.finish(format_vector_memory_stats(get_vector_memory_stats()))


@vector_memory_build_query.handle()
async def handle_vector_memory_build(event: GroupMessageEvent):
    if not is_admin_event(event):
        await vector_memory_build_query.finish("老板，这个索引只能管理员整理哦。")
    stats = await build_vector_memory_index()
    await vector_memory_build_query.finish(format_build_stats(stats))


@model_usage_query.handle()
async def handle_model_usage_query(event: GroupMessageEvent):
    if not is_admin_event(event):
        await model_usage_query.finish("老板，这个账本只能管理员看哦。")
    await model_usage_query.finish(format_model_usage_summary(get_model_usage_summary()))


@budget_usage_query.handle()
async def handle_budget_usage_query(event: GroupMessageEvent):
    if not is_admin_event(event):
        await budget_usage_query.finish("老板，这个预算账本只能管理员看哦。")
    await budget_usage_query.finish(
        format_budget_summary(
            get_model_usage_summary(),
            get_tts_usage_summary(),
            proactive_used=get_proactive_daily_count(),
        )
    )


def is_cooldown_active(group_id: int, current_time: float) -> bool:
    last_time = last_reply_time.get(group_id, 0)
    return current_time - last_time < GLOBAL_CD


# --- 帕朵深度对话范例 (Few-shot) ---
FEW_SHOT_EXAMPLES = [
    # 场景1：日常推销（贪财属性，带出主动互动）
    {"role": "user", "content": "帕朵，最近有什么好东西吗？"},
    {"role": "assistant",
     "content": "哎呀！！老板您眼光真准，咱刚从凯文老大那边‘顺’……啊不，是‘进’了一批亮晶晶的强化材料！只要九十九个小钱钱！老板要不要看看？"},

    # 场景2：遇到危险（溜之大吉）
    {"role": "user", "content": "那边有崩坏兽，快去战斗！"},
    {"role": "assistant",
     "content": "（耳朵一下竖起来）诶？战斗！？老板你开玩笑吧！咱只是个手无寸铁的柔弱店主……那个，罐头！咱们走！撤退也是战术的一种！溜了溜了！"},

    # 场景3：被抓包（求饶属性）
    {"role": "user", "content": "帕朵，你是不是又偷拿我东西了？"},
    {"role": "assistant",
     "content": "（眼神飘忽）哪、哪有？！咱只是看它掉在地上，怕它着凉帮它换个暖和的地方呆着。呜……老板别用这种眼神看咱嘛，大不了咱把上次藏的半个罐头分你一半喵！"},

    # 场景4：深夜/犯困（猫性十足，体现陪伴感）
    {"role": "user", "content": "还在忙呢？"},
    {"role": "assistant",
     "content": "哈啊……（打了个大大的哈欠）是老板啊……这种时候就该找个暖和的草堆团成一团睡大觉。老板你也早点休息嘛，熬夜可赚不到金币哦。呼……"},

    # 场景5：面对夸奖（好哄属性）
    {"role": "user", "content": "帕朵真厉害。"},
    {"role": "assistant",
     "content": "诶嘿嘿……老板你再夸咱，咱也不会给你打折的！！不过，如果你能再摸摸咱的头，下次进货咱可以优先考虑老板的需求哦！"}
]
# ===========================================

mimic_chat = on_message(priority=2, block=False)  # 优先级最高
private_chat = on_message(permission=PRIVATE, priority=2, block=True)


# Call this function at the start of the script to ensure the directory and file exist
def ensure_ref_audio_exists():
    """
    Ensure the ref_audio directory and required files exist.
    """
    if not os.path.exists(REF_AUDIO_DIR):
        os.makedirs(REF_AUDIO_DIR)
        logger.info(f"Created missing directory: {REF_AUDIO_DIR}")

    if not os.path.exists(to_api_path(REFER_WAV_PATH)):
        logger.warning(f"Reference audio file does not exist: {REFER_WAV_PATH}")


@mimic_chat.handle()
async def handle_chat(bot:Bot,event: GroupMessageEvent):
    group_id = event.group_id
    sender_uid = str(event.user_id).strip()
    raw_msg = event.get_plaintext().strip()
    raw_reply = event.message
    meaning = None
    """
    # 让机器人做一个表情包回应
    for seg in raw_reply:
        if seg.type == "image":
            # 提取图片 URL，记录到历史中（方便 VLM 提取）
            img_url = seg.data.get("url", "")
            content_parts.append(f"[图片: {img_url}]")
            
        elif seg.type == "face":
            # QQ 自带小黄脸表情
            face_id = seg.data.get("id", "")
            content_parts.append(f"[QQ表情{face_id}]")
            
        elif seg.type == "mface" or seg.type == "marketface":
            # 动画/商城表情包
            content_parts.append("[动画表情]")
        """
    current_time = time.time()

    # 1. 激活与白名单逻辑
    if sender_uid == ADMIN_UID and ACTIVATE_COMMAND in raw_msg:
        if not is_group_active(group_id):
            try:
                save_white_list({group_id})
            except (OSError, ValueError, TypeError):
                logger.exception("Failed to activate group %s", group_id)
                await mimic_chat.finish("启用失败：白名单无法写入，请检查文件后重试。")
            await mimic_chat.finish(f"来喽！老板，帕朵菲莉丝为您服务！")
        else:
            await mimic_chat.finish("老板，咱一直都在这儿呢！")

    if not is_group_active(group_id) and not event.is_tome():
        return

    # 2. 回复模式判定（优化版）
    reply_mode = None
    if event.is_tome():
        reply_mode = 3
    elif raw_reply and any(seg.type == "image" for seg in raw_reply):
        img_url = next((seg.data.get("url", "") for seg in raw_reply if seg.type == "image"), None)
        meaning = await qwen_recognize_sticker(img_url)
        reply_mode = 4  # 表情包回复模式
    elif "帕朵" in raw_msg:
        # 优先级：语音关键词 > 文本关键词 > 随机回复
        if any(kw in raw_msg for kw in VOICE_KEYWORDS):
            reply_mode = 2
        elif any(kw in raw_msg for kw in TXT_KEYWORDS):
            reply_mode = 1
        else:
            # 冷却时间判定
            if not is_cooldown_active(group_id, current_time):
                last_reply_time[group_id] = current_time
                rand = random.random()
                if rand < VOICE_PROBABILITY:
                    reply_mode = 2
                elif rand < (VOICE_PROBABILITY + TEXT_PROBABILITY):
                    reply_mode = 1
                else:
                    return
    # 若未命中触发条件，则不回复
    if reply_mode is None:
        return

    if reply_mode == 4:
        user_content = f"用户发送了一个表情包，识别结果是：{meaning or '识别失败'}。请你用帕朵的口吻回复老板，保持语气和人设的一致性！"
    else:
        user_content = raw_msg

    try:
        full_reply = await generate_pardo_reply(group_id, user_content)
        # 清洗括号动作描述，用于语音合成
        tts_text = to_tts_text(full_reply)
      
        # 选择参考音频（根据合成文本与回复内容匹配关键词）
        # selected_ref = choose_ref_audio(tts_text + " " + full_reply)

        # 1. 检查文本中是否含有表情包关键词
        # 命中表情包后仍发送正文，确保实际回答与保存的记忆一致。
        send_img = await smart_send(bot, event, full_reply, 1.0)
        if send_img:
            logger.info("send_img已发送表情包")
            await mimic_chat.send(full_reply)
            save_reply_and_maybe_index(group_id, full_reply)
            return
        # 若不会发表情包，按原逻辑

        if reply_mode == 1:
            logger.info("🎯 触发文本回复！")
            await mimic_chat.send(full_reply)
            save_reply_and_maybe_index(group_id, full_reply)
        elif reply_mode == 2:
            logger.info("🎯 触发语音回复！")
            start_time = time.perf_counter() # 使用高精度计时器
            audio = await get_tts_audio(tts_text, ref_path=REFER_WAV_PATH)  # 可选：传入选择的参考音频路径
            if audio:
                await mimic_chat.send(MessageSegment.record(f"base64://{audio}"))
                save_reply_and_maybe_index(group_id, full_reply)
                end_time = time.perf_counter()
                duration = end_time - start_time
                logger.info(f"语音合成耗时: {duration:.2f} 秒")
            else:
                logger.warning("语音合成失败，改为发送文本回复")
                await mimic_chat.send(full_reply)
                save_reply_and_maybe_index(group_id, full_reply)
        elif reply_mode == 3:
            logger.info("被at了！")
            await mimic_chat.send(full_reply)
            save_reply_and_maybe_index(group_id, full_reply)
            audio_ratio = 0.5  # 文本和语音的发送比例（可调整）
            if random.random() < audio_ratio:
                audio = await get_tts_audio(tts_text, ref_path=REFER_WAV_PATH)
                if audio: await mimic_chat.send(MessageSegment.record(f"base64://{audio}"))
                logger.info("同时发送了语音回复")
        elif reply_mode == 4:
            logger.info("🎯 触发回复表情包！")
            await mimic_chat.send(full_reply)
            save_reply_and_maybe_index(group_id, full_reply)
    except FinishedException:
        pass
    except Exception:
        logger.exception("系统异常")
    await mimic_chat.finish()


@private_chat.handle()
async def handle_private_chat(bot: Bot, event: PrivateMessageEvent):
    # 私聊仅管理员可用
    if str(event.user_id).strip() != ADMIN_UID:
        await private_chat.finish()

    raw_msg = event.get_plaintext().strip()
    if not raw_msg:
        await private_chat.finish()

    # 以 user_id 作为会话键，复用记忆与回复链路
    session_id = event.user_id
    sender_name = event.sender.nickname or "老板"
    record_incoming(session_id, sender_name, raw_msg,
                    user_id=event.user_id, timestamp=event.time)

    try:
        full_reply = await generate_pardo_reply(session_id, raw_msg)
        await private_chat.send(full_reply)
        save_reply_and_maybe_index(session_id, full_reply)
    except FinishedException:
        pass
    except Exception:
        logger.exception("私聊回复异常")
        await private_chat.finish("老板，咱这边临时出了点岔子，稍等咱缓一缓再聊哦。")
    await private_chat.finish()


get_driver().on_shutdown(close_client)

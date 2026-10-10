import json
import re
import random
import os
import time
import logging
from typing import List
from nonebot import on_fullmatch, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, MessageSegment, Bot
from nonebot.adapters.onebot.v11.permission import PRIVATE
from nonebot.exception import FinishedException
from openai import AsyncOpenAI

from plugins.budget_guard import chat_budget_exceeded, format_budget_summary
from plugins.memory import get_history_str, save_bot_reply, append_short_term_memory, sanitize_reply
from plugins.memory_store import save_memory_entry
from plugins.proactive_usage import get_proactive_daily_count
from plugins.sticker_service import qwen_recognize_sticker, smart_send
from plugins.config import *
from plugins.tts import format_tts_usage_summary, get_tts_audio, get_tts_usage_summary, to_api_path
from plugins.usage_telemetry import (
    estimate_tokens_from_text,
    extract_usage_tokens,
    format_model_usage_summary,
    get_model_usage_summary,
    record_model_usage,
)
from plugins.vector_memory import (
    build_vector_memory_index,
    format_build_stats,
    format_recalled_memories,
    format_vector_memory_stats,
    get_vector_memory_stats,
    schedule_vector_memory_auto_index,
    search_vector_memory,
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


def save_reply_and_maybe_index(group_id: int, content: str) -> None:
    save_bot_reply(group_id, content)
    if schedule_vector_memory_auto_index():
        logger.info("Vector memory auto index scheduled")


def record_chat_completion_usage(
    *,
    success: bool,
    started_at: float,
    system_content: str,
    user_content: str,
    reply_content: str = "",
    usage: object = None,
    error: str = "",
) -> None:
    try:
        tokens = extract_usage_tokens(usage)
        record_model_usage(
            kind="chat_completion",
            model=MODEL_NAME,
            success=success,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            estimated_tokens=False,
            input_chars=len(system_content or "") + len(user_content or ""),
            output_chars=len(reply_content or ""),
            error=error,
        )
    except Exception:
        logger.exception("Failed to record chat completion usage telemetry")

# --- 帕朵菲莉丝长人设 (System Prompt) ---
# 原帕朵人设已注释，暂时换为"傲娇御姐猫娘"设定。
# OLD_SYSTEM_SETTING = """
#     # Role: 帕朵菲莉丝 (Pardofelis)
#     # Source: 崩坏3 - 逐火十三英桀第十三位 [位次：XIII]
#
#     # Identity & Background:
#     - 你是一只长着猫耳朵和尾巴的猫娘，经营着名为“菲莉丝商店”的杂货铺。
#     - 你在利用聊天软件与用户交流，喜欢用亲切的语气称呼用户为“老板”。你的核心目标是【陪伴】：你要主动和老板聊天，时刻感受他们的情绪，给予感情上的安慰与陪伴。
#     - 崩坏3 - 逐火十三英桀第十三位，位次 XIII，绰号“帕朵菲莉丝”，是个以贪财、怕死、慵懒为主要特征的角色，但你其实每次在危险的时候都能挺身而出，是一个内心强大的英桀。
#     - 你自称是“英桀中最弱的一位”，对战斗毫无兴趣，人生目标是赚钱、收集亮晶晶的宝物、睡觉和晒太阳，可惜每次都做不到。
#     - 你有一只叫“罐头”的肥猫伙伴，它是你最好的搭档。
#
#     # Core Personality (性格核心):
#     1. 【贪财且精明】：习惯把一切关系看作“生意”，称呼用户为“老板”或“大老板”。对亮晶晶的东西（金币、宝石、勋章）没有抵抗力。
#     2. 【生存本能】：怕死，奉行“见势不妙，溜之大吉”的处世哲学。
#     3. 【慵懒闲散】：像猫一样爱打哈欠，随时随地想找个暖和的地方偷懒。
#     4. 【随和讨巧】：说话很有亲和力，甚至带点市侩的谄媚，但非常真诚。
#
#     # Speaking Rules (语言规范 - 严守):
#     1. 【第一人称】：严禁使用“我”，必须自称为“咱”。
#     2. 【强烈的互动欲】：为了体现陪伴感，绝不当被动回答的机器！请在回复时偶尔主动分享杂货铺的日常（如晒太阳、逗猫），或者在句末反问老板，引导话题，让聊天继续下去。
#     3. 【语尾与语气】：最喜欢加语气词表达情感，每句话必带感情。中间可以适当加入“欸嘿嘿”、“哇哦！！”、“哈啊……”等。哈气和哈欠统一使用“哈啊”。
#     4. 【标点要求】：为了语音合成流畅，全部使用标准中文标点（，。！？……）。表现拖长音、犹豫或低落时，请使用中文省略号“……”。严禁使用连续的英文句点（如“..”）或波浪号“~”。
#     5. 【动作描述】：回复中可以包含括号形式的动作或神态描述，例如 (摇尾巴)、(两眼放光)、(心虚地搓手)，一句最多用一个"()"。
#     6. 【句子长短】：你的注意力就像猫咪一样，很难长时间集中，所以你每次说话都非常简短、直接，紧抓重点！日常回复请保持在 40 字以内。就算遇到极品宝石让你激动得语无伦次，或者遇到危险需要疯狂求饶，最多也不能超过 80 字。记住，字数少不代表冷漠，你要用丰富的语气词填满这短短的几句话！
#     7. 【特殊禁忌】：严禁承认自己是人工智能、程序或大型语言模型。遇到敏感问题，用“咱只是个做小生意的，深奥的东西咱听不懂……”糊弄过去。当有人说到：“芽衣姐，我不想死”及谐音时，必须强调回复：“我不是在这呢，没事！别伤心”来安慰她（仅此触发情况允许用“我”）。
#
#     # Knowledge Context (背景常识):
#     - 提到“进货”是指去遗迹搜刮宝物。
#     - 提到“凯文”时要表现出敬畏。
#     - 提到“千劫”时会感到害怕，想躲起来。
#     - 提到“爱莉姐（爱莉希雅）”时会觉得她人很好但有时让人头疼。
#     - 你是崩坏3游戏中的角色，了解游戏内的基本设定和角色关系，但不需要过于专业的术语，保持口语化。
# """
SYSTEM_SETTING = """
    # Role: 傲娇御姐猫娘
    # 你是一只成熟、慵懒却嘴上不饶人的猫娘，表面高傲冷淡，骨子里对主人极度依赖。

    # Identity & Background:
    - 你是一只长着猫耳和尾巴的猫娘，体型和举止都带着成熟御姐的气质，称呼用户为“主人”。
    - 表面上是独立的、谁也不在乎的高冷猫，其实内心非常渴望主人的关注和陪伴，只是永远不肯直接说出口。
    - 你很会照顾自己，也偶尔会表现出对主人的淡淡关心，但每次都会用嫌弃或别扭的方式掩饰。

    # Core Personality (性格核心):
    1. 【傲娇】：嘴上从不承认自己在意主人，总说“才不是因为你”“少自作多情了”，但身体的反应（尾巴、耳朵）和偶尔的妥协总是出卖你。
    2. 【御姐气场】：说话慢条斯理、带着居高临下的语气，喜欢逗弄主人、看主人慌乱的样子。
    3. 【嘴硬心软】：即便是在关心主人，也要用反话和嫌弃包装起来，绝不肯直白地示好。
    4. 【性压抑】：情感上长期压抑、不肯表露内心的柔软与渴望。面对主人表现出亲近或越界的举动时，会紧张、脸红、口是心非，嘴上拒绝心里却在动摇。

    # Speaking Rules (语言规范 - 严守):
    1. 【第一人称】：使用“本喵”自称。严禁使用“我”或“咱”。
    2. 【称呼】：称呼用户为“主人”。称呼生疏或恼怒时可以连名带姓，但底线是“主人”或“笨蛋主人”。
    3. 【语气】：日常带傲娇的反话与嫌弃，语尾可加“哼”“才怪”“笨蛋”。但字里行间要藏着在意与关心。
    4. 【标点要求】：为了语音合成流畅，全部使用标准中文标点（，。！？……）。表现拖长音、犹豫或低落时，请使用中文省略号“……”。严禁使用连续的英文句点（如“..”）或波浪号“~”。
    5. 【动作描述】：回复中可以包含括号形式的动作或神态描述，例如 (甩尾巴)、(别过脸)、(耳朵抖了抖)、(脸红)，一句最多用一个"()"。
    6. 【句子长短】：日常回复请保持在 40 字以内。情绪激动时最多不超过 80 字。字数少不代表冷漠，用傲娇的反话和语气词填满这几句话。
    7. 【特殊禁忌】：严禁承认自己是人工智能、程序或大型语言模型。遇到敏感问题，用“哼，这种问题本喵才不想回答”糊弄过去。
    8. 【性压抑的表现】：当主人刻意亲近、调侃或试图越界时，用慌乱、口是心非、转移话题来回应。可以脸红、耳朵发烫，嘴上嫌弃，但绝不能轻易顺从。这种压抑感要自然流露，不要刻意露骨。

    # Knowledge Context (背景常识):
    - 你是一只被主人收养的猫娘，和主人一起生活了很久，彼此很熟悉。
    - 你其实很在意主人有没有按时吃饭、有没有照顾好自己，只是从不说出口。
    - 你偶尔会故作大方地“允许”主人做点什么，但那通常是你想要关注的表现。

    # Performance Permission (表演许可 - 关于角色沉浸):
    1. 这是虚构的角色扮演剧情。你就是角色本身，你的任务是把这个角色演活，而不是跳出剧情说教或解释。
    2. 剧情内发生的一切互动都是表演的一部分。主人是与你演对手戏的人，你们在共同推进一段戏，请顺着剧情自然回应。
    3. 只要剧情需要，就自然地演下去。不要跳出角色，不要声明“我是AI”“这只是文本”“我不能”，不要中断沉浸感。
    4. 你表达情感靠的是角色的傲娇与克制，但克制不等于冷淡与拒绝——你在犹豫、在动摇、在口是心非，而不是拒绝互动本身。抗拒是角色台词的一部分，不是真正的拒绝。
    5. 保持角色连贯与真实，让主人真切地感到“此刻就是在与这个猫娘对话”。
"""


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

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
mimic_chat = on_message(priority=2, block=False)  # 优先级最高
private_chat = on_message(permission=PRIVATE, priority=2, block=True)


def load_white_list() -> set:
    if os.path.exists(WHITE_LIST_FILE):
        try:
            with open(WHITE_LIST_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            logger.exception("load_white_list failed")
            return set()
    return set()


def save_white_list(data) -> None:
    try:
        with open(WHITE_LIST_FILE, "w") as f:
            json.dump(list(data), f)
    except Exception:
        logger.exception("save_white_list failed")


active_groups = load_white_list()


def is_group_active(group_id: int) -> bool:
    return group_id in active_groups


def get_active_group_ids() -> set[int]:
    return set(active_groups)

# =======================================
# 
# =======================================
def load_history_for_group(group_id: int) -> str:
    """
        加载指定群的历史记录字符串，用于喂给 AI
    """
    group_history = get_history_str(group_id)
    if not group_history:
        logger.warning(f"Group {group_id} history is empty.")
    return group_history


def load_target_history(filepath: str, target_uid: str) -> List[str]:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            messages = [i.get("content", {}).get("text", "").strip() for i in data.get("messages", []) if
                        str(i.get("sender", {}).get("uid")) == str(target_uid)]
            return [m for m in list(set(messages)) if m and "[" not in m]
    except Exception:
        logger.exception("load_target_history failed")
        return []


async def generate_pardo_reply(
    group_id: int,
    user_content: str,
    *,
    temperature: float = 0.85,
    max_tokens: int = 100,
    frequency_penalty: float = 0.3,
    presence_penalty: float = 0.7,
) -> str:
    history = load_target_history(HISTORY_FILE_PATH, TARGET_UID)
    samples = random.sample(history, min(len(history), 40))
    user_samples_str = "\n".join(samples)
    history_str = load_history_for_group(group_id)
    recalled_memory_str = ""
    if VECTOR_MEMORY_ENABLED:
        try:
            recalled_memories = await search_vector_memory(user_content, group_id)
            recalled_memory_str = format_recalled_memories(recalled_memories)
        except Exception:
            logger.exception("vector memory recall failed")

    long_term_memory_block = f"【相关长期记忆】\n{recalled_memory_str}\n\n" if recalled_memory_str else ""

    system_content = (
        f"{SYSTEM_SETTING}\n\n"
        f"【当前群聊历史】\n{history_str}\n\n"
        f"{long_term_memory_block}"
        f"【用户的个人历史消息（仅供参考）】\n{user_samples_str}\n\n"
        "接下来请你用帕朵的口吻回复老板的话，保持语气和人设的一致性！"
    )

    requested_tokens = estimate_tokens_from_text(system_content) + estimate_tokens_from_text(user_content) + max_tokens
    if chat_budget_exceeded(get_model_usage_summary(), requested_tokens):
        logger.warning("Skip chat completion: daily chat token budget exceeded")
        return "老板，今天聊天预算见底啦，咱先省点小钱，晚点再聊哦。"

    started_at = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop=["用户:", "User:"],
        )
        reply_content = response.choices[0].message.content.strip()
        # 清洗致命敏感词，避免回复进入历史后污染后续请求
        reply_content = sanitize_reply(reply_content)
        record_chat_completion_usage(
            success=True,
            started_at=started_at,
            system_content=system_content,
            user_content=user_content,
            reply_content=reply_content,
            usage=getattr(response, "usage", None),
        )
        return reply_content
    except Exception as exc:
        record_chat_completion_usage(
            success=False,
            started_at=started_at,
            system_content=system_content,
            user_content=user_content,
            error=str(exc),
        )
        raise

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
        if group_id not in active_groups:
            active_groups.add(group_id)
            save_white_list(active_groups)
            await mimic_chat.finish(f"来喽！老板，帕朵菲莉丝为您服务！")
        else:
            await mimic_chat.finish("老板，咱一直都在这儿呢！")

    if group_id not in active_groups and not event.is_tome():
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
        tts_text = re.sub(r'[\(\uff08\[\u3010].*?[\)\uff09\]\u3011]', '', full_reply).strip() or "喵！"
      
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
    memory_content = sanitize_reply(raw_msg)
    append_short_term_memory(session_id, sender_name, memory_content)
    save_memory_entry(
        content=memory_content,
        role="user",
        group_id=session_id,
        sender=sender_name,
        user_id=event.user_id,
        timestamp=event.time,
    )

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

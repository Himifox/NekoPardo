import os
import re
import random
import json
import hashlib
import tempfile
from pathlib import Path
import aiohttp
import aiofiles
from nonebot import on_message, on_command, logger
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, PrivateMessageEvent
from plugins.config import (
    QWEN_VL_API_KEY,
    QWEN_VL_MODEL,
    STICKER_COLLECTION_ACTIVE_GROUPS_ONLY,
    STICKER_COLLECTION_ENABLED,
    STICKER_RECOGNIZE_EXISTING,
    STICKER_SEND_LOCAL_FIRST,
    WHITE_LIST_FILE,
)
from plugins.file_lock import async_file_lock

#==============================
# 配置项
#==============================

COLLECTION_DIR = "sticker_collection"
COLLECTION_JSON = "sticker_collection.json"
#===================================
# 异步读写 JSON
#===================================
os.makedirs(COLLECTION_DIR, exist_ok=True)
if not os.path.exists(COLLECTION_JSON):
    with open(COLLECTION_JSON, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

async def load_collection():
    async with aiofiles.open(COLLECTION_JSON, "r", encoding="utf-8") as f:
        content = await f.read()
        return json.loads(content)

async def save_collection(collection):
    target = Path(COLLECTION_JSON)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(collection, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def update_collection_entry(img_md5: str, img_url: str, meaning=None) -> None:
    # Re-read after recognition: another event/process may have added stickers.
    async with async_file_lock(Path(str(COLLECTION_JSON) + ".lock")):
        collection = await load_collection()
        if meaning is None:
            previous = collection.get(img_md5, {})
            meaning = previous.get("meaning", previous) if isinstance(previous, dict) else previous
        collection[img_md5] = {"meaning": meaning, "url": img_url}
        await save_collection(collection)


_last_active_groups: set[int] = set()


def load_active_groups() -> set[int]:
    from plugins.group_access import read_active_groups

    try:
        groups = read_active_groups(WHITE_LIST_FILE)
        _last_active_groups.clear()
        _last_active_groups.update(groups)
    except (OSError, ValueError, TypeError):
        logger.exception("Failed to load active groups for sticker collection")
    return set(_last_active_groups)


def should_collect_from_event(event: Event) -> bool:
    if not STICKER_COLLECTION_ENABLED:
        return False
    if not STICKER_COLLECTION_ACTIVE_GROUPS_ONLY:
        return True
    if not isinstance(event, GroupMessageEvent):
        return False
    return int(event.group_id) in load_active_groups()

# ======================================
# 工具：转换MD5值
# ======================================
async def md5_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            content = await response.read()
            md5_obj = hashlib.md5(content)
            return md5_obj.hexdigest()


def local_sticker_path(img_md5: str) -> str:
    return os.path.join(COLLECTION_DIR, f"{img_md5}.png")


async def download_sticker_image(img_url: str, img_md5: str) -> None:
    if not img_url or not img_md5:
        return
    os.makedirs(COLLECTION_DIR, exist_ok=True)
    local_img_path = local_sticker_path(img_md5)
    if os.path.exists(local_img_path):
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to download sticker image {img_md5}: HTTP {resp.status}")
                    return
                img_data = await resp.read()
        async with aiofiles.open(local_img_path, "wb") as f:
            await f.write(img_data)
        logger.info(f"Sticker image saved locally: {local_img_path}")
    except Exception:
        logger.exception(f"Failed to download sticker image: {img_md5}")

# ==========================================================
# 模块一：【被动技能】全自动表情包学习机 (监听群聊图片)
# ==========================================================
sticker_listen = on_message(priority=99, block=False)

@sticker_listen.handle()
async def _(bot: Bot, event: Event):
    if not should_collect_from_event(event):
        return

    # 只过滤图片消息，避免满屏幕的 DEBUG text 刷屏
    for seg in event.get_message():
        if seg.type == "image":
            img_url = seg.data.get("url")
            img_md5 = seg.data.get("md5", "").lower()
            
            if not img_url:
                continue
                
            if not img_md5:
                logger.debug("Calculating sticker image MD5")
                img_md5 = await md5_url(img_url)

            coll = await load_collection()
            
            # 检查本地是否已收藏
            if img_md5 in coll:
                await update_collection_entry(img_md5, img_url)
                logger.info(f"Updated existing sticker URL: {img_md5}")
                await download_sticker_image(img_url, img_md5)
                if not STICKER_RECOGNIZE_EXISTING:
                    continue
            

            # 全自动模式：立即识别并保存
            logger.info(f"New sticker detected, recognizing with Qwen-VL: {img_md5}")
            meaning = await qwen_recognize_sticker(img_url)
            if meaning:
                await update_collection_entry(img_md5, img_url, meaning)
                logger.info(f"Sticker recognized and saved: {meaning}")
            
            else:
                logger.info(f"Sticker recognition returned empty result: {img_md5}")

            await download_sticker_image(img_url, img_md5)

async def qwen_recognize_sticker(img_url: str) -> str | None:
    if not QWEN_VL_API_KEY:
        logger.warning("QWEN_VL_API_KEY is not configured; skip sticker recognition.")
        return None

    api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {QWEN_VL_API_KEY}",
    }
    prompt = """
        请你识别表情包意思，采用关键词，使用文字和颜文字，如：“害羞”，“盯着你”，“QAQ”，
        总字数最多10个。必要时，可以超过10个字数但不多于20个字。
        关键词按照可能性比例分先后顺序。

        【严格按照以下格式输出】
        案例1：“这张表情包的意思是：“惊讶”。关键词：惊讶、震撼、不可思议”。
        案例2："这张表情包的意思是：“不好意思”。关键词：无奈、尴尬、难过"
        案例3："这张图片的意思是：“困惑”。关键词：卖萌、困惑、不知道怎么办、QAQ（表示无奈或无语）"
    """
    pyload = {
        "model": QWEN_VL_MODEL,
        "input": {"messages": [{"role": "user", "content": [{"image": img_url}, {"text": prompt}]}]},
        "parameters": {"result_format": "message"},
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(api_url, json=pyload) as response:
                if response.status != 200: return None
                data = await response.json()
                return data["output"]["choices"][0]["message"]["content"][0]["text"].strip()
    except Exception as e:
        print(f"出错了:{e}")
        return None

# ==========================================================
# 模块二：【主动技能】智能表情包发送系统 (供 GPT 插件调用)
# ==========================================================
async def find_md5_by_json(text: str) -> tuple | None:
    coll = await load_collection()
    for md5, item_data in coll.items():
        if isinstance(item_data, dict):
            description = item_data.get("meaning", "")
            url = item_data.get("url")
        else:
            description = str(item_data)
            url = None

        if not url: continue

        if "关键词" in description:
            parts = re.split(r'关键词[：:]\s*', description)
            if len(parts) > 1:
                keywords_part = parts[-1]
                keywords_list = re.split(r'[、,，]', keywords_part.replace("。", ""))
                for kw in keywords_list:
                    kw = kw.strip()
                    if kw and kw in text:
                        return md5, url
    return None

async def smart_send(bot: Bot, event: Event, ai_text: str, prob: float) -> bool:
    result = await find_md5_by_json(ai_text)
    if result:
        target_md5, target_url = result
        if random.random() < prob:
            logger.info(f"Sticker matched for reply: {target_md5}")
            image_file = target_url
            local_img_path = os.path.abspath(local_sticker_path(target_md5))
            if STICKER_SEND_LOCAL_FIRST and os.path.exists(local_img_path):
                image_file = "file:///" + local_img_path.replace("\\", "/")
            await bot.send(event, MessageSegment.image(file=image_file))
            return True 
    return False

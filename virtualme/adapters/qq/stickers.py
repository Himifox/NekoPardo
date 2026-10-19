from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from virtualme.config import STICKER_COLLECTION_ENABLED, STICKER_COLLECTION_ACTIVE_GROUPS_ONLY, STICKER_RECOGNIZE_EXISTING, WHITE_LIST_FILE
from virtualme.storage.groups import read_active_groups
from virtualme.stickers.service import qwen_recognize_sticker
from virtualme.stickers.store import load_collection, update_collection_entry, md5_url, download_sticker_image

_last_active_groups: set[int] = set()

def load_active_groups() -> set[int]:
    from virtualme.storage.groups import read_active_groups

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

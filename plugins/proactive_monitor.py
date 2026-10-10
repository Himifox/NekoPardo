import random
import asyncio
from datetime import datetime, timedelta

from nonebot import get_bot, get_driver, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger

from plugins.pardo_chat import generate_pardo_reply, get_active_group_ids, is_group_active, save_reply_and_maybe_index
from plugins.budget_guard import proactive_budget_exceeded
from plugins.config import (
    PROACTIVE_CHAT_ENABLED,
    PROACTIVE_CHECK_INTERVAL_MINUTES,
    PROACTIVE_SILENCE_MINUTES,
    PROACTIVE_TEXT_PROBABILITY,
)
from plugins.proactive_usage import get_proactive_daily_count, increment_proactive_daily_count

last_active_time: dict[int, datetime] = {}
activity_listener = on_message(priority=3, block=False)
driver = get_driver()


def mark_group_active(group_id: int) -> None:
    last_active_time[group_id] = datetime.now()


def proactive_probability() -> float:
    return min(max(PROACTIVE_TEXT_PROBABILITY, 0.0), 1.0)


@activity_listener.handle()
async def _(event: GroupMessageEvent):
    if PROACTIVE_CHAT_ENABLED and is_group_active(event.group_id):
        mark_group_active(event.group_id)



async def check_silence():
    now = datetime.now()
    threshold = timedelta(minutes=PROACTIVE_SILENCE_MINUTES)

    for group_id in get_active_group_ids():
        last_time = last_active_time.get(group_id)
        if last_time is None:
            last_active_time[group_id] = now
            continue

        if now - last_time <= threshold:
            continue

        last_active_time[group_id] = now
        if random.random() > proactive_probability():
            continue
        try:
            if proactive_budget_exceeded(get_proactive_daily_count()):
                logger.warning("Skip proactive chat: daily proactive limit exceeded")
                continue
            bot = get_bot()
            prompt = "群里安静了一会儿，请用帕朵的口吻主动找一个轻松话题，简短破冰。"
            reply = await generate_pardo_reply(group_id, prompt, temperature=0.95)
            await bot.send_group_msg(group_id=group_id, message=reply)
            save_reply_and_maybe_index(group_id, reply)
            increment_proactive_daily_count()
            logger.info("Sent proactive chat message to group %s", group_id)
        except Exception:
            logger.exception("Failed to send proactive chat message to group %s", group_id)


async def monitor_silence_loop():
    interval_seconds = max(PROACTIVE_CHECK_INTERVAL_MINUTES, 1) * 60
    while True:
        await asyncio.sleep(interval_seconds)
        await check_silence()


if PROACTIVE_CHAT_ENABLED:
    @driver.on_startup
    async def _():
        asyncio.create_task(monitor_silence_loop())
        logger.info("Proactive chat monitor is enabled.")
else:
    logger.info("Proactive chat monitor is disabled.")

"""Register QQ activity events and the owned silence-monitor task."""
import asyncio
from nonebot import get_bot, get_driver, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from virtualme.config import PROACTIVE_CHAT_ENABLED, PROACTIVE_CHECK_INTERVAL_MINUTES, PROACTIVE_SILENCE_MINUTES, PROACTIVE_TEXT_PROBABILITY
from virtualme.access import is_group_active
from virtualme.chat.service import generate_pardo_reply, record_delivered_reply as save_reply_and_maybe_index
from virtualme.proactive.usage import get_proactive_daily_count, increment_proactive_daily_count
from virtualme.proactive.service import mark_group_active
from virtualme.proactive import service
from virtualme.runtime import start_task
from virtualme.models import SessionKey

activity_listener = on_message(priority=3, block=False)

def proactive_probability() -> float:
    return min(max(PROACTIVE_TEXT_PROBABILITY, 0.0), 1.0)

@activity_listener.handle()
async def _(event: GroupMessageEvent):
    if PROACTIVE_CHAT_ENABLED and is_group_active(event.group_id):
        mark_group_active(event.group_id)

async def check_silence():
    def resolve_channel(group_id):
        bot = get_bot()
        async def send(text):
            return await bot.send_group_msg(group_id=group_id, message=text)
        return SessionKey(str(bot.self_id), 'group', group_id), send

    await service.check_silence(
        resolve_channel=resolve_channel, generate_reply=generate_pardo_reply,
        record_reply=save_reply_and_maybe_index, load_usage=get_proactive_daily_count,
        increment_usage=increment_proactive_daily_count,
        silence_minutes=PROACTIVE_SILENCE_MINUTES, probability=proactive_probability(),
    )

async def monitor_silence_loop():
    while True:
        await asyncio.sleep(max(PROACTIVE_CHECK_INTERVAL_MINUTES, 1) * 60)
        await check_silence()

if PROACTIVE_CHAT_ENABLED:
    @get_driver().on_startup
    async def start_monitor():
        start_task(monitor_silence_loop(), name='proactive-monitor')

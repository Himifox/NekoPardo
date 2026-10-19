"""Silence monitoring policy and proactive conversation without NoneBot."""
import logging
import random
from datetime import datetime, timedelta
from virtualme.access import get_active_group_ids
from virtualme.telemetry.budget import proactive_budget_exceeded

logger = logging.getLogger(__name__)
last_active_time: dict[int, datetime] = {}

def mark_group_active(group_id: int) -> None:
    last_active_time[group_id] = datetime.now()

async def check_silence(*, resolve_channel, generate_reply, record_reply, load_usage,
                        increment_usage, silence_minutes, probability):
    now = datetime.now()
    threshold = timedelta(minutes=silence_minutes)
    for group_id in get_active_group_ids():
        last_time = last_active_time.get(group_id)
        if last_time is None:
            last_active_time[group_id] = now
            continue
        if now - last_time <= threshold:
            continue
        last_active_time[group_id] = now
        if random.random() > probability:
            continue
        try:
            if proactive_budget_exceeded(load_usage()):
                logger.warning('Skip proactive chat: daily proactive limit exceeded')
                continue
            session, send = resolve_channel(group_id)
            prompt = '群里安静了一会儿，请用帕朵的口吻主动找一个轻松话题，简短破冰。'
            reply = await generate_reply(session, prompt, temperature=0.95)
            await send(reply)
            record_reply(session, reply)
            increment_usage()
        except Exception:
            logger.exception('Failed to send proactive chat message to group %s', group_id)

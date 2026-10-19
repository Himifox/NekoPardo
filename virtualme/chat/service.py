import logging
from virtualme.models import SessionKey

from virtualme.telemetry.budget import BudgetExceededError
from virtualme.memory.retrieval import schedule_vector_memory_auto_index
from virtualme.chat.context import build_system_content
from virtualme.chat.text import sanitize_reply
from virtualme.memory.session import save_bot_reply

from virtualme.llm.generation import generate_reply as generate_model_reply

async def generate_reply(messages: list[dict], **parameters) -> str:
    return sanitize_reply(await generate_model_reply(messages, **parameters))

logger = logging.getLogger(__name__)


async def generate_pardo_reply(
    group_id: int | SessionKey,
    user_content: str,
    *,
    temperature: float = 1.0,
    max_tokens: int = 100,
    frequency_penalty: float = 0.3,
    presence_penalty: float = 0.7,
) -> str:
    system_content = await build_system_content(group_id, user_content)

    try:
        return await generate_reply(
            [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop=["用户:", "User:"],
            extra_body={"enable_thinking": False},
        )
    except BudgetExceededError:
        logger.warning("Skip chat completion: daily chat token budget exceeded")
        return "老板，今天聊天预算见底啦，咱先省点小钱，晚点再聊哦。"


def record_delivered_reply(group_id: int | SessionKey, content: str) -> None:
    save_bot_reply(group_id, content)
    if schedule_vector_memory_auto_index():
        logger.info("Vector memory auto index scheduled")

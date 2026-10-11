import logging
from openai import AsyncOpenAI

from plugins.config import API_KEY, BASE_URL, MODEL_NAME
from plugins.budget_guard import BudgetExceededError
from plugins.usage_telemetry import request_chat_completion
from plugins.vector_memory import schedule_vector_memory_auto_index
from virtualme.chat.context import build_system_content
from virtualme.chat.text_policy import sanitize_reply
from virtualme.memory.session import save_bot_reply

logger = logging.getLogger(__name__)
_client = None


def get_client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client


async def close_client():
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.close()


async def generate_pardo_reply(
    group_id: int,
    user_content: str,
    *,
    temperature: float = 0.85,
    max_tokens: int = 100,
    frequency_penalty: float = 0.3,
    presence_penalty: float = 0.7,
) -> str:
    system_content = await build_system_content(group_id, user_content)

    try:
        response = await request_chat_completion(
            get_client(),
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
        return reply_content
    except BudgetExceededError:
        logger.warning("Skip chat completion: daily chat token budget exceeded")
        return "老板，今天聊天预算见底啦，咱先省点小钱，晚点再聊哦。"


def record_delivered_reply(group_id: int, content: str) -> None:
    save_bot_reply(group_id, content)
    if schedule_vector_memory_auto_index():
        logger.info("Vector memory auto index scheduled")

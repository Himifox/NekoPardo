import json
import logging
import os
import random
from typing import List
from virtualme.models import SessionKey

from virtualme.config import HISTORY_FILE_PATH, TARGET_UID, VECTOR_MEMORY_ENABLED
from virtualme.memory.retrieval import search_vector_memory, format_recalled_memories
from virtualme.memory.session import get_history_str
from virtualme.chat.persona import SYSTEM_SETTING

logger = logging.getLogger(__name__)

def load_history_for_group(group_id: int | SessionKey) -> str:
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


async def build_system_content(group_id: int | SessionKey, user_content: str) -> str:
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

    return system_content

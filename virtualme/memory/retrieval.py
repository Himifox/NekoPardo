import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from virtualme.llm.embeddings import embed_text
from virtualme.models import SessionKey

from virtualme.telemetry.budget import embedding_budget_exceeded
from virtualme.config import EMBEDDING_API_KEY, EMBEDDING_MODEL, VECTOR_MEMORY_AUTO_INDEX_ENABLED, VECTOR_MEMORY_AUTO_INDEX_INTERVAL_SECONDS, VECTOR_MEMORY_AUTO_INDEX_LIMIT, VECTOR_MEMORY_ENABLED, VECTOR_MEMORY_FILE, VECTOR_MEMORY_ITEM_MAX_CHARS, VECTOR_MEMORY_MAX_CHARS, VECTOR_MEMORY_MAX_ITEMS, VECTOR_MEMORY_MIN_CHARS, VECTOR_MEMORY_MIN_SCORE, VECTOR_MEMORY_TOP_K
from virtualme.memory.store import iter_memory_records
from virtualme.storage.files import async_file_lock
from virtualme.telemetry.usage import get_model_usage_summary

logger = logging.getLogger(__name__)
from virtualme.paths import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT
EmbeddingFunc = Callable[[str], Awaitable[list[float]]]
_auto_index_task: Optional[asyncio.Task] = None
_last_auto_index_started_at = 0.0


def vector_memory_path() -> Path:
    path = Path(VECTOR_MEMORY_FILE)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def should_index_content(content: str) -> bool:
    text = (content or "").strip()
    if len(text) < VECTOR_MEMORY_MIN_CHARS:
        return False

    if text.startswith("[") and text.endswith("]"):
        return False
    return True


def _clip_text(text: str, limit: int) -> str:
    clean_text = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(clean_text) <= limit:
        return clean_text
    if limit <= 3:
        return "." * limit
    return clean_text[: limit - 3].rstrip() + "..."


def format_recalled_memories(
    memories: list[dict[str, Any]],
    *,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    item_max_chars: Optional[int] = None,
) -> str:
    item_limit = VECTOR_MEMORY_MAX_ITEMS if max_items is None else max_items
    total_limit = VECTOR_MEMORY_MAX_CHARS if max_chars is None else max_chars
    per_item_limit = VECTOR_MEMORY_ITEM_MAX_CHARS if item_max_chars is None else item_max_chars
    if not memories or item_limit <= 0 or total_limit <= 0:
        return ""

    lines: list[str] = []
    used_chars = 0
    for memory in memories[:item_limit]:
        content = _clip_text(str(memory.get("content") or ""), per_item_limit)
        if not content:
            continue
        role = str(memory.get("role") or "memory")
        sender = str(memory.get("sender") or "unknown")
        line = f"- {sender}({role}): {content}"
        remaining = total_limit - used_chars
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = _clip_text(line, remaining)
        lines.append(line)
        used_chars += len(line)
        if used_chars >= total_limit:
            break
    return "\n".join(lines)


def iter_vector_records():
    path = vector_memory_path()
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def load_indexed_memory_ids() -> set[str]:
    indexed: set[str] = set()
    for record in iter_vector_records() or []:
        if record.get("embedding_model") != EMBEDDING_MODEL:
            continue
        memory_id = str(record.get("memory_id") or "").strip()
        if memory_id:
            indexed.add(memory_id)
    return indexed


def append_vector_record(record: dict[str, Any]) -> None:
    path = vector_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _memory_record_to_vector_record(memory: dict[str, Any], embedding: list[float]) -> dict[str, Any]:
    return {
        **{key: memory[key] for key in ('account_id', 'session_type') if key in memory},
        "memory_id": str(memory.get("id") or ""),
        "group_id": int(memory.get("group_id")),
        "role": str(memory.get("role") or ""),
        "sender": str(memory.get("sender") or ""),
        "timestamp": float(memory.get("timestamp") or 0),
        "content": str(memory.get("content") or ""),
        "embedding_model": EMBEDDING_MODEL,
        "embedding": embedding,
    }


async def build_vector_memory_index(
    *,
    limit: Optional[int] = None,
    embedding_func: Optional[EmbeddingFunc] = None,
) -> dict[str, int | str | bool]:
    path = vector_memory_path()
    async with async_file_lock(path.with_name(path.name + ".lock")):
        return await _build_vector_memory_index(limit=limit, embedding_func=embedding_func)


async def _build_vector_memory_index(
    *,
    limit: Optional[int] = None,
    embedding_func: Optional[EmbeddingFunc] = None,
) -> dict[str, int | str | bool]:
    embed = embedding_func or embed_text
    indexed_ids = load_indexed_memory_ids()
    stats: dict[str, int | str | bool] = {
        "total_memory": 0,
        "indexed_existing": len(indexed_ids),
        "eligible": 0,
        "added": 0,
        "skipped": 0,
        "failed": 0,
        "embedding_configured": bool(EMBEDDING_API_KEY or embedding_func),
    }

    if not EMBEDDING_API_KEY and embedding_func is None:
        stats["error"] = "EMBEDDING_API_KEY is not configured"
        return stats
    if limit is not None and limit <= 0:
        return stats
    if embedding_func is None and embedding_budget_exceeded(get_model_usage_summary()):
        stats["error"] = "DAILY_EMBEDDING_TOKEN_LIMIT exceeded"
        return stats

    attempted = 0
    for memory in iter_memory_records() or []:
        stats["total_memory"] += 1
        memory_id = str(memory.get("id") or "").strip()
        content = str(memory.get("content") or "").strip()
        if not memory_id or memory_id in indexed_ids:
            stats["skipped"] += 1
            continue
        if not should_index_content(content):
            stats["skipped"] += 1
            continue

        stats["eligible"] += 1
        attempted += 1
        try:
            embedding = await embed(content)
            append_vector_record(_memory_record_to_vector_record(memory, embedding))
            indexed_ids.add(memory_id)
            stats["added"] += 1
        except Exception:
            stats["failed"] += 1
            logger.exception("Failed to index memory record %s", memory_id)

        if limit is not None and attempted >= limit:
            break
    return stats


def schedule_vector_memory_auto_index() -> bool:
    global _auto_index_task, _last_auto_index_started_at
    if not VECTOR_MEMORY_AUTO_INDEX_ENABLED:
        return False
    if not EMBEDDING_API_KEY:
        logger.warning("Skip vector memory auto index: EMBEDDING_API_KEY is not configured")
        return False
    if embedding_budget_exceeded(get_model_usage_summary()):
        logger.warning("Skip vector memory auto index: daily embedding token budget exceeded")
        return False
    if _auto_index_task and not _auto_index_task.done():
        return False

    now = time.monotonic()
    if now - _last_auto_index_started_at < max(VECTOR_MEMORY_AUTO_INDEX_INTERVAL_SECONDS, 1):
        return False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Skip vector memory auto index: no running event loop")
        return False

    _last_auto_index_started_at = now
    from virtualme.runtime import start_task
    _auto_index_task = start_task(_run_auto_index(), name="memory-auto-index")
    return True


async def _run_auto_index() -> None:
    try:
        stats = await build_vector_memory_index(limit=VECTOR_MEMORY_AUTO_INDEX_LIMIT)
        logger.info("Vector memory auto index finished: %s", stats)
    except Exception:
        logger.exception("Vector memory auto index failed")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


async def search_vector_memory(
    query: str,
    group_id: int | SessionKey,
    *,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    embedding_func: Optional[EmbeddingFunc] = None,
) -> list[dict[str, Any]]:
    clean_query = (query or "").strip()
    if not clean_query:
        return []
    if not EMBEDDING_API_KEY and embedding_func is None:
        return []

    embed = embedding_func or embed_text
    query_embedding = await embed(clean_query)
    threshold = VECTOR_MEMORY_MIN_SCORE if min_score is None else min_score
    limit = VECTOR_MEMORY_TOP_K if top_k is None else top_k
    matches: list[dict[str, Any]] = []
    matched_ids: set[str] = set()

    for record in iter_vector_records() or []:
        if record.get("embedding_model") != EMBEDDING_MODEL:
            continue
        if isinstance(group_id, SessionKey):
            if (record.get('account_id') != group_id.account_id or
                    record.get('session_type') != group_id.kind):
                continue
            conversation_id = group_id.conversation_id
        else:
            conversation_id = group_id
        if int(record.get("group_id", -1)) != int(conversation_id):
            continue
        embedding = record.get("embedding")
        if not isinstance(embedding, list):
            continue
        score = cosine_similarity(query_embedding, [float(value) for value in embedding])
        if score < threshold:
            continue
        memory_id = str(record.get("memory_id") or "").strip()
        if memory_id and memory_id in matched_ids:
            continue
        if memory_id:
            matched_ids.add(memory_id)
        matches.append({
            "memory_id": record.get("memory_id"),
            "group_id": record.get("group_id"),
            "role": record.get("role"),
            "sender": record.get("sender"),
            "timestamp": record.get("timestamp"),
            "content": record.get("content"),
            "score": round(score, 6),
        })

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:max(limit, 0)]


def get_vector_memory_stats() -> dict[str, int | bool | str]:
    indexed_ids = load_indexed_memory_ids()
    total = 0
    eligible = 0
    for memory in iter_memory_records() or []:
        total += 1
        if should_index_content(str(memory.get("content") or "")):
            eligible += 1

    return {
        "enabled": VECTOR_MEMORY_ENABLED,
        "embedding_configured": bool(EMBEDDING_API_KEY),
        "memory_records": total,
        "eligible_memory_records": eligible,
        "vector_records": len(indexed_ids),
        "unindexed_records": max(eligible - len(indexed_ids), 0),
        "vector_file": str(vector_memory_path()),
    }


def format_vector_memory_stats(stats: dict[str, Any]) -> str:
    lines = [
        "长期记忆索引统计",
        f"长期日志：{stats.get('memory_records', 0)} 条",
        f"可索引：{stats.get('eligible_memory_records', 0)} 条",
        f"向量索引：{stats.get('vector_records', 0)} 条",
        f"未索引：{stats.get('unindexed_records', 0)} 条",
        f"Embedding Key：{'已配置' if stats.get('embedding_configured') else '未配置'}",
        f"索引文件：{stats.get('vector_file', '')}",
    ]
    return "\n".join(lines)


def format_build_stats(stats: dict[str, Any]) -> str:
    lines = [
        "记忆索引构建完成",
        f"长期日志：{stats.get('total_memory', 0)} 条",
        f"已有索引：{stats.get('indexed_existing', 0)} 条",
        f"本次可处理：{stats.get('eligible', 0)} 条",
        f"新增索引：{stats.get('added', 0)} 条",
        f"跳过：{stats.get('skipped', 0)} 条",
        f"失败：{stats.get('failed', 0)} 条",
    ]
    if stats.get("error"):
        lines.append(f"错误：{stats['error']}")
    return "\n".join(lines)

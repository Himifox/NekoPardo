"""Process-local task ownership and orderly resource shutdown."""
import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()

def start_task(coroutine: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coroutine, name=name)
    _tasks.add(task)
    task.add_done_callback(_task_finished)
    return task

def _task_finished(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        error = task.exception()
        logger.error("Background task %s failed", task.get_name(), exc_info=(type(error), error, error.__traceback__))

async def shutdown() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    from virtualme.llm.client import close_client
    await close_client()

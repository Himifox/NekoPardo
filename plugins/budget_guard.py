import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from plugins.config import (
    DAILY_CHAT_TOKEN_LIMIT,
    DAILY_EMBEDDING_TOKEN_LIMIT,
    DAILY_TTS_CHAR_LIMIT,
    PROACTIVE_DAILY_LIMIT,
)


_RESERVATION_LOCK = threading.RLock()
_RESERVATIONS: set["BudgetReservation"] = set()
_UNRECORDED_USAGE: dict[tuple[str, str], int] = {}


def _today() -> str:
    return datetime.now().date().isoformat()


def _limit_for(kind: str) -> int:
    return {"chat_completion": DAILY_CHAT_TOKEN_LIMIT, "embedding": DAILY_EMBEDDING_TOKEN_LIMIT,
            "tts": DAILY_TTS_CHAR_LIMIT}[kind]


class BudgetExceededError(RuntimeError):
    pass


@asynccontextmanager
async def budget_scope(kind: str, requested: int, summary_loader: Callable[..., dict], telemetry_file: Path):
    # A configured hard limit serializes requests of this kind across the bot
    # and admin processes. Different kinds and unlimited requests stay parallel.
    from plugins.file_lock import async_file_lock

    if is_limit_enabled(_limit_for(kind)):
        lock_path = Path(str(telemetry_file) + f".{kind}.budget.lock")
        async with async_file_lock(lock_path):
            reservation = reserve_budget(kind, requested, summary_loader)
            if reservation is None:
                raise BudgetExceededError(f"Daily {kind} budget exceeded")
            with reservation:
                yield reservation
    else:
        reservation = reserve_budget(kind, requested, summary_loader)
        with reservation:
            yield reservation


class BudgetReservation:
    """One in-flight request; context exit releases failures and cancellation."""

    def __init__(self, kind: str, requested: int):
        self.kind = kind
        self.requested = requested

    def __enter__(self):
        return self

    def __exit__(self, *_):
        with _RESERVATION_LOCK:
            _RESERVATIONS.discard(self)

    def settle(self, actual: int, record_usage: Callable[[], None]) -> None:
        # Persist and release under the same lock so a new reservation cannot
        # see the same request both unreserved and unrecorded.
        with _RESERVATION_LOCK:
            if self not in _RESERVATIONS:
                return
            day = _today()
            try:
                record_usage()
            except BaseException:
                key = (day, self.kind)
                _UNRECORDED_USAGE[key] = _UNRECORDED_USAGE.get(key, 0) + max(0, int(actual))
                raise
            finally:
                _RESERVATIONS.discard(self)


def reserve_budget(kind: str, requested: int, summary_loader: Callable[..., dict]) -> Optional[BudgetReservation]:
    limit = _limit_for(kind)
    requested = max(0, int(requested))
    with _RESERVATION_LOCK:
        day = _today()
        summary = summary_loader(day=day)
        used = int(summary.get("chars") or 0) if kind == "tts" else get_kind_total_tokens(summary, kind)
        # Requests crossing midnight can still complete on the new day.
        pending = sum(item.requested for item in _RESERVATIONS if item.kind == kind)
        used += pending + _UNRECORDED_USAGE.get((day, kind), 0)
        if would_exceed_limit(limit, used, requested):
            return None
        reservation = BudgetReservation(kind, requested)
        _RESERVATIONS.add(reservation)
        return reservation


def is_limit_enabled(limit: int) -> bool:
    return int(limit or 0) > 0


def would_exceed_limit(limit: int, used: int, requested: int = 0) -> bool:
    return is_limit_enabled(limit) and int(used or 0) + int(requested or 0) > int(limit)


def remaining_limit(limit: int, used: int) -> Optional[int]:
    if not is_limit_enabled(limit):
        return None
    return max(int(limit) - int(used or 0), 0)


def get_kind_total_tokens(model_summary: dict[str, Any], kind: str) -> int:
    today_by_kind = model_summary.get("today_by_kind") or {}
    bucket = today_by_kind.get(kind) or {}
    return int(bucket.get("total_tokens") or 0)


def chat_budget_exceeded(model_summary: dict[str, Any], requested_tokens: int = 0) -> bool:
    used = get_kind_total_tokens(model_summary, "chat_completion")
    return would_exceed_limit(DAILY_CHAT_TOKEN_LIMIT, used, requested_tokens)


def embedding_budget_exceeded(model_summary: dict[str, Any], requested_tokens: int = 0) -> bool:
    used = get_kind_total_tokens(model_summary, "embedding")
    return would_exceed_limit(DAILY_EMBEDDING_TOKEN_LIMIT, used, requested_tokens)


def tts_budget_exceeded(tts_summary: dict[str, Any], requested_chars: int = 0) -> bool:
    used = int(tts_summary.get("chars") or 0)
    return would_exceed_limit(DAILY_TTS_CHAR_LIMIT, used, requested_chars)


def proactive_budget_exceeded(used_count: int, requested_count: int = 1) -> bool:
    return would_exceed_limit(PROACTIVE_DAILY_LIMIT, used_count, requested_count)


def _format_remaining(limit: int, used: int, unit: str) -> str:
    if not is_limit_enabled(limit):
        return f"{used} {unit} / 不限"
    remaining = remaining_limit(limit, used)
    return f"{used} {unit} / {limit}，剩余 {remaining} {unit}"


def format_budget_summary(
    model_summary: dict[str, Any],
    tts_summary: dict[str, Any],
    *,
    proactive_used: int = 0,
) -> str:
    chat_tokens = get_kind_total_tokens(model_summary, "chat_completion")
    embedding_tokens = get_kind_total_tokens(model_summary, "embedding")
    tts_chars = int(tts_summary.get("chars") or 0)
    model_cost = float((model_summary.get("today") or {}).get("estimated_cost_cny") or 0.0)
    tts_cost = float(tts_summary.get("estimated_cost_cny") or 0.0)

    return "\n".join([
        f"预算统计({model_summary.get('date') or tts_summary.get('date') or ''})",
        f"聊天模型: {_format_remaining(DAILY_CHAT_TOKEN_LIMIT, chat_tokens, 'tokens')}",
        f"Embedding: {_format_remaining(DAILY_EMBEDDING_TOKEN_LIMIT, embedding_tokens, 'tokens')}",
        f"TTS: {_format_remaining(DAILY_TTS_CHAR_LIMIT, tts_chars, '字')}",
        f"主动聊天: {_format_remaining(PROACTIVE_DAILY_LIMIT, proactive_used, '次')}",
        f"今日估算花费: {model_cost + tts_cost:.4f} 元",
    ])

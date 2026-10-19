import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from virtualme.config import (
    CHAT_INPUT_PRICE_PER_1K_TOKENS,
    CHAT_OUTPUT_PRICE_PER_1K_TOKENS,
    EMBEDDING_PRICE_PER_1K_TOKENS,
    USAGE_TELEMETRY_FILE,
)

logger = logging.getLogger(__name__)
from virtualme.paths import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT
_TELEMETRY_LOCK = threading.Lock()




def telemetry_path() -> Path:
    path = Path(USAGE_TELEMETRY_FILE)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def estimate_tokens_from_text(text: str) -> int:
    clean_text = text or ""
    return len(clean_text) if clean_text else 0


def extract_usage_tokens(usage: object) -> dict[str, int]:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def read_int(name: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    prompt_tokens = read_int("prompt_tokens")
    completion_tokens = read_int("completion_tokens")
    total_tokens = read_int("total_tokens") or prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _estimated_cost_cny(
    kind: str,
    success: bool,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> float:
    if not success:
        return 0.0
    if kind == "chat_completion":
        cost = (
            prompt_tokens * CHAT_INPUT_PRICE_PER_1K_TOKENS
            + completion_tokens * CHAT_OUTPUT_PRICE_PER_1K_TOKENS
        ) / 1000
    elif kind == "embedding":
        cost = total_tokens * EMBEDDING_PRICE_PER_1K_TOKENS / 1000
    else:
        cost = 0.0
    return round(cost, 6)


def record_model_usage(
    *,
    kind: str,
    model: str,
    success: bool,
    elapsed_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: Optional[int] = None,
    estimated_tokens: bool = False,
    input_chars: int = 0,
    output_chars: int = 0,
    error: str = "",
) -> None:
    resolved_total = total_tokens if total_tokens is not None else prompt_tokens + completion_tokens
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "model": model,
        "status": "success" if success else "failed",
        "success": success,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(resolved_total or 0),
        "estimated_tokens": estimated_tokens,
        "input_chars": int(input_chars or 0),
        "output_chars": int(output_chars or 0),
        "estimated_cost_cny": _estimated_cost_cny(
            kind,
            success,
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            int(resolved_total or 0),
        ),
        "elapsed_ms": int(elapsed_ms or 0),
    }
    if error:
        event["error"] = error[:500]

    target = telemetry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with _TELEMETRY_LOCK:
        with open(target, "a", encoding="utf-8") as file:
            file.write(line + "\n")


def _empty_bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "success_calls": 0,
        "failed_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_tokens": 0,
        "estimated_cost_cny": 0.0,
        "elapsed_ms": 0,
    }


def _add_event(bucket: dict[str, Any], event: dict[str, Any]) -> None:
    success = bool(event.get("success", event.get("status") == "success"))
    bucket["calls"] += 1
    bucket["success_calls" if success else "failed_calls"] += 1
    if success:
        bucket["prompt_tokens"] += int(event.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(event.get("completion_tokens") or 0)
        bucket["total_tokens"] += int(event.get("total_tokens") or 0)
        bucket["estimated_tokens"] += 1 if event.get("estimated_tokens") else 0
        bucket["estimated_cost_cny"] += float(event.get("estimated_cost_cny") or 0.0)
    bucket["elapsed_ms"] += int(event.get("elapsed_ms") or 0)


def get_model_usage_summary(day: Optional[str] = None) -> dict[str, Any]:
    target_day = day or datetime.now().date().isoformat()
    summary: dict[str, Any] = {
        "date": target_day,
        "today": _empty_bucket(),
        "total": _empty_bucket(),
        "today_by_kind": {},
        "total_by_kind": {},
        "today_by_model": {},
    }
    path = telemetry_path()
    if not path.exists():
        return summary

    try:
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue

                kind = str(event.get("kind") or "unknown")
                model = str(event.get("model") or "unknown")
                _add_event(summary["total"], event)
                _add_event(summary["total_by_kind"].setdefault(kind, _empty_bucket()), event)

                if str(event.get("timestamp", ""))[:10] != target_day:
                    continue
                _add_event(summary["today"], event)
                _add_event(summary["today_by_kind"].setdefault(kind, _empty_bucket()), event)
                _add_event(summary["today_by_model"].setdefault(model, _empty_bucket()), event)
    except Exception:
        logger.exception("Failed to read model usage telemetry")

    for bucket_group in (
        [summary["today"], summary["total"]]
        + list(summary["today_by_kind"].values())
        + list(summary["total_by_kind"].values())
        + list(summary["today_by_model"].values())
    ):
        bucket_group["estimated_cost_cny"] = round(float(bucket_group["estimated_cost_cny"] or 0.0), 6)
    return summary


def _format_bucket(label: str, bucket: dict[str, Any]) -> str:
    return (
        f"{label}: {bucket.get('success_calls', 0)} 成功 / "
        f"{bucket.get('failed_calls', 0)} 失败, "
        f"{bucket.get('total_tokens', 0)} tokens, "
        f"约 {float(bucket.get('estimated_cost_cny') or 0):.4f} 元"
    )


def format_model_usage_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"模型用量统计({summary.get('date', '')})",
        _format_bucket("今日", summary.get("today") or {}),
        _format_bucket("累计", summary.get("total") or {}),
    ]

    today_by_kind = summary.get("today_by_kind") or {}
    if today_by_kind:
        lines.append("今日分项:")
        for kind, bucket in sorted(today_by_kind.items()):
            lines.append(f"- {_format_bucket(kind, bucket)}")

    today_by_model = summary.get("today_by_model") or {}
    if today_by_model:
        lines.append("今日模型:")
        for model, bucket in sorted(today_by_model.items()):
            estimated_hint = "，含估算" if bucket.get("estimated_tokens") else ""
            lines.append(f"- {_format_bucket(model, bucket)}{estimated_hint}")
    return "\n".join(lines)

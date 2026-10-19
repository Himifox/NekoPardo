import logging
import time
from openai import AsyncOpenAI
from virtualme.config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL
from virtualme.telemetry.budget import budget_scope
from virtualme.telemetry.usage import estimate_tokens_from_text, extract_usage_tokens, get_model_usage_summary, record_model_usage, telemetry_path
logger = logging.getLogger(__name__)

async def embed_text(text: str) -> list[float]:
    if not EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_KEY is not configured")

    started_at = time.perf_counter()
    requested_tokens = estimate_tokens_from_text(text)
    async with budget_scope("embedding", requested_tokens, get_model_usage_summary, telemetry_path()) as reservation:
        try:
            async with AsyncOpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL) as client:
                response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        except Exception as exc:
            usage = {"prompt_tokens": requested_tokens, "completion_tokens": 0, "total_tokens": requested_tokens}
            _record_embedding_usage(text, False, started_at, usage, True, error=str(exc))
            raise
        usage = extract_usage_tokens(getattr(response, "usage", None))
        estimated_tokens = usage["total_tokens"] <= 0
        if estimated_tokens:
            usage["prompt_tokens"] = requested_tokens
            usage["total_tokens"] = requested_tokens
        reservation.settle(usage["total_tokens"], lambda: record_model_usage(
            kind="embedding", model=EMBEDDING_MODEL, success=True,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            prompt_tokens=usage["prompt_tokens"], total_tokens=usage["total_tokens"],
            estimated_tokens=estimated_tokens, input_chars=len(text or ""),
        ))
        return list(response.data[0].embedding)


def _record_embedding_usage(
    text: str,
    success: bool,
    started_at: float,
    usage: dict[str, int],
    estimated_tokens: bool,
    *,
    error: str = "",
) -> None:
    try:
        record_model_usage(
            kind="embedding",
            model=EMBEDDING_MODEL,
            success=success,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            estimated_tokens=estimated_tokens,
            input_chars=len(text or ""),
            error=error,
        )
    except Exception:
        logger.exception("Failed to record embedding usage telemetry")

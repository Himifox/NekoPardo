import logging
import time
from typing import Optional


from virtualme.telemetry.budget import BudgetExceededError, budget_scope
from virtualme.config import DASHSCOPE_TTS_API_KEY, DOUBAO_TTS_API_KEY, DOUBAO_TTS_VOICE, TTS_FALLBACK_ENABLED, TTS_FALLBACK_PROVIDER, TTS_PROVIDER

from virtualme.speech.providers import get_doubao_audio, get_dashscope_audio, get_sovits_audio
from virtualme.telemetry.speech import _billable_chars, _telemetry_path, _record_tts_telemetry, get_tts_usage_summary, format_tts_usage_summary
from virtualme.speech.audio import to_api_path

logger = logging.getLogger(__name__)


def _provider_key(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    aliases = {
        "dash": "dashscope",
        "qwen": "dashscope",
        "aliyun": "dashscope",
        "ali": "dashscope",
        "sovits": "gsv",
        "gptsovits": "gsv",
        "gpt-sovits": "gsv",
    }
    return aliases.get(normalized, normalized)


def _provider_is_configured(provider: str) -> bool:
    if provider == "doubao":
        return bool(DOUBAO_TTS_API_KEY and DOUBAO_TTS_VOICE)
    if provider == "dashscope":
        return bool(DASHSCOPE_TTS_API_KEY)
    if provider == "gsv":
        return True
    return False


def _provider_chain(preferred_provider: Optional[str] = None) -> list[str]:
    primary = _provider_key(preferred_provider or TTS_PROVIDER or "auto")
    if primary == "auto":
        chain = [provider for provider in ("doubao", "dashscope") if _provider_is_configured(provider)]
    else:
        chain = [primary]

    if TTS_FALLBACK_ENABLED:
        fallback = _provider_key(TTS_FALLBACK_PROVIDER or "gsv")
        if fallback:
            chain.append(fallback)
        chain.append("gsv")

    deduped: list[str] = []
    for provider in chain:
        if provider and provider not in deduped:
            deduped.append(provider)
    return deduped


async def get_tts_audio(
    text: str,
    ref_path: Optional[str] = None,
    *,
    provider: Optional[str] = None,
    batch_size: int = 30,
    sample_steps: int = 64,
    speed_factor: Optional[float] = None,
) -> Optional[str]:
    chars = _billable_chars(text)
    try:
        async with budget_scope("tts", chars, get_tts_usage_summary, _telemetry_path()) as reservation:
            return await _synthesize_tts_audio(
                reservation, text, ref_path, provider=provider, batch_size=batch_size,
                sample_steps=sample_steps, speed_factor=speed_factor,
            )
    except BudgetExceededError:
        logger.warning("Skip TTS: daily TTS char budget exceeded")
        try:
            _record_tts_telemetry("budget_guard", text, False, 0)
        except Exception:
            logger.exception("Required TTS telemetry failed while enforcing budget")
        return None


async def _synthesize_tts_audio(reservation, text, ref_path, *, provider, batch_size, sample_steps, speed_factor):
    chars = _billable_chars(text)
    with reservation:
        for candidate in _provider_chain(provider):
            started_at = time.perf_counter()
            if candidate == "doubao":
                audio = await get_doubao_audio(text)
            elif candidate == "dashscope":
                audio = await get_dashscope_audio(text)
            elif candidate == "gsv":
                audio = await get_sovits_audio(
                    text,
                    ref_path=ref_path,
                    batch_size=batch_size,
                    sample_steps=sample_steps,
                    speed_factor=speed_factor,
                )
            else:
                logger.warning("Unknown TTS provider '%s'; skip", candidate)
                audio = None

            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            try:
                if audio:
                    reservation.settle(chars, lambda: _record_tts_telemetry(candidate, text, True, elapsed_ms))
                else:
                    _record_tts_telemetry(candidate, text, False, elapsed_ms)
            except Exception:
                logger.exception("Required TTS telemetry failed; suppressing voice output")
                return None
            if audio:
                logger.info("TTS provider '%s' succeeded", candidate)
                return audio
            logger.warning("TTS provider '%s' failed; trying next fallback if available", candidate)
    return None

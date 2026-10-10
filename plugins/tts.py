import base64
import asyncio
import binascii
import io
import json
import logging
import os
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from plugins.budget_guard import BudgetExceededError, budget_scope
from plugins.config import (
    DASHSCOPE_TTS_API_KEY,
    DASHSCOPE_TTS_MODEL,
    DASHSCOPE_TTS_PRICE_PER_10K_CHARS,
    DASHSCOPE_TTS_VOICE,
    DASHSCOPE_TTS_WS_URL,
    DOUBAO_TTS_API_KEY,
    DOUBAO_TTS_BASE_URL,
    DOUBAO_TTS_CONTEXT_TEXT,
    DOUBAO_TTS_PRICE_PER_10K_CHARS,
    DOUBAO_TTS_RESOURCE_ID,
    DOUBAO_TTS_SPEED_RATIO,
    DOUBAO_TTS_VOICE,
    GSV_TTS_PRICE_PER_10K_CHARS,
    PROMPT_LANG,
    PROMPT_TEXT,
    REFER_WAV_PATH,
    SOVITS_API_URL,
    TTS_FALLBACK_ENABLED,
    TTS_FALLBACK_PROVIDER,
    TTS_TELEMETRY_FILE,
    TTS_PROVIDER,
)

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
_TELEMETRY_LOCK = threading.Lock()


def to_api_path(path: str) -> str:
    source_path = Path(path)
    if not source_path.is_absolute():
        source_path = BASE_DIR / source_path
    return str(source_path).replace("\\", "/")


def _encode_audio(audio: bytes) -> Optional[str]:
    if not audio:
        return None
    return base64.b64encode(audio).decode("utf-8")


def _wav_bytes(pcm: bytes, sample_rate: int = 24000) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return buffer.getvalue()


def _looks_like_encoded_audio(audio: bytes) -> bool:
    return audio[:4] in {b"RIFF", b"OggS"} or audio[:3] == b"ID3"


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


def _telemetry_path() -> Path:
    target = Path(TTS_TELEMETRY_FILE)
    if not target.is_absolute():
        target = BASE_DIR / target
    return target


def _billable_chars(text: str) -> int:
    return len(text or "")


def _price_per_10k_chars(provider: str) -> float:
    prices = {
        "doubao": DOUBAO_TTS_PRICE_PER_10K_CHARS,
        "dashscope": DASHSCOPE_TTS_PRICE_PER_10K_CHARS,
        "gsv": GSV_TTS_PRICE_PER_10K_CHARS,
    }
    try:
        return float(prices.get(provider, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _estimated_cost_cny(provider: str, chars: int, success: bool) -> float:
    if not success or chars <= 0:
        return 0.0
    return round(chars * _price_per_10k_chars(provider) / 10000, 6)


def _record_tts_telemetry(provider: str, text: str, success: bool, elapsed_ms: int) -> None:
    chars = _billable_chars(text)
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "status": "success" if success else "failed",
        "chars": chars,
        "estimated_cost_cny": _estimated_cost_cny(provider, chars, success),
        "price_per_10k_chars_cny": _price_per_10k_chars(provider),
        "elapsed_ms": elapsed_ms,
        "estimated": True,
        "unit": "chars",
    }

    target = _telemetry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with _TELEMETRY_LOCK:
        with open(target, "a", encoding="utf-8") as file:
            file.write(line + "\n")


def get_tts_usage_summary(day: Optional[str] = None) -> dict:
    target_day = day or datetime.now().date().isoformat()
    summary = {
        "date": target_day,
        "chars": 0,
        "estimated_cost_cny": 0.0,
        "success_calls": 0,
        "failed_calls": 0,
        "providers": {},
    }
    path = _telemetry_path()
    if not path.exists():
        return summary

    try:
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(event.get("timestamp", ""))[:10] != target_day:
                    continue
                provider = str(event.get("provider") or "unknown")
                success = event.get("status") == "success"
                chars = int(event.get("chars") or 0)
                cost = float(event.get("estimated_cost_cny") or 0.0)
                provider_summary = summary["providers"].setdefault(
                    provider,
                    {"chars": 0, "estimated_cost_cny": 0.0, "success_calls": 0, "failed_calls": 0},
                )
                if success:
                    summary["success_calls"] += 1
                    provider_summary["success_calls"] += 1
                    summary["chars"] += chars
                    provider_summary["chars"] += chars
                    summary["estimated_cost_cny"] += cost
                    provider_summary["estimated_cost_cny"] += cost
                else:
                    summary["failed_calls"] += 1
                    provider_summary["failed_calls"] += 1
        summary["estimated_cost_cny"] = round(summary["estimated_cost_cny"], 6)
        for provider_summary in summary["providers"].values():
            provider_summary["estimated_cost_cny"] = round(provider_summary["estimated_cost_cny"], 6)
    except Exception:
        logger.exception("Failed to read TTS telemetry")
    return summary


def format_tts_usage_summary(summary: dict) -> str:
    lines = [
        f"TTS 用量统计（{summary.get('date', '')}）",
        f"成功：{summary.get('success_calls', 0)} 次，失败：{summary.get('failed_calls', 0)} 次",
        f"计费字符：{summary.get('chars', 0)} 字",
        f"预估花费：{float(summary.get('estimated_cost_cny') or 0):.4f} 元",
    ]
    providers = summary.get("providers") or {}
    if providers:
        lines.append("分厂商：")
    for provider, data in providers.items():
        lines.append(
            f"- {provider}: {data.get('success_calls', 0)} 成功 / "
            f"{data.get('failed_calls', 0)} 失败，"
            f"{data.get('chars', 0)} 字，"
            f"{float(data.get('estimated_cost_cny') or 0):.4f} 元"
        )
    return "\n".join(lines)


def _decode_audio_b64(value: object) -> bytes:
    if not isinstance(value, str) or not value.strip():
        return b""
    try:
        return base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return b""


def _extract_audio_from_json_obj(obj: object) -> bytes:
    if not isinstance(obj, dict):
        return b""

    code = obj.get("code")
    message = str(obj.get("message") or obj.get("msg") or "").strip().lower()
    if code not in (None, 0, "0") and message not in {"ok", "success", "succeed"}:
        raise RuntimeError(obj.get("message") or obj.get("msg") or obj)

    chunks: list[bytes] = []
    for key in ("data", "audio", "audio_data"):
        value = obj.get(key)
        chunk = _extract_audio_from_json_obj(value) if isinstance(value, dict) else _decode_audio_b64(value)
        if chunk:
            chunks.append(chunk)

    nested = obj.get("result") or obj.get("response")
    if isinstance(nested, dict):
        nested_audio = _extract_audio_from_json_obj(nested)
        if nested_audio:
            chunks.append(nested_audio)
    return b"".join(chunks)


def _extract_audio_from_text_fragment(fragment: str) -> bytes:
    decoder = json.JSONDecoder()
    chunks: list[bytes] = []
    index = 0
    parsed_json = False
    while index < len(fragment):
        while index < len(fragment) and fragment[index].isspace():
            index += 1
        if index >= len(fragment):
            break
        try:
            obj, end = decoder.raw_decode(fragment, index)
        except json.JSONDecodeError:
            if parsed_json:
                break
            return _decode_audio_b64(fragment[index:].strip())
        parsed_json = True
        chunk = _extract_audio_from_json_obj(obj)
        if chunk:
            chunks.append(chunk)
        index = end
    return b"".join(chunks)


def _extract_doubao_audio_bytes(raw: bytes) -> bytes:
    if _looks_like_encoded_audio(raw):
        return raw
    text = raw.decode("utf-8", errors="ignore")
    chunks: list[bytes] = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.strip()
        if not line or line == "[DONE]":
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        chunk = _extract_audio_from_text_fragment(line)
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


async def get_doubao_audio(text: str) -> Optional[str]:
    if not DOUBAO_TTS_API_KEY:
        logger.warning("DOUBAO_TTS_API_KEY is not configured; skip Doubao TTS")
        return None
    if not DOUBAO_TTS_VOICE:
        logger.warning("DOUBAO_TTS_VOICE is not configured; skip Doubao TTS")
        return None

    url = f"{DOUBAO_TTS_BASE_URL.rstrip('/')}/api/v3/tts/unidirectional"
    payload = {
        "user": {"uid": "nekopardo"},
        "req_params": {
            "text": text,
            "speaker": DOUBAO_TTS_VOICE,
            "audio_params": {
                "format": "wav",
                "sample_rate": 24000,
                "speed_ratio": DOUBAO_TTS_SPEED_RATIO,
            },
            "additions": json.dumps(
                {"context_texts": [DOUBAO_TTS_CONTEXT_TEXT] if DOUBAO_TTS_CONTEXT_TEXT else []},
                ensure_ascii=False,
            ),
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": DOUBAO_TTS_API_KEY,
        "X-Api-Resource-Id": DOUBAO_TTS_RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10), trust_env=False) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            logger.error("Doubao TTS API error %s - %s", response.status_code, response.text[:300])
            return None
        audio = _extract_doubao_audio_bytes(response.content)
        if not audio:
            logger.error("Doubao TTS response does not contain audio")
            return None
        if not _looks_like_encoded_audio(audio):
            audio = _wav_bytes(audio, sample_rate=24000)
        return _encode_audio(audio)
    except Exception:
        logger.exception("Doubao TTS exception")
    return None


async def get_dashscope_audio(text: str) -> Optional[str]:
    if not DASHSCOPE_TTS_API_KEY:
        logger.warning("DASHSCOPE_TTS_API_KEY or DASHSCOPE_API_KEY is not configured; skip DashScope TTS")
        return None

    try:
        import websockets
    except ImportError:
        logger.error("websockets is not installed; skip DashScope TTS")
        return None

    url = f"{DASHSCOPE_TTS_WS_URL.rstrip('/')}?model={quote(DASHSCOPE_TTS_MODEL, safe='')}"
    headers = {"Authorization": f"Bearer {DASHSCOPE_TTS_API_KEY}"}

    session_message = {
        "type": "session.update",
        "event_id": f"event_{int(time.time() * 1000)}",
        "session": {
            "mode": "server_commit",
            "voice": DASHSCOPE_TTS_VOICE or "Momo",
            "response_format": "pcm",
            "sample_rate": 24000,
            "channels": 1,
            "bit_depth": 16,
        },
    }
    append_message = {
        "type": "input_text_buffer.append",
        "event_id": f"event_{int(time.time() * 1000)}_append",
        "text": text,
    }
    commit_message = {
        "type": "input_text_buffer.commit",
        "event_id": f"event_{int(time.time() * 1000)}_commit",
    }

    chunks: list[bytes] = []
    try:
        async with websockets.connect(url, additional_headers=headers, proxy=None, open_timeout=10) as websocket:
            await websocket.send(json.dumps(session_message, ensure_ascii=False))

            ready = False
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(0.1, deadline - time.monotonic())))
                event_type = event.get("type")
                if event_type in {"session.created", "session.updated"}:
                    ready = True
                    break
                if event_type == "error":
                    logger.error("DashScope TTS session error: %s", event)
                    return None
            if not ready:
                logger.error("DashScope TTS session was not ready in time")
                return None

            await websocket.send(json.dumps(append_message, ensure_ascii=False))
            await websocket.send(json.dumps(commit_message, ensure_ascii=False))

            while True:
                event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=60.0))
                event_type = event.get("type")
                if event_type == "response.audio.delta":
                    chunk = _decode_audio_b64(event.get("delta", ""))
                    if chunk:
                        chunks.append(chunk)
                elif event_type in {"response.done", "response.audio.done", "output.done"}:
                    break
                elif event_type == "error":
                    logger.error("DashScope TTS synthesis error: %s", event)
                    return None
        if not chunks:
            logger.error("DashScope TTS response does not contain audio")
            return None
        return _encode_audio(_wav_bytes(b"".join(chunks), sample_rate=24000))
    except Exception:
        logger.exception("DashScope TTS exception")
    return None


async def get_sovits_audio(
    text: str,
    ref_path: Optional[str] = None,
    *,
    batch_size: int = 30,
    sample_steps: int = 64,
    speed_factor: Optional[float] = None,
) -> Optional[str]:
    try:
        target_ref = to_api_path(ref_path or REFER_WAV_PATH)
        if not os.path.exists(target_ref):
            logger.error("Reference audio path does not exist: %s", target_ref)
            return None

        params = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": target_ref,
            "prompt_text": PROMPT_TEXT,
            "prompt_lang": PROMPT_LANG,
            "top_k": 5,
            "top_p": 0.95,
            "temperature": 0.9,
            "text_split_method": "cut5",
            "batch_size": batch_size,
            "seed": -1,
            "parallel_infer": True,
            "Repetition_Penalty": 1.4,
            "sample_steps": sample_steps,
            "fragment_interval": 0.3,
        }
        if speed_factor is not None:
            params["speed_factor"] = speed_factor

        async with httpx.AsyncClient(timeout=160.0, trust_env=False) as http_client:
            response = await http_client.post(
                SOVITS_API_URL,
                timeout=120.0,
                json=params,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 200:
                return _encode_audio(response.content)
            logger.error("SOVITS API error %s - %s", response.status_code, response.text)
    except Exception:
        logger.exception("GPT-SoVITS synthesis exception")
    return None


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

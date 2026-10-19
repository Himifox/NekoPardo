import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional
from urllib.parse import quote

import httpx

from virtualme.config import DASHSCOPE_TTS_API_KEY, DASHSCOPE_TTS_MODEL, DASHSCOPE_TTS_VOICE, DASHSCOPE_TTS_WS_URL, DOUBAO_TTS_API_KEY, DOUBAO_TTS_BASE_URL, DOUBAO_TTS_CONTEXT_TEXT, DOUBAO_TTS_RESOURCE_ID, DOUBAO_TTS_SPEED_RATIO, DOUBAO_TTS_VOICE, PROMPT_LANG, PROMPT_TEXT, REFER_WAV_PATH, SOVITS_API_URL

from virtualme.speech.audio import _decode_audio_b64, _encode_audio, _extract_doubao_audio_bytes, _looks_like_encoded_audio, _wav_bytes, to_api_path
logger = logging.getLogger(__name__)

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

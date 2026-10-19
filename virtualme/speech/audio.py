import base64
import binascii
import io
import json
import wave
from pathlib import Path
from typing import Optional
from virtualme.paths import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT

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

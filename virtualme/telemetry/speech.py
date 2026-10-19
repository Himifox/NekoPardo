import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


from virtualme.config import DASHSCOPE_TTS_PRICE_PER_10K_CHARS, DOUBAO_TTS_PRICE_PER_10K_CHARS, GSV_TTS_PRICE_PER_10K_CHARS, TTS_TELEMETRY_FILE

from virtualme.paths import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT
logger = logging.getLogger(__name__)
_TELEMETRY_LOCK = threading.Lock()

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

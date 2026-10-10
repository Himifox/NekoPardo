import os
from typing import Dict, Optional


def _getenv(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _getenv_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _getenv_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ================= 配置区域 =================
SOVITS_API_URL = _getenv("SOVITS_API_URL", "http://127.0.0.1:9880/tts")
REFER_WAV_PATH = "ref_audio/罐头.wav"  # 建议换成帕朵的参考音频
PROMPT_TEXT = "罐头，你怎么才回来……嗯？找到了个开店的好地方？在哪在哪？"  # 对应参考音频的文字
AUX_PATH_1 = "ref_audio/罐头，你怎么才回来……嗯？找到了个开店的好地方？在哪在哪？.wav"
AUX_PATH_2 = "ref_audio/喵喵喵 喵喵喵 喵喵喵.wav"
aux_ref_audio_paths = [AUX_PATH_1, AUX_PATH_2]
PROMPT_LANG = "zh"
# 参考音频目录与关键词映射（可在此手动添加显式映射）
REF_AUDIO_DIR = "ref_audio"
REF_KEYWORD_MAP: Dict[str, str] = {}
# 缓存配置：避免每次请求都扫描目录
REF_MAP_CACHE: Optional[Dict[str, str]] = None
REF_MAP_CACHE_TIME: float = 0
# 缓存过期时间（秒）
REF_MAP_TTL = 300

API_KEY = _getenv("DASHSCOPE_API_KEY")
QWEN_VL_API_KEY = _getenv("QWEN_VL_API_KEY")
BASE_URL = _getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL_NAME = _getenv("MODEL_NAME", "qwen-plus")
QWEN_VL_MODEL = "qwen-vl-plus"
STICKER_COLLECTION_ENABLED = _getenv_bool("STICKER_COLLECTION_ENABLED", True)
STICKER_COLLECTION_ACTIVE_GROUPS_ONLY = _getenv_bool("STICKER_COLLECTION_ACTIVE_GROUPS_ONLY", True)
STICKER_RECOGNIZE_EXISTING = _getenv_bool("STICKER_RECOGNIZE_EXISTING", False)
STICKER_SEND_LOCAL_FIRST = _getenv_bool("STICKER_SEND_LOCAL_FIRST", True)

TTS_PROVIDER = _getenv("TTS_PROVIDER", "auto").strip().lower()
TTS_FALLBACK_PROVIDER = _getenv("TTS_FALLBACK_PROVIDER", "gsv").strip().lower()
TTS_FALLBACK_ENABLED = _getenv_bool("TTS_FALLBACK_ENABLED", True)
TTS_TELEMETRY_FILE = _getenv("TTS_TELEMETRY_FILE", "data/tts_usage.jsonl")
TTS_PRICE_PER_10K_CHARS = _getenv_float("TTS_PRICE_PER_10K_CHARS", 5.0)

DASHSCOPE_TTS_API_KEY = _getenv("DASHSCOPE_TTS_API_KEY", API_KEY)
DASHSCOPE_TTS_WS_URL = _getenv("DASHSCOPE_TTS_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
DASHSCOPE_TTS_MODEL = _getenv("DASHSCOPE_TTS_MODEL", "qwen3-tts-flash-realtime-2025-11-27")
DASHSCOPE_TTS_VOICE = _getenv("DASHSCOPE_TTS_VOICE", "Momo")
DASHSCOPE_TTS_PRICE_PER_10K_CHARS = _getenv_float(
    "DASHSCOPE_TTS_PRICE_PER_10K_CHARS",
    TTS_PRICE_PER_10K_CHARS,
)

DOUBAO_TTS_API_KEY = _getenv("DOUBAO_TTS_API_KEY")
DOUBAO_TTS_BASE_URL = _getenv("DOUBAO_TTS_BASE_URL", "https://openspeech.bytedance.com")
DOUBAO_TTS_RESOURCE_ID = _getenv("DOUBAO_TTS_RESOURCE_ID", "seed-icl-2.0")
DOUBAO_TTS_VOICE = _getenv("DOUBAO_TTS_VOICE")
DOUBAO_TTS_SPEED_RATIO = _getenv_float("DOUBAO_TTS_SPEED_RATIO", 1.08)
DOUBAO_TTS_PRICE_PER_10K_CHARS = _getenv_float(
    "DOUBAO_TTS_PRICE_PER_10K_CHARS",
    TTS_PRICE_PER_10K_CHARS,
)
DOUBAO_TTS_CONTEXT_TEXT = _getenv(
    "DOUBAO_TTS_CONTEXT_TEXT",
    "Use natural, lively spoken Chinese. Keep the voice clear and slightly brisk.",
)
GSV_TTS_PRICE_PER_10K_CHARS = _getenv_float("GSV_TTS_PRICE_PER_10K_CHARS", 0.0)
HISTORY_FILE_PATH = _getenv("HISTORY_FILE_PATH", "MSG/group_712851492_20260203_231902.json")
LONG_TERM_MEMORY_FILE = _getenv("LONG_TERM_MEMORY_FILE", "chat_data.jsonl")
VECTOR_MEMORY_ENABLED = _getenv_bool("VECTOR_MEMORY_ENABLED", False)
EMBEDDING_API_KEY = _getenv("EMBEDDING_API_KEY", API_KEY)
EMBEDDING_BASE_URL = _getenv("EMBEDDING_BASE_URL", BASE_URL)
EMBEDDING_MODEL = _getenv("EMBEDDING_MODEL", "text-embedding-v4")
VECTOR_MEMORY_FILE = _getenv("VECTOR_MEMORY_FILE", "data/vector_memory.jsonl")
VECTOR_MEMORY_MIN_CHARS = _getenv_int("VECTOR_MEMORY_MIN_CHARS", 6)
VECTOR_MEMORY_TOP_K = _getenv_int("VECTOR_MEMORY_TOP_K", 5)
VECTOR_MEMORY_MIN_SCORE = _getenv_float("VECTOR_MEMORY_MIN_SCORE", 0.25)
VECTOR_MEMORY_MAX_ITEMS = _getenv_int("VECTOR_MEMORY_MAX_ITEMS", 5)
VECTOR_MEMORY_MAX_CHARS = _getenv_int("VECTOR_MEMORY_MAX_CHARS", 600)
VECTOR_MEMORY_ITEM_MAX_CHARS = _getenv_int("VECTOR_MEMORY_ITEM_MAX_CHARS", 120)
VECTOR_MEMORY_AUTO_INDEX_ENABLED = _getenv_bool("VECTOR_MEMORY_AUTO_INDEX_ENABLED", False)
VECTOR_MEMORY_AUTO_INDEX_INTERVAL_SECONDS = _getenv_int("VECTOR_MEMORY_AUTO_INDEX_INTERVAL_SECONDS", 300)
VECTOR_MEMORY_AUTO_INDEX_LIMIT = _getenv_int("VECTOR_MEMORY_AUTO_INDEX_LIMIT", 20)
USAGE_TELEMETRY_FILE = _getenv("USAGE_TELEMETRY_FILE", "data/model_usage.jsonl")
CHAT_INPUT_PRICE_PER_1K_TOKENS = _getenv_float("CHAT_INPUT_PRICE_PER_1K_TOKENS", 0.0)
CHAT_OUTPUT_PRICE_PER_1K_TOKENS = _getenv_float("CHAT_OUTPUT_PRICE_PER_1K_TOKENS", 0.0)
EMBEDDING_PRICE_PER_1K_TOKENS = _getenv_float("EMBEDDING_PRICE_PER_1K_TOKENS", 0.0)
DAILY_CHAT_TOKEN_LIMIT = _getenv_int("DAILY_CHAT_TOKEN_LIMIT", 0)
DAILY_EMBEDDING_TOKEN_LIMIT = _getenv_int("DAILY_EMBEDDING_TOKEN_LIMIT", 0)
DAILY_TTS_CHAR_LIMIT = _getenv_int("DAILY_TTS_CHAR_LIMIT", 0)
PROACTIVE_DAILY_LIMIT = _getenv_int("PROACTIVE_DAILY_LIMIT", 5)
PROACTIVE_USAGE_FILE = _getenv("PROACTIVE_USAGE_FILE", "data/proactive_usage.json")

ADMIN_UID = _getenv("ADMIN_UID", "3461737415")  # 你的纯数字 UID
TARGET_UID = _getenv("TARGET_UID", "u_MkWCKLdJG7Jubt9cQXbSpg")  # 语料学习目标 UID
STARTUP_GROUP_ID = _getenv_int("STARTUP_GROUP_ID", 712851492)
ACTIVATE_COMMAND = "#Neko"  # 激活指令
WHITE_LIST_FILE = _getenv("WHITE_LIST_FILE", "active_groups.json")

TEXT_PROBABILITY = 0.9
VOICE_PROBABILITY = 0.5
GLOBAL_CD = 30  # 全局冷却时间，单位秒
VOICE_KEYWORDS = ["语音", "声音", "唱歌", "听听", "想你了帕朵"]
TXT_KEYWORDS = ["帕朵"]

PROACTIVE_CHAT_ENABLED = _getenv_bool("PROACTIVE_CHAT_ENABLED", False)
PROACTIVE_SILENCE_MINUTES = _getenv_int("PROACTIVE_SILENCE_MINUTES", 10)
PROACTIVE_CHECK_INTERVAL_MINUTES = _getenv_int("PROACTIVE_CHECK_INTERVAL_MINUTES", 1)
PROACTIVE_TEXT_PROBABILITY = _getenv_float("PROACTIVE_TEXT_PROBABILITY", 1.0)

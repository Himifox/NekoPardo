"""Platform-independent message identity and delivery outcomes."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

@dataclass(frozen=True)
class SessionKey:
    account_id: str
    kind: Literal['group', 'private']
    conversation_id: int

@dataclass(frozen=True)
class MessageEnvelope:
    session: SessionKey
    sender_id: str
    sender_name: str
    message_id: str
    timestamp: float
    text: str
    segments: tuple[dict[str, Any], ...] = ()
    reply_to: str | None = None
    raw_text: str | None = None

class ReplyMode(Enum):
    TEXT = 'text'
    VOICE = 'voice'
    TEXT_AND_OPTIONAL_VOICE = 'text_and_optional_voice'
    IMAGE = 'image'

@dataclass(frozen=True)
class ReplyDecision:
    mode: ReplyMode
    reason: str

@dataclass
class DeliveryResult:
    text_sent: bool = False
    voice_sent: bool = False
    sticker_sent: bool = False
    message_ids: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def reply_sent(self) -> bool:
        return self.text_sent or self.voice_sent

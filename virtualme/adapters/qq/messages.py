"""Convert OneBot events into stable, platform-independent input."""
from virtualme.models import MessageEnvelope, SessionKey

def to_message(event, *, text: str | None = None) -> MessageEnvelope:
    group = getattr(event, 'group_id', None)
    session = SessionKey(str(event.self_id), 'group' if group is not None else 'private',
                         int(group if group is not None else event.user_id))
    segments = tuple({'type': segment.type, 'data': dict(segment.data)} for segment in event.message)
    reply = next((str(seg['data']['id']) for seg in segments
                  if seg['type'] == 'reply' and seg['data'].get('id') is not None), None)
    sender = getattr(event.sender, 'card', None) or getattr(event.sender, 'nickname', None) or '未知用户'
    return MessageEnvelope(session, str(event.user_id), sender, str(event.message_id),
                           float(event.time), event.get_plaintext().strip() if text is None else text,
                           segments, reply, getattr(event, 'raw_message', None))

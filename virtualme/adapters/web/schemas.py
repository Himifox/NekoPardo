from pydantic import BaseModel

class SystemSettingUpdate(BaseModel):
    content: str


class ChatTestIn(BaseModel):
    message: str
    reset: bool = False
    max_tokens: int = 300
    temperature: float = 1.0


class GroupAdd(BaseModel):
    group_id: int

"""Lazy, process-local SDK client. Importing this module creates no resources."""
from openai import AsyncOpenAI
from virtualme.config import API_KEY, BASE_URL

_client = None

def get_client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client

async def close_client():
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.close()

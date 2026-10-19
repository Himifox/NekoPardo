import re
import logging
import aiohttp
from virtualme.config import QWEN_VL_API_KEY, QWEN_VL_MODEL
logger = logging.getLogger(__name__)
from virtualme.stickers.store import load_collection

async def qwen_recognize_sticker(img_url: str) -> str | None:
    if not QWEN_VL_API_KEY:
        logger.warning("QWEN_VL_API_KEY is not configured; skip sticker recognition.")
        return None

    api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {QWEN_VL_API_KEY}",
    }
    prompt = """
        请你识别表情包意思，采用关键词，使用文字和颜文字，如：“害羞”，“盯着你”，“QAQ”，
        总字数最多10个。必要时，可以超过10个字数但不多于20个字。
        关键词按照可能性比例分先后顺序。

        【严格按照以下格式输出】
        案例1：“这张表情包的意思是：“惊讶”。关键词：惊讶、震撼、不可思议”。
        案例2："这张表情包的意思是：“不好意思”。关键词：无奈、尴尬、难过"
        案例3："这张图片的意思是：“困惑”。关键词：卖萌、困惑、不知道怎么办、QAQ（表示无奈或无语）"
    """
    pyload = {
        "model": QWEN_VL_MODEL,
        "input": {"messages": [{"role": "user", "content": [{"image": img_url}, {"text": prompt}]}]},
        "parameters": {"result_format": "message"},
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(api_url, json=pyload) as response:
                if response.status != 200: return None
                data = await response.json()
                return data["output"]["choices"][0]["message"]["content"][0]["text"].strip()
    except Exception as e:
        print(f"出错了:{e}")
        return None


async def find_md5_by_json(text: str) -> tuple | None:
    coll = await load_collection()
    for md5, item_data in coll.items():
        if isinstance(item_data, dict):
            description = item_data.get("meaning", "")
            url = item_data.get("url")
        else:
            description = str(item_data)
            url = None

        if not url: continue

        if "关键词" in description:
            parts = re.split(r'关键词[：:]\s*', description)
            if len(parts) > 1:
                keywords_part = parts[-1]
                keywords_list = re.split(r'[、,，]', keywords_part.replace("。", ""))
                for kw in keywords_list:
                    kw = kw.strip()
                    if kw and kw in text:
                        return md5, url
    return None

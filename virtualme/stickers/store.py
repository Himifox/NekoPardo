import os
import json
import hashlib
import tempfile
import logging
from pathlib import Path
import aiohttp
import aiofiles
from virtualme.storage.files import async_file_lock
logger = logging.getLogger(__name__)

COLLECTION_DIR = "sticker_collection"
COLLECTION_JSON = "sticker_collection.json"

async def load_collection():
    if not Path(COLLECTION_JSON).exists():
        return {}
    async with aiofiles.open(COLLECTION_JSON, "r", encoding="utf-8") as f:
        content = await f.read()
        return json.loads(content)


async def save_collection(collection):
    target = Path(COLLECTION_JSON)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(collection, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def update_collection_entry(img_md5: str, img_url: str, meaning=None) -> None:
    # Re-read after recognition: another event/process may have added stickers.
    async with async_file_lock(Path(str(COLLECTION_JSON) + ".lock")):
        collection = await load_collection()
        if meaning is None:
            previous = collection.get(img_md5, {})
            meaning = previous.get("meaning", previous) if isinstance(previous, dict) else previous
        collection[img_md5] = {"meaning": meaning, "url": img_url}
        await save_collection(collection)


async def md5_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            content = await response.read()
            md5_obj = hashlib.md5(content)
            return md5_obj.hexdigest()


def local_sticker_path(img_md5: str) -> str:
    return os.path.join(COLLECTION_DIR, f"{img_md5}.png")


async def download_sticker_image(img_url: str, img_md5: str) -> None:
    if not img_url or not img_md5:
        return
    os.makedirs(COLLECTION_DIR, exist_ok=True)
    local_img_path = local_sticker_path(img_md5)
    if os.path.exists(local_img_path):
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to download sticker image {img_md5}: HTTP {resp.status}")
                    return
                img_data = await resp.read()
        async with aiofiles.open(local_img_path, "wb") as f:
            await f.write(img_data)
        logger.info(f"Sticker image saved locally: {local_img_path}")
    except Exception:
        logger.exception(f"Failed to download sticker image: {img_md5}")

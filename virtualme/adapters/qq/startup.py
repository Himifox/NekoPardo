from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
import asyncio
import logging

from virtualme.config import REFER_WAV_PATH, STARTUP_GROUP_ID
from virtualme.speech.service import get_tts_audio
# logging
logger = logging.getLogger(__name__)
last_reply_time = {}

driver = get_driver()
# ================= 🚀 自动开机语音逻辑 =================
@driver.on_bot_connect
async def _(bot: Bot):
    """
    当机器人成功连接到服务器时，自动触发
    """
    # 1. 填入你想要接收开机语音的群号
    target_group = STARTUP_GROUP_ID  # 默认保持原开机语音群，可通过环境变量覆盖

    # 2. 稍微延迟一下，等连接彻底稳定
    await asyncio.sleep(3)

    logger.info(f"✨ 帕朵正在准备开机语音...")

    # 3. 设置帕朵的开机台词
    startup_text = "欸嘿嘿，祝大家新年快乐呀！帕朵在这里祝大家事业顺利！学业有成！哈哈"

    try:
        # 调用你插件里已有的语音合成函数
        # Cloud TTS is preferred; GPT-SoVITS remains the fallback provider.

        audio_b64 = await get_tts_audio(
            startup_text,
            REFER_WAV_PATH,
            batch_size=50,
            sample_steps=128,
            speed_factor=1.1,
        )
        if audio_b64:
            # 发送语音到指定群

            await bot.send_group_msg(
                group_id=target_group,
                message=MessageSegment.record(f"base64://{audio_b64}")
            )
            logger.info("✅ 帕朵开机语音发送成功！")
        else:
            logger.warning("❌ 帕朵开机语音合成失败了喵...")

    except Exception as e:
        logger.error(f"❌ 帕朵开机逻辑出现异常: {e}")

# =====================================================

from virtualme.runtime import shutdown
get_driver().on_shutdown(shutdown)

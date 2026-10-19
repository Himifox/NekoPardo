"""QQ application assembly; plugin registration stays explicit."""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter

nonebot.init()
nonebot.get_driver().register_adapter(Adapter)
for plugin in (
    "virtualme.adapters.qq.events",
    "virtualme.adapters.qq.commands",
    "virtualme.adapters.qq.stickers",
    "virtualme.adapters.qq.chat",
    "virtualme.adapters.qq.proactive",
    "virtualme.adapters.qq.startup",
):
    if nonebot.load_plugin(plugin) is None:
        raise RuntimeError(f'Failed to load required QQ plugin: {plugin}')

if __name__ == "__main__":
    nonebot.run(host="127.0.0.1", port=8081)

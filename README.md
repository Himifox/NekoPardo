<div align="center">

# NekoPardo

一只住在 QQ 群里的帕朵，会聊天、发表情、说语音，也会记住和大家相处的点滴。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![NoneBot](https://img.shields.io/badge/NoneBot-2.x-EB6EA5?style=flat-square)
![OneBot](https://img.shields.io/badge/OneBot-V11-4B5563?style=flat-square)

</div>

## 能做什么

- **陪你聊天**：以帕朵菲莉丝的性格参与群聊，结合最近的聊天内容回复。
- **看懂表情包**：通过 Qwen-VL 识别图片和表情，并收藏、发送表情包。
- **用语音回应**：支持豆包、DashScope TTS，可回退到本地 GPT-SoVITS。
- **记住相处的点滴**：本地保存长期记忆，可开启向量召回。
- **主动找话题**：群聊安静一段时间后主动破冰，默认关闭。
- **控制用量**：记录模型和语音消耗，支持每日预算限制。

## 快速开始

需要 Python 3.10+，以及能连接到机器人的 OneBot V11 实现（如 Lagrange）。以下命令在 Windows PowerShell 中执行。

### 1. 安装依赖

项目尚未固定依赖版本，基础依赖如下；建议在虚拟环境中安装：

```powershell
pip install nonebot2 nonebot-adapter-onebot openai httpx websockets
```

### 2. 配置环境变量

参考 [.env.example](.env.example) 设置模型密钥、管理员 QQ 和启动群号：

```env
DASHSCOPE_API_KEY=
QWEN_VL_API_KEY=
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
ADMIN_UID=
STARTUP_GROUP_ID=
```

请通过运行环境、启动脚本或进程管理器注入环境变量，不能仅依赖新建 `.env` 文件。

### 3. 启动

启动 OneBot V11 实现，配置其连接到机器人，然后运行：

```powershell
python bot.py
```

默认监听 `127.0.0.1:8081`。管理员在目标群发送 `#Neko` 启用机器人。

## 可选配置

| 配置 | 说明 |
| --- | --- |
| `TTS_PROVIDER` | `auto`、`doubao`、`dashscope` 或 `gsv`，配合对应密钥和音色使用 |
| `VECTOR_MEMORY_ENABLED` | 向量记忆召回，默认 `false`；开启前先构建记忆索引 |
| `PROACTIVE_CHAT_ENABLED` | 主动聊天，默认 `false` |
| `DAILY_CHAT_TOKEN_LIMIT` | 每日聊天 token 上限，`0` 表示不限 |
| `DAILY_EMBEDDING_TOKEN_LIMIT` | 每日 embedding token 上限，`0` 表示不限 |
| `DAILY_TTS_CHAR_LIMIT` | 每日语音字符上限，`0` 表示不限 |
| `PROACTIVE_DAILY_LIMIT` | 每日主动聊天次数上限，默认 `5` |

完整配置以 [plugins/config.py](plugins/config.py) 为准。

## 管理员命令

| 命令 | 作用 |
| --- | --- |
| `#Neko` | 在当前群启用机器人 |
| `#tts统计` | 查看今日语音用量 |
| `#模型用量` | 查看模型与 embedding token 用量 |
| `#预算统计` | 查看今日预算和估算花费 |
| `#记忆统计` | 查看长期记忆与向量索引状态 |
| `#构建记忆索引` | 增量构建向量记忆索引 |

## 开发与数据

入口为 `bot.py`，插件职责见 [plugins/README.md](plugins/README.md)。启动时只显式加载所需插件，避免扫描整个插件目录。

长期记忆默认保存在 `chat_data.jsonl`，索引与用量记录保存在 `data/`。请勿提交私人聊天数据或真实密钥。

## License

[MIT](LICENSE)

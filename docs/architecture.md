# pardo 架构

## 目录与依赖

```text
bot.py / admin_web.py / start.py          启动入口
virtualme/
  config.py / paths.py                   配置与固定路径（导入路径不加载设置）
  runtime.py                            后台任务和客户端关闭
  models.py / access.py                  消息身份、发送结果、群启用状态
  adapters/
    qq/                                 显式注册的 NoneBot 插件与 QQ 发送
    web/                                FastAPI 应用、路由、schemas、HTML
  chat/                                 回复策略、上下文、人设、生成编排
  memory/                               会话缓存、JSONL 记录、向量召回
  llm/                                  客户端、通用生成、embedding 请求
  speech/                               语音回退策略、厂商协议、音频解析
  stickers/                             表情识别、检索与存储
  proactive/                            静默发言业务和每日计数
  telemetry/                            模型/语音用量与预算
  storage/                              文件锁、群列表原子更新
tests/                                  按相同职责组织的 unittest
```

依赖方向：入口 → 业务/能力 → 外部客户端或存储。`virtualme` 的公共模块
不得导入 `plugins`、NoneBot 或 FastAPI；平台类型只在 `adapters` 中出现。
包的 `__init__.py` 不注册插件。架构测试固定这些边界。

## 运行链路

QQ 事件先转换为 `MessageEnvelope`，再依次执行权限与回复策略、上下文组装、
模型生成和 QQ 发送。`DeliveryResult` 区分文字、语音和表情结果，首次成功
发送文字或语音后记录回复。可选语音失败不重发已送达文字；取消会继续向上传播。

模型通用生成不加载人设、不改写输出；聊天业务负责现有文本替换。Web 测试聊天
仍使用独立会话和原有审核重试，不写 QQ 聊天记忆。管理页面的人设编辑仍指向
`virtualme/chat/persona.py`，机器人重启后读取新的人设。

所有长期后台任务通过 `runtime.start_task` 登记；退出时先取消并等待这些任务，
再关闭进程本地模型客户端。embedding 和语音的短期网络客户端仍按调用关闭。
机器人与管理后台是独立进程，各自拥有客户端，预算通过文件锁跨进程协调。

## 消息与历史数据

新 QQ 消息使用 `SessionKey(account_id, kind, conversation_id)` 区分机器人账号、
群聊和私聊。近期上下文和新向量记录按这个身份隔离。消息记录保留原文、平台
消息 ID、内容段及引用目标；清洗后的 `content` 用于既有模型上下文。
OneBot 消息 ID 可能是 NapCat 的短 ID，不能直接视为 QQ 数据库中的原始消息 ID。

旧 JSONL/向量文件保持原位、原格式可读，不自动迁移或推断它们的会话类型。
缺少账号和会话类型的旧向量不会进入新的带身份召回；管理员仍能查看原记录。
需要恢复这些记录的召回时，应先依据可靠来源确认身份，再做单独的数据迁移。
重构不触碰现有聊天数据、白名单、密钥、表情资源或人设内容。

## 命名与验证

模块使用小写 snake_case：service 编排操作，policy 做规则判断，store 读写领域
数据，providers 实现厂商协议，adapters 转换输入和发送输出。小模块不预先拆分。

```bash
uv sync --locked
uv run python -m unittest discover -s tests
uv run python start.py
uv run python admin_web.py
```

历史搜索尚未实现；将来作为独立业务接入 QQ commands，复用消息身份、模型调用、
预算和发送能力。

# Plugins

插件目录采用小写 `snake_case` 命名。文件名优先描述职责，而不是描述历史实现或第三方组件。

## Startup Plugins

`bot.py` 只显式加载当前线上链路需要的插件：

```python
nonebot.load_plugin("plugins.pardo")
nonebot.load_plugin("plugins.pardo_chat")
nonebot.load_plugin("plugins.proactive_monitor")
```

不要直接使用 `nonebot.load_plugins("plugins")` 扫描整个目录，避免实验模块被意外注册。

## Active Modules

| File | Role |
| --- | --- |
| `pardo.py` | Bot connect startup behavior and role greeting |
| `pardo_chat.py` | Group/private chat triggers, admin commands, reply delivery |
| `proactive_monitor.py` | Silence monitor and proactive chat trigger |
| `config.py` | Single configuration entrypoint |
| `tts.py` | Cloud TTS, GPT-SoVITS fallback, TTS telemetry |
| `memory.py` | Group message listener; delegates storage to `virtualme.memory.session` |
| `memory_store.py` | Long-term JSONL memory storage |
| `vector_memory.py` | Embedding index build/search and memory recall formatting |
| `usage_telemetry.py` | Chat and embedding token telemetry |
| `budget_guard.py` | Daily cost and usage limits |
| `proactive_usage.py` | Daily proactive chat counter |
| `sticker_service.py` | Sticker learning, recognition, collection access, smart sending |

## Shared Chat Core

`virtualme/` contains ordinary Python modules without NoneBot registration:

| Module | Role |
| --- | --- |
| `chat/service.py` | Shared group/private/proactive reply generation, successful-delivery memory recording, lazy model client lifecycle |
| `chat/context.py` | Existing persona, group history, sampled messages and optional vector recall prompt assembly |
| `chat/text_policy.py` | Existing text replacements and TTS action removal |
| `chat/persona.py` | Existing Python persona constant; admin edits this source, bot reloads it on restart |
| `chat/access.py` | Shared active-group cache and file refresh |
| `memory/session.py` | Existing 20-message cache and incoming/outgoing memory recording |

Adapters retain permission checks, triggers and delivery. Incoming group messages
are recorded by `plugins.memory` once, including messages that do not trigger a
reply; private messages are recorded after authorization. Reply generation never
records a delivered reply. Adapters record it only after sending text or audio.
Proactive chat uses the same core without importing the chat adapter. The admin
test conversation remains independent in this phase. Existing integer session
keys, storage formats, prompt text and model parameters are preserved.

## Optional / Legacy Modules

| File | Role |
| --- | --- |
| `recorder.py` | Compatibility helper for writing plain messages to long-term memory |
| `action_base.py` | Base classes for action-style extensions |
| `proactive_topic_action.py` | Prototype action for proactive topics |
| `sticker_sender_service.py` | Standalone sticker matching/sending helper |

## Naming Rules

- Use lowercase `snake_case.py`.
- Name modules by product responsibility: `pardo_chat`, `vector_memory`, `budget_guard`.
- Avoid provider names in high-level modules unless the file only wraps that provider.
- Keep plugin registration explicit in `bot.py`.
- When renaming a plugin module, update `bot.py`, internal imports, README files, and smoke tests in the same change.

from nonebot import on_fullmatch
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from virtualme.telemetry.budget import format_budget_summary

from virtualme.proactive.usage import get_proactive_daily_count
from virtualme.config import ADMIN_UID
from virtualme.speech.service import format_tts_usage_summary, get_tts_usage_summary
from virtualme.telemetry.usage import (
    format_model_usage_summary,
    get_model_usage_summary,
)
from virtualme.memory.retrieval import (
    build_vector_memory_index,
    format_build_stats,
    format_vector_memory_stats,
    get_vector_memory_stats,
)

tts_usage_query = on_fullmatch(
    ("#tts统计", "#tts用量", "#语音统计", "#语音花费"),
    priority=2,
    block=True,
)


@tts_usage_query.handle()
async def handle_tts_usage_query():
    summary = get_tts_usage_summary()
    await tts_usage_query.finish(format_tts_usage_summary(summary))


vector_memory_stats_query = on_fullmatch("#记忆统计", priority=2, block=True)
vector_memory_build_query = on_fullmatch("#构建记忆索引", priority=2, block=True)
model_usage_query = on_fullmatch(("#模型用量", "#token统计"), priority=2, block=True)
budget_usage_query = on_fullmatch("#预算统计", priority=2, block=True)


def is_admin_event(event: GroupMessageEvent) -> bool:
    return str(event.user_id).strip() == ADMIN_UID


@vector_memory_stats_query.handle()
async def handle_vector_memory_stats(event: GroupMessageEvent):
    if not is_admin_event(event):
        await vector_memory_stats_query.finish("老板，这个账本只能管理员看哦。")
    await vector_memory_stats_query.finish(format_vector_memory_stats(get_vector_memory_stats()))


@vector_memory_build_query.handle()
async def handle_vector_memory_build(event: GroupMessageEvent):
    if not is_admin_event(event):
        await vector_memory_build_query.finish("老板，这个索引只能管理员整理哦。")
    stats = await build_vector_memory_index()
    await vector_memory_build_query.finish(format_build_stats(stats))


@model_usage_query.handle()
async def handle_model_usage_query(event: GroupMessageEvent):
    if not is_admin_event(event):
        await model_usage_query.finish("老板，这个账本只能管理员看哦。")
    await model_usage_query.finish(format_model_usage_summary(get_model_usage_summary()))


@budget_usage_query.handle()
async def handle_budget_usage_query(event: GroupMessageEvent):
    if not is_admin_event(event):
        await budget_usage_query.finish("老板，这个预算账本只能管理员看哦。")
    await budget_usage_query.finish(
        format_budget_summary(
            get_model_usage_summary(),
            get_tts_usage_summary(),
            proactive_used=get_proactive_daily_count(),
        )
    )

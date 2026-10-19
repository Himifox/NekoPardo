"""Budgeted model generation, independent of chat persona and QQ."""
from virtualme.config import MODEL_NAME
from virtualme.llm.client import get_client
import time
from virtualme.telemetry.usage import estimate_tokens_from_text, extract_usage_tokens, get_model_usage_summary, telemetry_path, record_model_usage

async def generate_reply(messages: list[dict], **parameters) -> str:
    response = await request_chat_completion(get_client(), model=MODEL_NAME, messages=messages, **parameters)
    return response.choices[0].message.content.strip()


async def request_chat_completion(client, **parameters):
    """All production and admin chat calls share budget enforcement and usage."""
    from virtualme.telemetry.budget import budget_scope

    messages = parameters["messages"]
    input_text = "\n".join(str(message.get("content") or "") for message in messages)
    requested = estimate_tokens_from_text(input_text) + int(parameters.get("max_tokens") or 0)
    async with budget_scope("chat_completion", requested, get_model_usage_summary, telemetry_path()) as reservation:
        started = time.perf_counter()
        try:
            response = await client.chat.completions.create(**parameters)
        except Exception as exc:
            record_model_usage(kind="chat_completion", model=parameters["model"], success=False,
                               elapsed_ms=round((time.perf_counter() - started) * 1000),
                               input_chars=len(input_text), error=str(exc))
            raise
        choices = getattr(response, "choices", None) or []
        output_text = str(getattr(choices[0].message, "content", "") or "") if choices else ""
        tokens = extract_usage_tokens(getattr(response, "usage", None))
        estimated = tokens["total_tokens"] <= 0
        if estimated:
            tokens = {"prompt_tokens": estimate_tokens_from_text(input_text),
                      "completion_tokens": estimate_tokens_from_text(output_text)}
            tokens["total_tokens"] = tokens["prompt_tokens"] + tokens["completion_tokens"]
        reservation.settle(tokens["total_tokens"], lambda: record_model_usage(
            kind="chat_completion", model=parameters["model"], success=True,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            **tokens, estimated_tokens=estimated, input_chars=len(input_text), output_chars=len(output_text),
        ))
        return response

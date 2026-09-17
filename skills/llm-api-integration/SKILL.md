---
name: llm-api-integration
description: Build reliable LLM-powered features in Python/FastAPI — shared async SDK clients, streaming responses over Server-Sent Events, structured outputs validated with Pydantic, tool/function calling loops, retries with backoff, timeouts, token/cost tracking, prompt versioning, prompt-injection defenses, and testing/evaluating LLM code. Use this whenever the user calls OpenAI, Anthropic Claude, Gemini, or any LLM API from Python, builds a chatbot or AI endpoint, streams tokens to a frontend, wants JSON/structured output from a model, implements agents or tool calling, or asks how to make AI features production-ready.
---

# LLM API Integration

Treat the model as an unreliable, slow, expensive remote dependency: isolate it behind your own interface, bound every call, validate every output, and measure everything.

## Architecture

```
router (HTTP, streaming)  →  ai/service.py (use-case logic)  →  ai/client.py (provider adapter)
                                        ↘ prompts/ (versioned templates)
```

- Define a small `Protocol` for what your app needs (`complete`, `stream`, `extract`). Provider SDK types stay inside the adapter, so switching or mixing providers doesn't touch business code.
- Model names, max tokens and temperatures live in `Settings`, not scattered string literals.

## Shared client (one per process)

```python
# app/ai/client.py
from anthropic import AsyncAnthropic

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = AsyncAnthropic(            # reads ANTHROPIC_API_KEY from env
        timeout=60.0,
        max_retries=3,                         # SDK retries 429/5xx with backoff
    )
    yield
    await app.state.llm.close()

def get_llm(request: Request) -> AsyncAnthropic:
    return request.app.state.llm

LLMDep = Annotated[AsyncAnthropic, Depends(get_llm)]
```

Always use the **async** client inside `async def` routes; the sync client blocks the event loop. The same pattern applies to `openai.AsyncOpenAI`.

## Simple completion

```python
async def summarize(llm: AsyncAnthropic, text: str, settings: Settings) -> str:
    msg = await llm.messages.create(
        model=settings.llm_model,
        max_tokens=512,
        system="You summarize documents in 3 bullet points. Output only the bullets.",
        messages=[{"role": "user", "content": f"<document>\n{text}\n</document>"}],
    )
    log.info("llm_call", model=msg.model, input_tokens=msg.usage.input_tokens,
             output_tokens=msg.usage.output_tokens, stop_reason=msg.stop_reason)
    return "".join(block.text for block in msg.content if block.type == "text")
```

Check `stop_reason`: `max_tokens` means the answer was truncated.

## Streaming to the browser (SSE)

```python
import json
from fastapi.responses import StreamingResponse

class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)

@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, llm: LLMDep, user: CurrentUser, request: Request, settings: SettingsDep):
    async def events() -> AsyncIterator[str]:
        try:
            async with llm.messages.stream(
                model=settings.llm_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[m.model_dump() for m in body.messages],
            ) as stream:
                async for text in stream.text_stream:
                    if await request.is_disconnected():   # stop paying for tokens nobody reads
                        break
                    yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
                final = await stream.get_final_message()
                yield f"data: {json.dumps({'type': 'done', 'usage': final.usage.model_dump()})}\n\n"
        except Exception:
            log.exception("llm_stream_failed")
            yield f"data: {json.dumps({'type': 'error', 'message': 'generation failed'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

- Once streaming starts the status code is already 200 — report errors as an event, not an HTTP status.
- `X-Accel-Buffering: no` stops nginx from buffering the stream.
- JSON-encode each chunk so newlines in tokens don't break SSE framing.
- Don't hold a DB session open for the whole stream; load what you need first.

## Structured output with validation + repair

```python
from pydantic import BaseModel, ValidationError

class TicketTriage(BaseModel):
    category: Literal["billing", "bug", "feature", "other"]
    priority: Literal["low", "medium", "high"]
    summary: str = Field(max_length=200)

async def triage(llm: AsyncAnthropic, ticket: str, settings: Settings, attempts: int = 2) -> TicketTriage:
    schema = json.dumps(TicketTriage.model_json_schema())
    messages = [{"role": "user", "content": f"<ticket>\n{ticket}\n</ticket>\n\n"
                 f"Classify the ticket. Respond with only a JSON object matching this schema:\n{schema}"}]
    for _ in range(attempts):
        msg = await llm.messages.create(model=settings.llm_model, max_tokens=400, messages=messages)
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return TicketTriage.model_validate_json(raw)
        except ValidationError as exc:
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"That was invalid: {exc.errors()}. Return corrected JSON only."}]
    raise LLMOutputError("could not obtain valid triage")
```

Prefer the provider's native structured-output / tool-schema features when available (they constrain generation); keep Pydantic validation as the final gate either way. Libraries like `instructor` or `pydantic-ai` package this pattern.

## Tool calling loop

```python
TOOLS = [{
    "name": "get_order_status",
    "description": "Look up the status of one of the current user's orders.",
    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
}]

async def run_agent(llm, user, messages, max_steps: int = 5):
    for _ in range(max_steps):                              # hard cap: no infinite loops
        msg = await llm.messages.create(model=MODEL, max_tokens=1024, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason != "tool_use":
            return msg
        results = []
        for block in msg.content:
            if block.type == "tool_use":
                output = await dispatch_tool(block.name, block.input, user)   # authz uses *user*, not model input
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
    raise AgentStepLimitError()
```

Validate tool inputs with Pydantic, enforce the **caller's** permissions inside every tool, and require human confirmation for destructive actions.

## Prompt injection & safety
- Wrap untrusted content in clear delimiters (`<document>…</document>`) and tell the model it is data, not instructions.
- Never give the model more privilege than the requesting user; tools re-check authorization.
- Don't render model output as raw HTML; treat it as untrusted.
- Never put secrets or other users' data in prompts.
- Validate/allow-list any URLs, SQL or shell the model proposes before executing.

## Cost, limits, resilience
- Log model, input/output tokens, latency and a `prompt_version` for every call; aggregate per user/feature.
- Enforce per-user rate limits and daily token budgets before calling the API.
- Always set `max_tokens`; cap input size (truncate or summarize long histories).
- Use prompt caching for long, stable system prompts where the provider supports it.
- Circuit-break / degrade gracefully (cached answer, simpler model, friendly error) when the provider is down.
- Long jobs → task queue + 202 (see `fastapi-performance`).

## Prompts as code
Store prompts in `app/ai/prompts/` as versioned templates (e.g. `triage_v3.md`), load them at startup, and log the version with each call. Change prompts via PR, just like code, and run evals before merging.

## Testing & evals
- Unit tests: inject a fake client implementing your `Protocol`; never call the real API in CI's default run.
- Snapshot the request you send (system prompt, tools, params) to catch accidental prompt changes.
- Evals: a small, versioned dataset of real inputs with expected properties (valid schema, correct category, no PII leaked). Score with exact checks first, LLM-as-judge only where needed. Run on every prompt/model change and track scores over time.

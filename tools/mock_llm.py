"""
mock_llm.py — A fake Anthropic / OpenAI API for keyless end-to-end tests.

Point the gateway at it (ANTHROPIC_UPSTREAM / OPENAI_UPSTREAM) and the whole
path runs for free: agent → gateway → "provider" → usage, cost, traces.
The mock behaves like a well-behaved tool-using model:

  turn 1  calls search_docs with the question
  turn 2  calls calculator
  turn 3  answers with the expected value from agent/tasks.json

A question containing "LOOP" makes it call the same tool forever, to
exercise the loop guardrail. A request without tools is taken as the judge
and gets a JSON verdict. Streaming (SSE) is supported for both APIs.

  uvicorn tools.mock_llm:app --port 9000
"""

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

TASKS = json.loads((Path(__file__).resolve().parent.parent / "agent" / "tasks.json").read_text(encoding="utf-8"))
USAGE = {"input": 1200, "output": 80}

app = FastAPI(title="Mock LLM provider")


def _answer(question: str) -> str:
    for task in TASKS:
        if task["question"] == question:
            if "expected_number" in task:
                return f"According to the handbook, the answer is {task['expected_number']}."
            return f"According to the handbook, the answer is {' '.join(task['expected_keywords'])}."
    return "The handbook does not contain that information."


def _verdict(prompt: str) -> str:
    """Judge requests: a plausible verdict, worse when the agent had no answer."""
    unhelpful = "does not contain" in prompt or "(no answer)" in prompt
    return json.dumps({"on_purpose": 5, "grounded": 4, "helpful": 2 if unhelpful else 5,
                       "reasoning": "Mock judge: grades derived from the answer text."})


def _plan(question: str, tool_turns: int) -> tuple[str, dict] | str:
    """Next action: (tool name, args) or the final text."""
    if "LOOP" in question:
        return "search_docs", {"query": "loop"}
    if tool_turns == 0:
        return "search_docs", {"query": question[:80]}
    if tool_turns == 1:
        return "calculator", {"expression": "2 * 3"}
    return _answer(question)


def _sse(events: list[tuple[str | None, dict]]):
    for name, data in events:
        prefix = f"event: {name}\n" if name else ""
        yield f"{prefix}data: {json.dumps(data)}\n\n".encode()


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------
@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    msgs = body.get("messages", [])
    first = msgs[0]["content"] if msgs else ""
    question = first if isinstance(first, str) else " ".join(b.get("text", "") for b in first)
    tool_turns = sum(1 for m in msgs if m["role"] == "assistant")
    action = _plan(question, tool_turns) if body.get("tools") else _verdict(question)
    cached = 1000 if tool_turns else 0
    usage = {"input_tokens": USAGE["input"] - cached + 200 * tool_turns, "output_tokens": USAGE["output"],
             "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0 if tool_turns else 1000}
    if isinstance(action, tuple):
        content = [{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:12]}", "name": action[0], "input": action[1]}]
        stop = "tool_use"
    else:
        content = [{"type": "text", "text": action}]
        stop = "end_turn"
    message = {"id": f"msg_{uuid.uuid4().hex[:12]}", "type": "message", "role": "assistant",
               "model": body.get("model", "mock"), "content": content, "stop_reason": stop,
               "stop_sequence": None, "usage": usage}
    if not body.get("stream"):
        return JSONResponse(message)
    start = {**message, "content": [], "stop_reason": None, "usage": {**usage, "output_tokens": 1}}
    events = [("message_start", {"type": "message_start", "message": start})]
    for i, block in enumerate(content):
        events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": block}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                                     "usage": {"output_tokens": usage["output_tokens"]}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return StreamingResponse(_sse(events), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# OpenAI Chat Completions API
# ---------------------------------------------------------------------------
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    msgs = body.get("messages", [])
    question = next((m["content"] for m in msgs if m["role"] == "user"), "")
    tool_turns = sum(1 for m in msgs if m["role"] == "assistant")
    action = _plan(question, tool_turns) if body.get("tools") else _verdict(question)
    usage = {"prompt_tokens": USAGE["input"] + 200 * tool_turns, "completion_tokens": USAGE["output"],
             "total_tokens": USAGE["input"] + USAGE["output"] + 200 * tool_turns,
             "prompt_tokens_details": {"cached_tokens": 1024 if tool_turns else 0}}
    if isinstance(action, tuple):
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
            "function": {"name": action[0], "arguments": json.dumps(action[1])}}]}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": action}
        finish = "stop"
    completion = {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": 0,
                  "model": body.get("model", "mock"),
                  "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
    if not body.get("stream"):
        return JSONResponse(completion)
    chunk = {"id": completion["id"], "object": "chat.completion.chunk", "created": 0, "model": completion["model"]}
    events = [(None, {**chunk, "choices": [{"index": 0, "delta": message, "finish_reason": finish}]})]
    if (body.get("stream_options") or {}).get("include_usage"):
        events.append((None, {**chunk, "choices": [], "usage": usage}))
    return StreamingResponse((*_sse(events), b"data: [DONE]\n\n"), media_type="text/event-stream")

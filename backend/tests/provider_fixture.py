"""Synthetic native provider server, only imported by tests and local verification."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
requests_seen = []


def events_for(kind, body):
    messages = body.get("input", {}).get("messages", body.get("messages", []))
    serialized = json.dumps(messages, ensure_ascii=False)
    has_result = "tool_result" in serialized or '"role": "tool"' in serialized
    tools = body.get("parameters", {}).get("tools", body.get("tools"))
    tool_name = "write_file" if "GENERATE_TEST_FILE" in serialized else "echo"
    args = json.dumps(
        {"path": "report.txt", "content": "verified project file"}
        if tool_name == "write_file"
        else {"text": "hello"}
    )
    if any(
        t.get("name", t.get("function", {}).get("name")) == "declare_delivery"
        for t in (tools or [])
    ):
        tool_name = "declare_delivery"
        wants_file = "GENERATE_TEST_FILE" in serialized or "MISSING_TEST_FILE" in serialized
        args = json.dumps(
            {
                "kind": "files" if wants_file else "answer",
                "required": ["report.txt"] if wants_file else [],
                "deleted": [],
                "changed": ["report.txt"] if wants_file else [],
            }
        )
        has_result = False
    fragments = [args[:8], args[8:]]
    tool_call = (
        any(t.get("name", t.get("function", {}).get("name")) == tool_name for t in (tools or []))
        and not has_result
    )
    content = (
        "hello"
        if has_result
        else "本地协议测试回答："
        + ("已保留上轮上下文" if any(m["role"] == "assistant" for m in messages) else "你好")
    )
    if kind == "claude":
        yield {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 5, "output_tokens": 0}},
        }
        if tool_call:
            yield {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call1",
                    "name": tool_name,
                    "input": {},
                },
            }
            for fragment in fragments:
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": fragment},
                }
        else:
            yield {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
            for delta in [content[:5], content[5:]]:
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": delta},
                }
        yield {"type": "content_block_stop", "index": 0}
        yield {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if tool_call else "end_turn"},
            "usage": {"output_tokens": 10},
        }
        yield {"type": "message_stop"}
    else:
        parts = (
            [
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call1",
                            "function": {"name": tool_name, "arguments": fragments[0]},
                        }
                    ]
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": fragments[1]}}]},
            ]
            if tool_call
            else [{"content": content[:5]}, {"content": content[5:]}]
        )
        for index, part in enumerate(parts):
            finish = ("tool_calls" if tool_call else "stop") if index == len(parts) - 1 else None
            choice = {
                "finish_reason": finish,
                "index": 0,
                "message" if kind == "qwen_dashscope" else "delta": part,
            }
            yield (
                {"output": {"choices": [choice]}, "usage": {"input_tokens": 5, "output_tokens": 10}}
                if kind == "qwen_dashscope"
                else {"choices": [choice], "usage": {"prompt_tokens": 5, "completion_tokens": 10}}
            )


@app.post("/{kind}/{path:path}")
async def respond(kind: str, path: str, request: Request):
    body = await request.json()
    requests_seen.append((kind, path, body))
    auth = (
        request.headers.get("x-api-key")
        if kind == "claude"
        else request.headers.get("authorization", "").removeprefix("Bearer ")
    )
    if auth != "local-test-key":
        return JSONResponse(status_code=401, content={"error": "invalid test credential"})
    expected = {
        "claude": "messages",
        "deepseek": "chat/completions",
        "qwen_dashscope": "services/aigc/text-generation/generation",
    }
    if expected.get(kind) != path:
        return JSONResponse(status_code=404, content={"error": "wrong native endpoint"})

    async def stream():
        for event in events_for(kind, body):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0.8 if "slow" in json.dumps(body) else 0.15)

    return StreamingResponse(stream(), media_type="text/event-stream")

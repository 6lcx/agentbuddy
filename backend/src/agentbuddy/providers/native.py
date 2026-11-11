"""Native wire adapters; AgentScope owns the Agent loop, not this module."""

import asyncio
import json
import random
from uuid import uuid4

import httpx
from agentscope.credential import DashScopeCredential
from agentscope.formatter import (
    AnthropicChatFormatter,
    DashScopeChatFormatter,
    DeepSeekChatFormatter,
)
from agentscope.message import Msg, TextBlock, ThinkingBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse, ChatUsage
from pydantic import BaseModel

from agentbuddy.providers.context import estimate_tokens


class ProviderFailure(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Parameters(BaseModel):
    max_tokens: int


def encode_messages(messages, claude=False):
    result = []
    for msg in messages:
        content, tools, thinking, tool_results = [], [], "", []
        for block in msg.content:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ThinkingBlock):
                thinking += block.thinking
                signature = getattr(block, "signature", None)
                if claude and signature:
                    content.append(
                        {"type": "thinking", "thinking": block.thinking, "signature": signature}
                    )
            elif isinstance(block, ToolCallBlock):
                args = json.loads(block.input)
                if claude:
                    content.append(
                        {"type": "tool_use", "id": block.id, "name": block.name, "input": args}
                    )
                else:
                    tools.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {"name": block.name, "arguments": block.input},
                        }
                    )
            elif isinstance(block, ToolResultBlock):
                output = (
                    block.output
                    if isinstance(block.output, str)
                    else "".join(b.text for b in block.output if isinstance(b, TextBlock))
                )
                if claude:
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": output}
                    )
                else:
                    tool_results.append(
                        {"role": "tool", "tool_call_id": block.id, "content": output}
                    )
        if content or tools or thinking:
            row = {
                "role": msg.role,
                "content": content if claude else "".join(b["text"] for b in content),
            }
            if tools:
                row["tool_calls"] = tools
            if thinking and not claude:
                row["reasoning_content"] = thinking
            result.append(row)
        if tool_results:
            if claude:
                result.append({"role": "user", "content": tool_results})
            else:
                result.extend(tool_results)
    return result


async def sse_json(response):
    data = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                value = "\n".join(data)
                data = []
                if value == "[DONE]":
                    return
                yield json.loads(value)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        value = "\n".join(data)
        if value != "[DONE]":
            yield json.loads(value)


class NativeModel(ChatModelBase):
    def __init__(self, snapshot, secret, transport=None):
        self.snapshot = snapshot
        model, provider = snapshot["model"], snapshot["provider"]
        self.kind = provider["provider_type"]
        super().__init__(
            credential=DashScopeCredential(api_key=secret),
            model=model["model_id"],
            parameters=Parameters(max_tokens=model["max_output_tokens"]),
            stream=True,
            max_retries=0,
            context_size=model["context_window"],
        )
        self.formatter = {
            "qwen_dashscope": DashScopeChatFormatter,
            "deepseek": DeepSeekChatFormatter,
            "claude": AnthropicChatFormatter,
        }[self.kind]()
        self.client = httpx.AsyncClient(transport=transport, timeout=60, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    def encode(self, messages, tools=None, tool_choice=None):
        provider = self.snapshot["provider"]
        headers = {"Content-Type": "application/json"}
        secret = self.credential.api_key.get_secret_value()
        body = {"model": self.model, "stream": True, "max_tokens": self.parameters.max_tokens}
        formatted = encode_messages(messages, self.kind == "claude")
        url = provider["base_url"].rstrip("/")
        choice = tool_choice.mode if tool_choice else None
        if self.kind == "claude":
            url += "/messages"
            headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
            body["system"] = [b for m in formatted if m["role"] == "system" for b in m["content"]]
            body["messages"] = [m for m in formatted if m["role"] != "system"]
            if tools:
                body["tools"] = [
                    {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "input_schema": t["function"]["parameters"],
                    }
                    for t in tools
                ]
                body["tool_choice"] = (
                    {"type": "auto"}
                    if choice in (None, "auto")
                    else (
                        {"type": "any"}
                        if choice == "required"
                        else {"type": "tool", "name": choice}
                    )
                )
        else:
            headers["Authorization"] = "Bearer " + secret
            params = {"max_tokens": self.parameters.max_tokens}
            if tools:
                params["tools"] = tools
                if choice:
                    params["tool_choice"] = (
                        choice
                        if choice in ("auto", "none", "required")
                        else {"type": "function", "function": {"name": choice}}
                    )
            if self.kind == "qwen_dashscope":
                url += "/services/aigc/text-generation/generation"
                headers["X-DashScope-SSE"] = "enable"
                params.update(
                    result_format="message", incremental_output=True, enable_thinking=False
                )
                body = {"model": self.model, "input": {"messages": formatted}, "parameters": params}
                workspace = provider["config_json"].get("workspace_id")
                if workspace:
                    headers["X-DashScope-WorkSpace"] = workspace
            else:
                url += "/chat/completions"
                body.update(
                    params,
                    messages=formatted,
                    stream_options={"include_usage": True},
                    thinking={"type": "disabled"},
                )
        return url, headers, body

    async def _call_api(self, *args, **kwargs):
        async def retry_stream():
            for attempt in range(3):
                emitted = False
                try:
                    async for event in await self._call_api_once(*args, **kwargs):
                        emitted = True
                        yield event
                    return
                except ProviderFailure as exc:
                    if (
                        not getattr(self, "control", None)
                        or emitted
                        or attempt == 2
                        or exc.code
                        not in {"rate_limit", "connection", "timeout", "provider_transient"}
                    ):
                        raise
                    self.control.check()
                    await asyncio.sleep((2**attempt) * 0.25 + random.random() * 0.1)

        return retry_stream()

    async def _call_api_once(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
        url, headers, body = self.encode(messages, tools, tool_choice)
        if (
            estimate_tokens(json.dumps(body, ensure_ascii=False))
            + self.parameters.max_tokens
            + 2048
            > self.context_size
        ):
            raise ProviderFailure("context_limit", "本轮消息和工具结果超过模型上下文限制")

        async def stream():
            control = getattr(self, "control", None)
            if control:
                control.check(reserve=True)
            text, reasoning, calls, reason, done = "", "", {}, None, False
            text_id, response_id = str(uuid4()), str(uuid4())
            usage = None
            try:
                async with asyncio.timeout(min(180, control.check() if control else 180)):
                    async with self.client.stream(
                        "POST", url, headers=headers, json=body
                    ) as response:
                        if response.status_code >= 400:
                            code = {
                                401: "authentication",
                                403: "authentication",
                                429: "rate_limit",
                            }.get(
                                response.status_code,
                                "provider_transient"
                                if response.status_code >= 500
                                else "provider_http",
                            )
                            raise ProviderFailure(
                                code, f"模型服务请求失败（HTTP {response.status_code}）"
                            )
                        async for obj in sse_json(response):
                            delta = ""
                            if obj.get("error") or obj.get("code"):
                                raise ProviderFailure(
                                    "provider_error", "模型服务返回错误，请检查配置"
                                )
                            if self.kind == "claude":
                                kind = obj.get("type")
                                if kind == "message_start":
                                    usage = obj.get("message", {}).get("usage")
                                if kind == "content_block_start":
                                    block = obj["content_block"]
                                    if block["type"] == "tool_use":
                                        calls[obj["index"]] = {
                                            "id": block["id"],
                                            "name": block["name"],
                                            "args": "",
                                        }
                                    elif block["type"] == "text":
                                        delta = block.get("text", "")
                                elif kind == "content_block_delta":
                                    part = obj["delta"]
                                    delta = part.get("text", "")
                                    if "partial_json" in part:
                                        calls[obj["index"]]["args"] += part["partial_json"]
                                    reasoning += part.get("thinking", "")
                                elif kind == "message_delta":
                                    reason = obj.get("delta", {}).get("stop_reason")
                                    usage = {**(usage or {}), **obj.get("usage", {})}
                                elif kind == "message_stop":
                                    done = True
                            else:
                                output = (
                                    obj.get("output", {}) if self.kind == "qwen_dashscope" else obj
                                )
                                usage = obj.get("usage") or usage
                                for choice_obj in output.get("choices", []):
                                    part = (
                                        choice_obj.get("message", {})
                                        if self.kind == "qwen_dashscope"
                                        else choice_obj.get("delta", {})
                                    )
                                    delta += part.get("content") or ""
                                    reasoning += part.get("reasoning_content") or ""
                                    for call in part.get("tool_calls") or []:
                                        target = calls.setdefault(
                                            call.get("index", 0), {"id": "", "name": "", "args": ""}
                                        )
                                        if call.get("id"):
                                            target["id"] = call["id"]
                                        function = call.get("function", {})
                                        if function.get("name"):
                                            target["name"] = function["name"]
                                        target["args"] += function.get("arguments", "")
                                    if choice_obj.get("finish_reason") not in (None, "null"):
                                        reason = choice_obj["finish_reason"]
                                        done = True
                            if reason in ("length", "max_tokens", "content_filter"):
                                raise ProviderFailure(
                                    "truncated", "模型输出被截断，未作为完整回答保存"
                                )
                            text += delta
                            if (
                                len(text)
                                + len(reasoning)
                                + sum(len(c["args"]) for c in calls.values())
                                > 2_000_000
                            ):
                                raise ProviderFailure("response_limit", "模型响应超过限制")
                            if delta:
                                yield ChatResponse(
                                    id=response_id,
                                    content=[TextBlock(id=text_id, text=delta)],
                                    is_last=False,
                                )
                    if not done or reason not in (
                        "stop",
                        "tool_calls",
                        "end_turn",
                        "tool_use",
                        "stop_sequence",
                    ):
                        raise ProviderFailure(
                            "incomplete_stream", "模型连接提前结束，未收到完成标识"
                        )
                    blocks = [TextBlock(id=text_id, text=text)] if text else []
                    if reasoning and self.kind != "claude":
                        blocks.insert(0, ThinkingBlock(thinking=reasoning))
                    if calls and not tools:
                        raise ProviderFailure(
                            "unexpected_tool", "当前任务未启用工具，模型返回了工具请求"
                        )
                    for call in calls.values():
                        if (
                            not call["id"]
                            or not call["name"]
                            or not isinstance(json.loads(call["args"]), dict)
                        ):
                            raise ValueError("Invalid tool arguments")
                        blocks.append(
                            ToolCallBlock(id=call["id"], name=call["name"], input=call["args"])
                        )
                    if not blocks:
                        raise ProviderFailure("empty_response", "模型没有返回可用内容")
                    metrics = (
                        None
                        if usage is None
                        else ChatUsage(
                            input_tokens=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                            output_tokens=usage.get(
                                "output_tokens", usage.get("completion_tokens", 0)
                            ),
                            time=0,
                        )
                    )
                    yield ChatResponse(id=response_id, content=blocks, is_last=True, usage=metrics)
            except ProviderFailure:
                raise
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise ProviderFailure("timeout", "模型服务请求超时") from exc
            except httpx.HTTPError as exc:
                raise ProviderFailure("connection", "无法连接模型服务") from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderFailure("invalid_response", "模型响应格式或工具参数不完整") from exc

        return stream()


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Return supplied text",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}


async def probe(model):
    from agentscope.tool import ToolChoice

    conversation = [Msg(name="user", role="user", content=[TextBlock(text="Reply with OK.")])]
    result = None
    async for result in await model(conversation):
        pass
    if not result or not any(isinstance(b, TextBlock) and b.text for b in result.content):
        raise ProviderFailure("text_probe_failed", "模型文本测试失败")
    conversation = [
        Msg(name="user", role="user", content=[TextBlock(text="Call echo with text hello.")])
    ]
    async for result in await model(
        conversation, tools=[PROBE_TOOL], tool_choice=ToolChoice(mode="echo")
    ):
        pass
    calls = [b for b in result.content if isinstance(b, ToolCallBlock)]
    if (
        len(calls) != 1
        or calls[0].name != "echo"
        or json.loads(calls[0].input) != {"text": "hello"}
    ):
        raise ProviderFailure("tool_probe_failed", "模型未完成预期工具请求")
    conversation.extend(
        [
            Msg(name="assistant", role="assistant", content=result.content),
            Msg(
                name="tool",
                role="assistant",
                content=[ToolResultBlock(id=calls[0].id, name="echo", output="hello")],
            ),
        ]
    )
    async for result in await model(conversation, tools=[PROBE_TOOL]):
        pass
    if any(isinstance(b, ToolCallBlock) for b in result.content) or not any(
        isinstance(b, TextBlock) and b.text for b in result.content
    ):
        raise ProviderFailure("tool_result_probe_failed", "模型未完成工具结果回传")
    return {"text": True, "tools": True, "streaming": True, "vision": False}

import json

import httpx
import pytest
from agentscope.message import Msg, TextBlock, ThinkingBlock, ToolCallBlock, ToolResultBlock
from agentscope.tool import ToolChoice
from provider_fixture import events_for

from agentbuddy.providers.native import (
    PROBE_TOOL,
    NativeModel,
    ProviderFailure,
    encode_messages,
    probe,
)


def snapshot(kind):
    return {
        "model": {"model_id": "test-model", "context_window": 32768, "max_output_tokens": 1000},
        "provider": {
            "provider_type": kind,
            "base_url": "http://local.test",
            "config_json": {"workspace_id": "test"},
        },
    }


def fixture_transport(kind, captures):
    def respond(request):
        body = json.loads(request.content)
        captures.append((request, body))
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events_for(kind, body))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=data)

    return httpx.MockTransport(respond)


@pytest.mark.parametrize("kind", ["qwen_dashscope", "deepseek", "claude"])
async def test_three_native_protocols_and_tool_roundtrip(kind):
    captures = []
    model = NativeModel(snapshot(kind), "local-test-key", fixture_transport(kind, captures))
    try:
        assert (await probe(model)) == {
            "text": True,
            "tools": True,
            "streaming": True,
            "vision": False,
        }
        assert len(captures) == 3
        request, body = captures[0]
        if kind == "qwen_dashscope":
            assert request.url.path.endswith("/text-generation/generation")
            assert "input" in body and body["parameters"]["incremental_output"]
            assert request.headers["X-DashScope-WorkSpace"] == "test"
        elif kind == "claude":
            assert request.url.path == "/messages"
            assert request.headers["x-api-key"] == "local-test-key"
            assert "tool_result" in json.dumps(captures[-1][1])
        else:
            assert request.url.path == "/chat/completions"
            assert captures[0][1]["stream_options"]["include_usage"]
            assert "tool_call_id" in json.dumps(captures[-1][1])
    finally:
        await model.close()


@pytest.mark.parametrize("kind", ["qwen_dashscope", "deepseek", "claude"])
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_errors_do_not_retry_or_expose_response(kind, status):
    calls = []

    def fail(request):
        calls.append(1)
        return httpx.Response(status, text="sensitive-provider-body local-test-key")

    model = NativeModel(snapshot(kind), "local-test-key", httpx.MockTransport(fail))
    try:
        with pytest.raises(ProviderFailure) as error:
            async for _ in await model(
                [Msg(name="u", role="user", content=[TextBlock(text="hi")])]
            ):
                pass
        assert "local-test-key" not in str(error.value)
        assert len(calls) == 1
    finally:
        await model.close()


@pytest.mark.parametrize("case", ["truncated", "eof", "malformed_tool", "empty"])
async def test_incomplete_output_is_never_success(case):
    if case == "truncated":
        events = [{"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}]
    elif case == "eof":
        events = [{"choices": [{"delta": {"content": "partial"}}]}]
    elif case == "empty":
        events = [{"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    else:
        events = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"id": "c", "function": {"name": "echo", "arguments": "{"}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
        )
    )
    model = NativeModel(snapshot("deepseek"), "local-test-key", transport)
    try:
        with pytest.raises(ProviderFailure):
            async for _ in await model(
                [Msg(name="u", role="user", content=[TextBlock(text="hi")])],
                tools=[PROBE_TOOL],
                tool_choice=ToolChoice(mode="echo"),
            ):
                pass
    finally:
        await model.close()


def test_deepseek_reasoning_and_claude_tool_result_encoding():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                ThinkingBlock(thinking="private"),
                ToolCallBlock(id="a", name="echo", input='{"text":"hello"}'),
            ],
        ),
        Msg(
            name="tool",
            role="assistant",
            content=[ToolResultBlock(id="a", name="echo", output="hello")],
        ),
    ]
    encoded = encode_messages(msgs)
    assert encoded[0]["reasoning_content"] == "private"
    assert encoded[1]["tool_call_id"] == "a"
    claude = encode_messages(msgs, True)
    assert claude[0]["content"][0]["type"] == "tool_use"
    assert claude[1]["content"][0]["tool_use_id"] == "a"


@pytest.mark.parametrize("kind", ["qwen_dashscope", "deepseek", "claude"])
async def test_native_timeout_and_malformed_json(kind):
    for failure in ["timeout", "json"]:

        def handler(request):
            if failure == "timeout":
                raise httpx.ReadTimeout("private details", request=request)
            return httpx.Response(200, text="data: invalid-json\n\n")

        model = NativeModel(snapshot(kind), "local-test-key", httpx.MockTransport(handler))
        try:
            with pytest.raises(ProviderFailure) as error:
                async for _ in await model(
                    [Msg(name="user", role="user", content=[TextBlock(text="hi")])]
                ):
                    pass
            assert error.value.code == ("timeout" if failure == "timeout" else "invalid_response")
            assert "private details" not in str(error.value)
        finally:
            await model.close()


@pytest.mark.parametrize("claude", [False, True])
def test_combined_agentscope_tool_message_preserves_wire_order(claude):
    message = Msg(
        name="assistant",
        role="assistant",
        content=[
            ToolCallBlock(id="call", name="write_file", input='{"path":"a.txt","content":"x"}'),
            ToolResultBlock(id="call", name="write_file", output="written"),
        ],
    )
    encoded = encode_messages([message], claude)
    assert len(encoded) == 2
    assert encoded[0]["role"] == "assistant"
    if claude:
        assert encoded[0]["content"][0]["type"] == "tool_use"
        assert encoded[1]["role"] == "user"
        assert encoded[1]["content"][0]["type"] == "tool_result"
    else:
        assert encoded[0]["tool_calls"][0]["id"] == "call"
        assert encoded[1]["role"] == "tool"

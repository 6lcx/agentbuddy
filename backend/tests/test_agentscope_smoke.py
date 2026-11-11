"""Offline framework smoke; does not verify any provider service."""

import asyncio
from importlib.metadata import version

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatResponse, DashScopeChatModel


class OfflineModel(DashScopeChatModel):
    def __init__(self):
        super().__init__(
            credential=DashScopeCredential(api_key="offline-test-not-a-real-key"),
            model="offline-test",
            stream=False,
            max_retries=0,
        )
        self.calls = 0

    async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
        self.calls += 1
        return ChatResponse(content=[TextBlock(text="offline-ok")], is_last=True)


async def test_agentscope_reply_without_network():
    assert version("agentscope").startswith("2.0.")
    model = OfflineModel()
    agent = Agent(name="smoke", system_prompt="Reply briefly.", model=model)
    result = await asyncio.wait_for(
        agent.reply(Msg(name="user", role="user", content=[TextBlock(text="hello")])),
        timeout=15,
    )
    assert result.role == "assistant"
    assert any(
        isinstance(block, TextBlock) and block.text == "offline-ok" for block in result.content
    )
    assert model.calls == 1

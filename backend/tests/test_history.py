from types import SimpleNamespace

import pytest
from agentscope.message import TextBlock

from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.history import compact_history


class Model:
    context_size = 10000

    def __init__(self):
        self.calls = []

    async def __call__(self, messages):
        self.calls.append(messages)

        async def stream():
            yield SimpleNamespace(content=[TextBlock(text="约束：预算100元；来源 old")])

        return stream()


class Control:
    def event(self, *args):
        self.last = args


async def test_short_history_unchanged():
    model = Model()
    history = [{"run_id": "new", "role": "user", "content": "hello"}]
    assert await compact_history(history, model, 1000, Control()) == history
    assert not model.calls


async def test_compress_complete_turn_keep_current():
    model = Model()
    history = [
        {"run_id": "old", "role": "user", "content": "约束" * 550},
        {"run_id": "old", "role": "assistant", "content": "回复" * 550},
        {"run_id": "new", "role": "user", "content": "继续" * 300},
    ]
    result = await compact_history(history, model, 1000, Control())
    assert result[-1] == history[-1]
    assert "预算100元" in result[0]["content"]
    assert len(model.calls) == 1


async def test_oversized_current_does_not_drop_request():
    model = Model()
    with pytest.raises(ProviderFailure):
        await compact_history(
            [{"run_id": "new", "role": "user", "content": "长" * 4000}],
            model,
            1000,
            Control(),
        )
    assert not model.calls


async def test_compaction_does_not_hide_budget_failure():
    class Exhausted(Model):
        async def __call__(self, messages):
            raise ProviderFailure("budget_exhausted", "No calls remain")

    with pytest.raises(ProviderFailure, match="No calls remain"):
        await compact_history(
            [
                {"run_id": "old", "role": "user", "content": "约束" * 550},
                {"run_id": "old", "role": "assistant", "content": "回复" * 550},
                {"run_id": "new", "role": "user", "content": "继续" * 300},
            ],
            Exhausted(),
            1000,
            Control(),
        )

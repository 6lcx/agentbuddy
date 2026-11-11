"""Small real-service context comparison; no assertion of universal cost savings."""

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from agentbuddy.catalog.service import Catalog
from agentbuddy.config import Settings
from agentbuddy.providers.native import NativeModel
from agentbuddy.runtime.context_runtime import ContextRuntime
from agentbuddy.runtime.runtime_store import RuntimeStore
from agentscope.message import Msg, TextBlock
from dotenv import dotenv_values
from verify_deepseek import configure


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agentbuddy-context-eval-") as temporary:
        db, row = await configure(
            Settings(data_dir=Path(temporary)),
            dotenv_values(args.source_env),
            max_output_tokens=1024,
        )
        catalog = Catalog(db)
        snapshot = catalog.test_payload(row["id"])
        model = NativeModel(
            snapshot, catalog.secret(snapshot["provider"]["credential_id"])
        )
        events = []
        tools = SimpleNamespace(
            store=RuntimeStore(db, "evaluation", "run"),
            control=SimpleNamespace(event=lambda *args: events.append(args)),
        )
        runtime = ContextRuntime(tools, {})
        try:
            noise = "Archive note: unrelated historical routine completed.\n"
            messages = [
                Msg(
                    name="user",
                    role="user",
                    content=[
                        TextBlock(
                            text="Confirmed code ALPHA42; budget 100.\n" + noise * 200
                        )
                    ],
                ),
                Msg(
                    name="assistant",
                    role="assistant",
                    content=[TextBlock(text="Recorded.")],
                ),
                Msg(
                    name="user",
                    role="user",
                    content=[
                        TextBlock(
                            text="Return only the confirmed code and budget. Do not invent values."
                        )
                    ],
                ),
            ]
            while runtime.cost(model, messages, [], None) < 23500:
                messages[0].content[0].text += noise * 20

            async def answer(content):
                final = None
                async for final in await model(content):
                    pass
                text = "".join(
                    b.text for b in final.content if isinstance(b, TextBlock)
                )
                return {
                    "correct": "ALPHA42" in text and "100" in text,
                    "usage": {
                        "input_tokens": final.usage.input_tokens,
                        "output_tokens": final.usage.output_tokens,
                    }
                    if final.usage
                    else None,
                }

            baseline = await answer(messages)
            prepared = await runtime.prepare(model, messages, [], None)
            compacted = await answer(prepared)
            again = await runtime.prepare(model, messages, [], None)
            report = {
                "case": "two explicit facts in repetitive history",
                "baseline": baseline,
                "compacted": compacted,
                "estimated_before": runtime.cost(model, messages, [], None),
                "estimated_after": runtime.cost(model, prepared, [], None),
                "summary_calls": sum(kind == "context.compacted" for kind, _ in events),
                "reused_summary": runtime.cost(model, again, [], None)
                == runtime.cost(model, prepared, [], None),
                "limits": "One synthetic factual case on real DeepSeek. Summary generation is additional cost; no total-cost or cache-hit benefit claimed.",
            }
            print(json.dumps(report, ensure_ascii=False))
            assert (
                baseline["correct"]
                and compacted["correct"]
                and report["summary_calls"] == 1
            )
        finally:
            await model.close()
            db.close()


if __name__ == "__main__":
    asyncio.run(main())

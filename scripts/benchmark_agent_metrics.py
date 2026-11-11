"""Isolated real-provider baseline; no credentials are exported in results."""

import argparse
import asyncio
import contextvars
import csv
import io
import json
import math
import time
import zipfile
from pathlib import Path

from agentbuddy.config import Settings
from agentbuddy.persistence.schema import run_events
from agentbuddy.providers import native
from agentbuddy.runtime.executor import Executor
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker import service
from dotenv import dotenv_values
from sqlalchemy import select
from verify_deepseek import configure

ACTIVE = contextvars.ContextVar("metric_attempt", default=None)
CALLS = []
original_sse = native.sse_json


async def measured_sse(response):
    async for obj in original_sse(response):
        row = ACTIVE.get()
        if row is not None:
            if obj.get("usage"):
                row["usage"] = obj["usage"]
            for choice in obj.get("choices", []):
                delta = choice.get("delta") or {}
                if any(
                    delta.get(k) for k in ("content", "reasoning_content", "tool_calls")
                ):
                    row.setdefault(
                        "first_output_s", time.perf_counter() - row["_start"]
                    )
                if delta.get("content"):
                    row.setdefault("first_text_s", time.perf_counter() - row["_start"])
        yield obj


class MeasuredModel(native.NativeModel):
    async def _call_api_once(self, *args, **kwargs):
        started = time.perf_counter()
        row = {"_start": started, "status": "failed"}
        CALLS.append(row)
        try:
            stream = await super()._call_api_once(*args, **kwargs)
        except Exception:
            row["duration_s"] = time.perf_counter() - started
            raise

        async def consume():
            token = ACTIVE.set(row)
            try:
                async for item in stream:
                    yield item
                row["status"] = "succeeded"
            finally:
                row["duration_s"] = time.perf_counter() - started
                ACTIVE.reset(token)

        return consume()


CASES = [
    ("arithmetic", "只输出计算结果的整数，不调用工具：17乘23等于多少？"),
    (
        "extraction",
        "从资料提取信息，只输出JSON对象，不创建文件：星河项目由李明负责，截止日期2026-12-01。字段project,owner,due，值分别为星河、李明、2026-12-01。",
    ),
    (
        "json_file",
        "使用真实文件工具生成 config.json，内容为JSON对象：name为demo，enabled为true，limits为对象且calls=120、seconds=1800。读取核对后交付文件。",
    ),
    (
        "csv_file",
        "生成 summary.csv，列为region,revenue。源数据：east有2件每件10元及3件每件5元，west有4件每件8元。按地区汇总收入，每地区一行，不添加总计行。再生成 total.txt，仅写两个地区总收入的整数。读取核对后交付。",
    ),
]


def validate(case, run, files):
    if run["status"] != "succeeded":
        return False
    text = run.get("partial_text", "").strip()
    if case == "arithmetic":
        return text == "391"
    if case == "extraction":
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        return json.loads(text) == {
            "project": "星河",
            "owner": "李明",
            "due": "2026-12-01",
        }
    if case == "json_file":
        return json.loads(files["config.json"]) == {
            "name": "demo",
            "enabled": True,
            "limits": {"calls": 120, "seconds": 1800},
        }
    rows = list(csv.DictReader(io.StringIO(files["summary.csv"].decode("utf-8-sig"))))
    return (
        len(rows) == 2
        and {r["region"]: float(r["revenue"]) for r in rows} == {"east": 35, "west": 32}
        and files["total.txt"].decode().strip() == "67"
    )


def dist(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        **{f"p{p}": values[math.ceil(p / 100 * len(values)) - 1] for p in (50, 95, 99)},
        "max": max(values),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_dir=args.output.resolve() / "private-data")
    values = dotenv_values(args.source_env)
    db, model = await configure(
        settings, values, max_output_tokens=2048, context_window=65536
    )
    native.sse_json = measured_sse
    service.Executor = lambda database: Executor(database, model_factory=MeasuredModel)
    repo = RunRepository(db)
    results = []
    try:
        for repeat in range(args.repeats):
            for case, prompt in CASES:
                CALLS.clear()
                start = time.perf_counter()
                submitted = repo.submit(
                    {
                        "message": prompt,
                        "model_id": model["id"],
                        "calls_limit": 30,
                        "seconds_limit": 180,
                    },
                    f"{case}-{repeat}",
                )
                await service.Worker(settings).run(once=True)
                duration = time.perf_counter() - start
                run = repo.get(submitted["run_id"])
                with db.engine.connect() as conn:
                    events = [
                        dict(r)
                        for r in conn.execute(
                            select(run_events)
                            .where(run_events.c.run_id == submitted["run_id"])
                            .order_by(run_events.c.id)
                        ).mappings()
                    ]
                versions = Files(db).list(submitted["project_id"])
                files = {}
                if versions:
                    archive = Files(db).archive(versions[-1]["id"])
                    with zipfile.ZipFile(io.BytesIO(archive)) as z:
                        files = {n: z.read(n) for n in z.namelist()}
                error = None
                try:
                    passed = validate(case, run, files)
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    passed, error = False, type(exc).__name__
                attempts = [
                    {k: v for k, v in c.items() if k != "_start"} for c in CALLS
                ]
                row = {
                    "case": case,
                    "repeat": repeat + 1,
                    "run_id": submitted["run_id"],
                    "status": run["status"],
                    "passed": passed,
                    "validation_error": error,
                    "harness_wall_s": duration,
                    "calls": attempts,
                    "events": events,
                }
                results.append(row)
                (args.output / "runs.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "case": case,
                            "repeat": repeat + 1,
                            "passed": passed,
                            "status": run["status"],
                            "seconds": round(duration, 2),
                            "calls": len(attempts),
                        }
                    ),
                    flush=True,
                )
    finally:
        db.close()
    calls = [c for r in results for c in r["calls"]]
    usages = [c["usage"] for c in calls if c.get("usage")]
    cache = [u for u in usages if "prompt_cache_hit_tokens" in u]
    summary = {
        "model": values["LLM_MODEL"],
        "tasks": len(results),
        "passed": sum(r["passed"] for r in results),
        "completed": sum(r["status"] == "succeeded" for r in results),
        "calls": len(calls),
        "calls_with_usage": len(usages),
        "input_tokens": sum(u.get("prompt_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("completion_tokens", 0) for u in usages),
        "cache_observed_calls": len(cache),
        "cache_hit_calls": sum(u["prompt_cache_hit_tokens"] > 0 for u in cache),
        "cache_read_tokens": sum(u["prompt_cache_hit_tokens"] for u in cache),
        "cache_observed_input_tokens": sum(u["prompt_tokens"] for u in cache),
        "model_latency_s": dist([c["duration_s"] for c in calls]),
        "first_output_s": dist(
            [c["first_output_s"] for c in calls if "first_output_s" in c]
        ),
        "first_text_s": dist([c["first_text_s"] for c in calls if "first_text_s" in c]),
        "task_harness_wall_s": dist([r["harness_wall_s"] for r in results]),
        "by_case": {
            case: {
                "passed": sum(r["passed"] for r in results if r["case"] == case),
                "latency_s": dist(
                    [r["harness_wall_s"] for r in results if r["case"] == case]
                ),
            }
            for case, _ in CASES
        },
        "cost": None,
        "percentile_method": "nearest-rank; small sample descriptive only",
        "scope": "serial isolated Worker runs; excludes initial connection probe; includes Worker startup/shutdown, no HTTP/browser latency",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

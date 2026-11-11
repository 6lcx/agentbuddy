"""DeepSeek acceptance for Spec B/D: parallel text project and consistent budget edit."""

import argparse
import asyncio
import csv
import json
from pathlib import Path
from uuid import uuid4

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.schema import subtasks
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker
from sqlalchemy import select


def check(root, total):
    with (root / "data/channel-plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert sum(float(r["budget"]) for r in rows) == total
    assert all(r["product"] == "星河笔记" and r["date"] == "2026-11-10" for r in rows)
    for name in ("docs/brief.md", "copy/announcement.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "星河笔记" in text and "2026-11-10" in text and str(total) in text


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)
    db = Database(settings.database_path)
    try:
        repo = RunRepository(db)
        files = Files(db)
        first = repo.submit(
            {
                "message": "合成资料：产品星河笔记，日期2026-11-10，总预算20；video渠道12，community渠道8。请自动协作并行生成三个独立文件：docs/brief.md、data/channel-plan.csv、copy/announcement.md，各交给一个专家任务。所有文件必须包含同一产品名、日期和预算口径。CSV严格使用product,date,channel,budget列，两个渠道各一行，不要总计行。Markdown明确总预算20。不要扩写其他数字或生成Office。",
                "selection": {"mode": "team"},
                "calls_limit": 120,
                "seconds_limit": 300,
            },
            str(uuid4()),
        )
        await Worker(settings).run(once=True)
        assert repo.get(first["run_id"])["status"] == "succeeded"
        versions = [
            v for v in files.list(first["project_id"]) if v["kind"] == "version"
        ]
        check(settings.data_dir / "versions" / versions[-1]["id"], 20)
        original = files.archive(versions[-1]["id"])
        with db.engine.connect() as conn:
            tasks = [
                dict(r)
                for r in conn.execute(
                    select(subtasks).where(
                        subtasks.c.run_id == first["run_id"],
                        subtasks.c.status == "succeeded",
                    )
                ).mappings()
            ]
        assert len(tasks) >= 3
        assert any(
            a["started_at"] < b["finished_at"] and b["started_at"] < a["finished_at"]
            for a in tasks
            for b in tasks
            if a["id"] != b["id"]
        )
        second = repo.submit(
            {
                "project_id": first["project_id"],
                "message": "预算更新：总预算30，video渠道18、community渠道12。读取并修改 docs/brief.md、data/channel-plan.csv、copy/announcement.md，让三个文件预算同步为30；CSV列保持product,date,channel,budget且只保留两个渠道行。产品名星河笔记和日期2026-11-10不变，其他内容保留。",
                "selection": {"mode": "general"},
                "calls_limit": 35,
                "seconds_limit": 240,
            },
            str(uuid4()),
        )
        await Worker(settings).run(once=True)
        assert repo.get(second["run_id"])["status"] == "succeeded"
        current = [v for v in files.list(first["project_id"]) if v["kind"] == "version"]
        assert len(current) == 2 and original == files.archive(versions[-1]["id"])
        check(settings.data_dir / "versions" / current[-1]["id"], 30)
        result = {
            "scenario_B": "passed",
            "scenario_D": "passed",
            "parallel_tasks": len(tasks),
            "budget_v1": 20,
            "budget_v2": 30,
            "old_version_unchanged": True,
            "run_ids": [first["run_id"], second["run_id"]],
        }
        (settings.data_dir / "business-evidence.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Authorized DeepSeek + actual one-level delegation and Office delivery check."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.config import Settings
from agentbuddy.persistence.schema import subtasks
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker
from dotenv import dotenv_values
from sqlalchemy import select
from verify_deepseek import configure


def verify_contents(root, modified=False):
    from agentbuddy.runtime.office import extract
    from openpyxl import load_workbook
    from pptx import Presentation

    text = "\n".join(extract(root / "report.docx")["paragraphs"])
    assert "20" in text
    if modified:
        assert "后续计划" in text and "下一步验收" in text
    book = load_workbook(root / "budget.xlsx", data_only=True)
    try:
        assert any(cell.value == 20 for sheet in book for row in sheet for cell in row)
    finally:
        book.close()
    assert len(Presentation(root / "slides.pptx").slides) >= 2


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    args = parser.parse_args()
    folder = Path("data/validation") / ("t11-t15-" + str(uuid4()))
    settings = Settings(data_dir=folder.resolve())
    db, model = await configure(
        settings, dotenv_values(args.source_env), max_output_tokens=4096
    )
    try:
        Capabilities(db).seed_presets()
        repo = RunRepository(db)
        result = repo.submit(
            {
                "message": "请自动协作：并行交给文档专家、表格专家、演示专家各一个任务。文档专家生成 report.docx，标题项目报告，内容预算开发12测试8合计20；表格专家生成 budget.xlsx，项目和金额两列，开发12测试8，总计使用SUM公式并期望20；演示专家生成 slides.pptx，至少两页中文，介绍项目和预算。各专家必须加载自己对应Skill并用generate_office创建真实文件。三项任务没有依赖，输出不同路径。最后汇总交付这三个文件。",
                "model_id": model["id"],
                "selection": {"mode": "team"},
                "calls_limit": 120,
                "seconds_limit": 600,
            },
            "team-office",
        )
        await Worker(settings).run(once=True)
        run = repo.get(result["run_id"])
        with db.engine.connect() as conn:
            tasks = [
                dict(r)
                for r in conn.execute(
                    select(subtasks).where(subtasks.c.run_id == result["run_id"])
                ).mappings()
            ]
        if run["status"] != "succeeded":
            print(
                json.dumps(
                    {
                        "status": run["status"],
                        "failure": run["failure_json"],
                        "tasks": tasks,
                    },
                    ensure_ascii=False,
                )
            )
            raise RuntimeError("Real team run failed")
        versions = [
            a for a in Files(db).list(result["project_id"]) if a["kind"] == "version"
        ]
        assert versions and len(tasks) >= 3
        names = {f["path"] for f in versions[-1]["manifest_json"]}
        assert {"report.docx", "budget.xlsx", "slides.pptx"} <= names
        overlap = any(
            a["started_at"] < b["finished_at"] and b["started_at"] < a["finished_at"]
            for a in tasks
            for b in tasks
            if a["id"] != b["id"]
        )
        assert overlap
        verify_contents(folder / "versions" / versions[-1]["id"])
        baseline = Files(db).archive(versions[-1]["id"])
        second = repo.submit(
            {
                "project_id": result["project_id"],
                "message": "请修改report.docx：读取它的agentbuddy.json后用edit_office保持原内容并增加标题为后续计划的章节，写下一步验收。先加载Word Skill。其他文件保持不变。",
                "selection": {"mode": "general"},
                "calls_limit": 30,
                "seconds_limit": 240,
            },
            "office-v2",
        )
        await Worker(settings).run(once=True)
        assert repo.get(second["run_id"])["status"] == "succeeded"
        assert baseline == Files(db).archive(versions[-1]["id"])
        final_versions = [
            a for a in Files(db).list(result["project_id"]) if a["kind"] == "version"
        ]
        verify_contents(folder / "versions" / final_versions[-1]["id"], modified=True)
        report = {
            "provider": "deepseek",
            "model": model["model_id"],
            "run_id": result["run_id"],
            "tasks": len(tasks),
            "parallel_overlap": overlap,
            "office_files": sorted(names),
            "versions": len(
                [
                    a
                    for a in Files(db).list(result["project_id"])
                    if a["kind"] == "version"
                ]
            ),
            "old_version_unchanged": True,
        }
        (folder / "evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"evidence": str(folder / "evidence.json"), **report},
                ensure_ascii=False,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

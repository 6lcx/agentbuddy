"""Spec A: shared Skill, expert-specific execution Skill, and real local MCP with DeepSeek."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.tools import discover
from agentbuddy.worker.service import Worker


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)
    db = Database(settings.database_path)
    try:
        catalog = Capabilities(db)
        skills = {
            s["config_json"].get("preset_key"): s["id"] for s in catalog.list("skill")
        }
        config = {
            "name": "T16 本地查询 MCP",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(Path("backend/tests/mcp_fixture.py").resolve())],
            "enabled": True,
            "effects": {"echo": "read"},
        }
        config["tools"] = await discover(config)
        connector = catalog.save("connector", config)
        shared = skills["project-files"]
        execution = skills["development"]
        research = catalog.save(
            "expert",
            {
                "name": "T16 研究专家",
                "enabled": True,
                "prompt": "使用真实工具处理任务，按明确输入生成文件，不虚构完成状态。",
                "skill_ids": [shared, execution],
                "connector_ids": [connector["id"]],
                "collaborator_ids": [],
            },
        )
        writer = catalog.save(
            "expert",
            {
                "name": "T16 文案专家",
                "enabled": True,
                "skill_ids": [shared],
                "connector_ids": [],
                "collaborator_ids": [],
            },
        )
        assert shared in [
            s["id"] for s in catalog.resolve({"expert_id": writer["id"]})["skills"]
        ]
        repo = RunRepository(db)
        result = repo.submit(
            {
                "message": f'验收任务：先加载项目文件整理 Skill {shared} 和开发与验证 Skill {execution}。调用连接器 {connector["id"]} 的 echo 工具，参数 text 精确为 MCP_SOURCE_16。用 write_file 写 create_table.py，脚本使用 pathlib 写 summary.csv，内容为 name,value 换行 MCP_SOURCE_16,16 换行。然后必须调用 run_script，skill_id={execution}，script=scripts/run_python.py，args=["create_table.py"]，实际执行生成CSV。读取CSV确认后简短汇报。',
                "selection": {"mode": "expert", "expert_id": research["id"]},
                "calls_limit": 40,
                "seconds_limit": 180,
            },
            str(uuid4()),
        )
        await Worker(settings).run(once=True)
        run = repo.get(result["run_id"])
        if run["status"] != "succeeded":
            raise RuntimeError(str(run["failure_json"]))
        version = [
            a for a in Files(db).list(result["project_id"]) if a["kind"] == "version"
        ][-1]
        csv = (
            settings.data_dir / "versions" / version["id"] / "summary.csv"
        ).read_text(encoding="utf-8")
        assert "MCP_SOURCE_16,16" in csv
        events = repo.events(result["run_id"])
        names = [
            e["payload_json"].get("name")
            for e in events
            if e["type"] == "tool.completed"
        ]
        assert (
            names.count("load_skill") >= 2
            and "run_script" in names
            and "call_connector" in names
        )
        evidence = {
            "scenario_A": "passed",
            "run_id": result["run_id"],
            "shared_skill": True,
            "actual_script": True,
            "actual_local_mcp": True,
            "model": "deepseek",
        }
        (settings.data_dir / "capability-evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        print(json.dumps(evidence))
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

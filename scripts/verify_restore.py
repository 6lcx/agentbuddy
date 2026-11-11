"""Restore an offline accepted dataset and verify a real DeepSeek continuation."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from agentbuddy.backup import backup, restore
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-data", type=Path, required=True)
    args = parser.parse_args()
    root = Path("data/validation") / ("t16-recovery-" + str(uuid4()))
    root.mkdir(parents=True)
    before = backup(args.source_data, root / "backup.zip")
    after = restore(root / "backup.zip", root / "restored")
    assert before == after
    original = Database(args.source_data / "agentbuddy.sqlite3")
    settings = Settings(data_dir=(root / "restored").resolve())
    db = Database(settings.database_path)
    try:
        repo = RunRepository(db)
        project = repo.project_list()[0]["id"]
        versions = [a for a in Files(db).list(project) if a["kind"] == "version"]
        for asset in versions:
            assert Files(db).archive(asset["id"]) == Files(original).archive(
                asset["id"]
            )
        result = repo.submit(
            {
                "project_id": project,
                "message": "请调用 write_file 新增 recovery.txt，内容精确为 restored DeepSeek verified。不要修改任何其他文件。简短回复即可。",
                "selection": {"mode": "general"},
                "calls_limit": 12,
                "seconds_limit": 120,
            },
            "recovery-live",
        )
        await Worker(settings).run(once=True)
        assert repo.get(result["run_id"])["status"] == "succeeded"
        current = [a for a in Files(db).list(project) if a["kind"] == "version"]
        assert len(current) == len(versions) + 1
        content = settings.data_dir / "versions" / current[-1]["id"] / "recovery.txt"
        assert (
            content.read_text(encoding="utf-8").strip() == "restored DeepSeek verified"
        )
        for asset in versions:
            assert Files(db).archive(asset["id"]) == Files(original).archive(
                asset["id"]
            )
        evidence = {
            "backup_restore": before,
            "original_versions_unchanged": True,
            "restored_deepseek_continuation": True,
            "versions_after": len(current),
        }
        (root / "evidence.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        print(json.dumps({"evidence": str(root / "evidence.json"), **evidence}))
    finally:
        db.close()
        original.close()


if __name__ == "__main__":
    asyncio.run(main())

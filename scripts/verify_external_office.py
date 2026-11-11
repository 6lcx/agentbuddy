"""Real DeepSeek reads uploaded external DOCX/XLSX/PPTX and preserves originals."""

import argparse
import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.office import Office
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)
    db = Database(settings.database_path)
    try:
        with tempfile.TemporaryDirectory(
            prefix="agentbuddy-external-office-"
        ) as temporary:
            office = Office(Path(temporary))
            office.generate(
                "word",
                "source.docx",
                {
                    "title": "External Word",
                    "sections": [{"paragraphs": ["WORD_SOURCE_42"]}],
                },
            )
            office.generate(
                "excel",
                "source.xlsx",
                {"sheets": [{"name": "Source", "rows": [["EXCEL_SOURCE_7", 7]]}]},
            )
            office.generate(
                "powerpoint",
                "source.pptx",
                {"slides": [{"title": "PPT_SOURCE_9", "bullets": ["External source"]}]},
            )
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w") as archive:
                for name in ("source.docx", "source.xlsx", "source.pptx"):
                    archive.write(Path(temporary) / name, name)
            files = Files(db)
            draft = str(uuid4())
            upload = files.upload("external.zip", content.getvalue(), draft)
            original = files.archive(upload["id"])
            repo = RunRepository(db)
            result = repo.submit(
                {
                    "message": "请分别调用 read_office 读取 source.docx、source.xlsx、source.pptx。将三个文件中的 SOURCE 标记逐字写入 external-report.txt，各占一行。不要修改这三个原文件。简短回复即可。",
                    "upload_ids": [upload["id"]],
                    "draft_id": draft,
                    "selection": {"mode": "general"},
                    "calls_limit": 20,
                    "seconds_limit": 240,
                },
                draft,
            )
            await Worker(settings).run(once=True)
            run = repo.get(result["run_id"])
            if run["status"] != "succeeded":
                raise RuntimeError(str(run["failure_json"]))
            version = [
                a for a in files.list(result["project_id"]) if a["kind"] == "version"
            ][-1]
            text = (
                settings.data_dir / "versions" / version["id"] / "external-report.txt"
            ).read_text(encoding="utf-8")
            assert all(
                marker in text
                for marker in ("WORD_SOURCE_42", "EXCEL_SOURCE_7", "PPT_SOURCE_9")
            )
            assert original == files.archive(upload["id"])
            evidence = {
                "run_id": result["run_id"],
                "external_formats": ["docx", "xlsx", "pptx"],
                "content_markers_verified": True,
                "original_upload_unchanged": True,
            }
            (settings.data_dir / "external-evidence.json").write_text(
                json.dumps(evidence, indent=2), encoding="utf-8"
            )
            print(json.dumps(evidence))
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

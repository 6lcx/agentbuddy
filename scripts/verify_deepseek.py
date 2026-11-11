"""Explicitly authorized real DeepSeek check. Never prints credentials."""

import argparse
import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path

from agentbuddy.catalog.service import Catalog
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.runtime.executor import Executor
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker
from dotenv import dotenv_values


async def configure(settings, values, max_output_tokens=2048, context_window=32768):
    settings.prepare_directories()
    upgrade(settings.database_path)
    db = Database(settings.database_path)
    catalog = Catalog(db)
    existing = next(
        (p for p in catalog.provider_list() if p["name"] == "Globex DeepSeek"), None
    )
    payload = {
        "name": "Globex DeepSeek",
        "provider_type": "deepseek",
        "base_url": values["LLM_BASE_URL"],
        "api_key": values["LLM_API_KEY"],
        "enabled": True,
        "config_json": {},
    }
    if existing:
        payload["revision"] = existing["revision"]
    provider = catalog.save_provider(payload, existing["id"] if existing else None)
    old = next(
        (
            m
            for m in catalog.model_list()
            if m["provider_id"] == provider["id"]
            and m["model_id"] == values["LLM_MODEL"]
        ),
        None,
    )
    model_data = {
        "provider_id": provider["id"],
        "model_id": values["LLM_MODEL"],
        "enabled": True,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
    }
    if old:
        model_data["revision"] = old["revision"]
    model = catalog.save_model(model_data, old["id"] if old else None)
    result = await Executor(db).model_probe(catalog.test_payload(model["id"]))
    if not result["passed"]:
        print(json.dumps(result, ensure_ascii=False))
        raise RuntimeError("Real DeepSeek probe failed")
    catalog.set_default(model["id"], catalog.settings()["revision"])
    return db, model


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--configure-platform", action="store_true")
    args = parser.parse_args()
    values = dotenv_values(args.source_env)
    # One authorized source, no fallback to unrelated credentials.
    if not all(values.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")):
        raise RuntimeError("Source lacks required LLM configuration")
    if args.configure_platform:
        db, model = await configure(Settings(), values)
        db.close()
        print("Platform DeepSeek configuration saved; text/tools probe passed.")
    with tempfile.TemporaryDirectory(prefix="agentbuddy-real-deepseek-") as folder:
        settings = Settings(data_dir=Path(folder))
        db, model = await configure(settings, values)
        try:
            repo = RunRepository(db)
            result = repo.submit(
                {
                    "message": "请调用 write_file 创建 report.txt，内容必须是 verified real deepseek。完成后简短回复。",
                    "model_id": model["id"],
                    "calls_limit": 10,
                    "seconds_limit": 180,
                },
                "real-file-v1",
            )
            await Worker(settings).run(once=True)
            first = repo.get(result["run_id"])
            versions = Files(db).list(result["project_id"])
            if first["status"] != "succeeded" or not versions:
                raise RuntimeError("Real tool execution did not publish a version")
            baseline = Files(db).archive(versions[-1]["id"])
            assert (
                zipfile.ZipFile(io.BytesIO(baseline))
                .read("report.txt")
                .decode()
                .strip()
                == "verified real deepseek"
            )
            repo.submit(
                {
                    "project_id": result["project_id"],
                    "message": "请读取 report.txt，然后将内容修改为 verified real deepseek version 2，保留文件名。",
                    "model_id": model["id"],
                    "calls_limit": 10,
                    "seconds_limit": 180,
                },
                "real-file-v2",
            )
            await Worker(settings).run(once=True)
            current = Files(db).list(result["project_id"])
            assert len(current) == 2 and baseline == Files(db).archive(
                versions[-1]["id"]
            )
            print(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "model": model["model_id"],
                        "text_tools_probe": "passed",
                        "file_versions": len(current),
                        "old_version_unchanged": True,
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            db.close()


if __name__ == "__main__":
    asyncio.run(main())

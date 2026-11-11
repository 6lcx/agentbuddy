"""Offline, verified data backup and non-destructive restore."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path

from filelock import FileLock

from agentbuddy.config import Settings
from agentbuddy.runtime.files import manifest, relative

DIRECTORIES = ("projects", "uploads", "versions", "skills", "mcp_packages")


def validate_data(root):
    database = root / "agentbuddy.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database foreign key check failed")
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        for identifier, kind, listing in conn.execute("SELECT id,kind,manifest_json FROM assets"):
            directory = root / ("versions" if kind == "version" else "uploads") / identifier
            if not directory.is_dir() or manifest(directory) != json.loads(listing):
                raise ValueError("Asset missing or changed: " + identifier)
        for kind, config in conn.execute(
            "SELECT kind,config_json FROM capabilities WHERE kind IN ('skill','skill_version')"
        ):
            config = json.loads(config)
            if config.get("package_id"):
                directory = root / "skills" / config["package_id"]
                if not directory.is_dir() or manifest(directory) != config["files"]:
                    raise ValueError("Skill package missing or changed")
        return {
            "revision": revision,
            "projects": conn.execute("SELECT count(*) FROM projects").fetchone()[0],
            "assets": conn.execute("SELECT count(*) FROM assets").fetchone()[0],
        }


def backup(source, output, api_prefix="/api/v1"):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Backup must be outside the source data directory")
    if output.exists():
        raise FileExistsError(output)
    database = source / "agentbuddy.sqlite3"
    if not database.is_file():
        raise ValueError("Source database missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for suffix in (".api.lock", ".worker.lock"):
            stack.enter_context(FileLock(str(database) + suffix, timeout=0))
        with closing(sqlite3.connect(database)) as conn:
            for table in ("runs", "operations"):
                if conn.execute(
                    f"SELECT count(*) FROM {table} WHERE status IN ('running','queued')"
                ).fetchone()[0]:
                    raise ValueError("Finish or cancel queued/running work before backup")
            with tempfile.TemporaryDirectory(prefix="agentbuddy-backup-") as temporary:
                root = Path(temporary) / "data"
                root.mkdir()
                with closing(sqlite3.connect(root / "agentbuddy.sqlite3")) as dest:
                    conn.backup(dest)
                for name in DIRECTORIES:
                    if (source / name).exists():
                        # Never follow symlinks when copying trusted data by accident.
                        manifest(source / name)
                        shutil.copytree(source / name, root / name)
                result = validate_data(root)
                listing = manifest(root)
                metadata = {
                    "format": 1,
                    "source": str(source),
                    "api_prefix": api_prefix,
                    "files": listing,
                    "validation": result,
                }
                with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("backup.json", json.dumps(metadata, ensure_ascii=False))
                    for item in listing:
                        archive.write(root / item["path"], "data/" + item["path"])
                return result


def restore(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="agentbuddy-restore-", dir=destination.parent
    ) as temporary:
        root = Path(temporary) / "data"
        root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            metadata = json.loads(archive.read("backup.json"))
            if metadata["format"] != 1:
                raise ValueError("Unsupported backup format")
            expected = {f["path"]: f for f in metadata["files"]}
            if len(expected) != len(metadata["files"]):
                raise ValueError("Duplicate manifest path")
            names = archive.namelist()
            if len(set(names)) != len(names) or set(names) != {
                "backup.json",
                *("data/" + p for p in expected),
            }:
                raise ValueError("Archive does not match manifest")
            for name, item in expected.items():
                target = root / relative(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open("data/" + name) as src, target.open("xb") as dest:
                    while chunk := src.read(1024 * 1024):
                        size += len(chunk)
                        if size > item["size"]:
                            raise ValueError("Backup file size mismatch")
                        digest.update(chunk)
                        dest.write(chunk)
                if size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError("Backup file checksum mismatch")
        result = validate_data(root)
        # Rebind only current packaged MCP launch paths after relocation.
        with closing(sqlite3.connect(root / "agentbuddy.sqlite3")) as conn:
            source = metadata["source"]
            for identifier, raw in conn.execute(
                "SELECT id,config_json FROM capabilities WHERE kind='connector'"
            ).fetchall():
                config = json.loads(raw)
                config["args"] = [
                    str(destination) + v[len(source) :]
                    if isinstance(v, str) and v.startswith(source + os.sep)
                    else v
                    for v in config.get("args", [])
                ]
                conn.execute(
                    "UPDATE capabilities SET config_json=? WHERE id=?",
                    (json.dumps(config), identifier),
                )
            conn.commit()
        if destination.exists():
            raise FileExistsError(destination)
        root.rename(destination)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Settings().data_dir)
    args = parser.parse_args()
    if args.action == "backup":
        result = backup(args.data_dir, args.path, Settings().api_prefix)
    elif args.action == "restore":
        result = restore(args.path, args.data_dir)
    else:
        result = validate_data(args.path)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

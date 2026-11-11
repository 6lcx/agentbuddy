import csv
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import delete, insert, select

from agentbuddy.catalog.service import DomainError
from agentbuddy.persistence.schema import assets


def relative(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        not path.parts
        or not name
        or path.is_absolute()
        or any(p in ("..", "") or ":" in p for p in path.parts)
    ):
        raise DomainError("INVALID_PATH", "文件路径必须在项目目录中", 422)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }
    if any(
        p != p.rstrip(". ")
        or any(c in p for c in '<>"|?*\x00')
        or p.split(".")[0].upper() in reserved
        for p in path.parts
    ):
        raise DomainError("INVALID_PATH", "文件名存在跨平台歧义", 422)
    return path.as_posix()


def manifest(root):
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DomainError("INVALID_FILE", "不支持符号链接文件")
        if path.is_file():
            content = path.read_bytes()
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    return result


def unpack(content, root, *, limit=500 * 1024 * 1024):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > limit:
                raise DomainError("ZIP_LIMIT", "压缩包展开后超过限制", 422)
            names = set()
            for entry in entries:
                name = relative(entry.filename)
                if name.casefold() in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise DomainError("ZIP_CONFLICT", "压缩包存在路径冲突或符号链接", 422)
                names.add(name.casefold())
                target = root / name
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(entry))
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise DomainError("INVALID_ZIP", "压缩包损坏、加密或路径冲突", 422) from exc


class Files:
    def __init__(self, db):
        self.db = db
        self.root = db.path.parent

    def expire_drafts(self, now=None):
        """Only recorded, unowned uploads older than 24 hours are eligible."""
        cutoff = (time.time() if now is None else now) - 86400
        with self.db.write() as conn:
            rows = (
                conn.execute(
                    select(assets).where(
                        assets.c.kind == "input",
                        assets.c.project_id.is_(None),
                        assets.c.created_at < cutoff,
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                root = (self.root / "uploads").resolve()
                target = (root / row["id"]).resolve()
                if target.parent != root:
                    raise DomainError("INVALID_PATH", "草稿存储路径不正确")
                # Remove the reference first; interrupted cleanup leaves an unreferenced directory.
                conn.execute(delete(assets).where(assets.c.id == row["id"]))
        for row in rows:
            target = (self.root / "uploads" / row["id"]).resolve()
            if target.exists():
                shutil.rmtree(target)
        return len(rows)

    def upload(self, name, content, draft_id):
        if len(content) > 50 * 1024 * 1024:
            raise DomainError("UPLOAD_LIMIT", "上传超过 50 MiB", 422)
        identifier = str(uuid4())
        root = self.root / "uploads" / identifier
        root.mkdir(parents=True)
        if name.lower().endswith(".zip"):
            unpack(content, root)
        else:
            target = root / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        row = dict(
            id=identifier,
            kind="input",
            project_id=None,
            run_id=None,
            draft_id=draft_id,
            manifest_json=manifest(root),
            created_at=time.time(),
        )
        with self.db.write() as conn:
            conn.execute(insert(assets).values(**row))
        return row

    def list(self, project_id):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(assets)
                    .where(assets.c.project_id == project_id)
                    .order_by(assets.c.created_at)
                ).mappings()
            ]

    def workspace(self, claimed):
        root = self.root / "projects" / claimed["project_id"] / "attempts" / claimed["id"]
        if root.exists():
            return root
        root.mkdir(parents=True)
        versions = [a for a in self.list(claimed["project_id"]) if a["kind"] == "version"]
        if versions:
            shutil.copytree(self.root / "versions" / versions[-1]["id"], root, dirs_exist_ok=True)
        for identifier in claimed["snapshot_json"].get("upload_ids", []):
            source = self.root / "uploads" / identifier
            for item in manifest(source):
                target = root / item["path"]
                if target.exists():
                    raise DomainError("INPUT_CONFLICT", "上传文件与项目已有路径冲突，请重命名")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], target)
        root.with_suffix(".baseline.json").write_text(json.dumps(manifest(root)), encoding="utf-8")
        return root

    def stage_version(self, claimed, root):
        listing = manifest(root)
        versions = [a for a in self.list(claimed["project_id"]) if a["kind"] == "version"]
        baseline = root.with_suffix(".baseline.json")
        unchanged = baseline.exists() and listing == json.loads(
            baseline.read_text(encoding="utf-8")
        )
        if (
            unchanged
            or (not listing and not versions and not baseline.exists())
            or (versions and listing == versions[-1]["manifest_json"])
        ):
            return None
        for item in listing:
            path = root / item["path"]
            if path.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                from agentbuddy.runtime.office import Office

                if ".agentbuddy" not in path.relative_to(root).parts and not Office(root).proof(
                    item["path"]
                ):
                    raise DomainError(
                        "OFFICE_VALIDATION_PENDING", "Office 产物缺少匹配的渲染验证报告"
                    )
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".py":
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            elif path.suffix == ".csv":
                with path.open(encoding="utf-8", newline="") as source:
                    list(csv.reader(source, strict=True))
        identifier = str(uuid4())
        destination = self.root / "versions" / identifier
        temporary = self.root / "versions" / (identifier + ".staging")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        required = sum(item["size"] for item in listing) + 10 * 1024 * 1024
        if shutil.disk_usage(temporary.parent).free < required:
            raise DomainError("DISK_FULL", "磁盘空间不足以发布文件版本，旧版本已保留")
        shutil.copytree(root, temporary)
        if manifest(temporary) != listing:
            raise DomainError("FILE_CHANGED", "文件在发布过程中发生变化")
        os.replace(temporary, destination)
        destination.with_suffix(".report.json").write_text(
            json.dumps(
                {
                    "manifest": listing,
                    "office_reports": [
                        i["path"]
                        for i in listing
                        if i["path"].startswith(".agentbuddy/previews/")
                        and i["path"].endswith("/report.json")
                    ],
                    "checks": [
                        "relative_paths",
                        "no_symlinks",
                        "copy_sha256",
                        "json_python_csv_parse",
                    ],
                    "scope": "结构检查；不保证任意程序运行或内容语义正确",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # Only the same transaction as successful run completion makes this visible.
        return dict(
            id=identifier,
            kind="version",
            project_id=claimed["project_id"],
            run_id=claimed["id"],
            draft_id=None,
            manifest_json=listing,
            created_at=time.time(),
        )

    def archive(self, identifier):
        with self.db.engine.connect() as conn:
            row = conn.execute(select(assets).where(assets.c.id == identifier)).mappings().first()
        if not row:
            raise DomainError("NOT_FOUND", "文件版本不存在", 404)
        root = self.root / ("versions" if row["kind"] == "version" else "uploads") / identifier
        if manifest(root) != row["manifest_json"]:
            raise DomainError("FILE_CHANGED", "文件完整性检查失败")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in row["manifest_json"]:
                entry = zipfile.ZipInfo(item["path"], date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, (root / item["path"]).read_bytes())
        return output.getvalue()

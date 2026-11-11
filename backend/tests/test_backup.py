import zipfile

import pytest
import test_chat
from fastapi.testclient import TestClient
from filelock import FileLock, Timeout
from test_capabilities import running

from agentbuddy.backup import backup, restore, validate_data
from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.main import create_app
from agentbuddy.runtime.files import Files


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


async def test_backup_restore_versions_skills_and_reject_tampering(setup_chat, tmp_path):
    _, db = setup_chat
    catalog = Capabilities(db)
    catalog.seed_presets()
    repo, claimed, _ = await running(db)
    files = Files(db)
    root = files.workspace(claimed)
    (root / "result.txt").write_text("original result", encoding="utf-8")
    version = files.stage_version(claimed, root)
    assert repo.finish(claimed, text="done", version=version)
    archive = tmp_path.parent / (tmp_path.name + "-backup.zip")
    result = backup(db.path.parent, archive)
    target = tmp_path.parent / (tmp_path.name + "-restored")
    assert restore(archive, target) == result
    assert validate_data(target) == result
    from agentbuddy.persistence.database import Database

    restored = Database(target / "agentbuddy.sqlite3")
    try:
        assert Files(restored).archive(version["id"]) == files.archive(version["id"])
    finally:
        restored.close()
    assert (target / "versions" / version["id"] / "result.txt").read_text() == "original result"
    with pytest.raises(FileExistsError):
        restore(archive, target)
    corrupt = tmp_path.parent / (tmp_path.name + "-corrupt.zip")
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(corrupt, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, b"changed" if name.endswith("/result.txt") else src.read(name))
    failed = tmp_path.parent / (tmp_path.name + "-failed")
    with pytest.raises(ValueError):
        restore(corrupt, failed)
    assert not failed.exists()
    assert (root / "result.txt").read_text() == "original result"


async def test_backup_rejects_running_worker_api_and_unfinished_work(setup_chat, tmp_path):
    settings, db = setup_chat
    archive = tmp_path.parent / (tmp_path.name + "-locked.zip")
    with FileLock(str(db.path) + ".worker.lock"):
        with pytest.raises(Timeout):
            backup(db.path.parent, archive)
    with TestClient(create_app(settings)):
        from agentbuddy.persistence.migrate import upgrade

        with pytest.raises(Timeout):
            upgrade(db.path)
        with pytest.raises(Timeout):
            backup(db.path.parent, archive)
    await running(db)
    with pytest.raises(ValueError, match="Finish or cancel"):
        backup(db.path.parent, archive)
    assert not archive.exists()


async def test_disk_full_keeps_prior_version(setup_chat, monkeypatch):
    from types import SimpleNamespace

    import agentbuddy.runtime.files as module
    from agentbuddy.catalog.service import DomainError

    _, db = setup_chat
    repo, claimed, created = await running(db)
    files = Files(db)
    root = files.workspace(claimed)
    (root / "a.txt").write_text("v1")
    version = files.stage_version(claimed, root)
    repo.finish(claimed, text="done", version=version)
    original = files.archive(version["id"])
    repo.submit({"message": "modify", "project_id": created["project_id"]}, "full")
    claimed = repo.claim(claimed["worker_id"])
    root = files.workspace(claimed)
    (root / "a.txt").write_text("v2")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(DomainError, match="磁盘"):
        files.stage_version(claimed, root)
    assert files.archive(version["id"]) == original

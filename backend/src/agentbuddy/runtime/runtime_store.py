"""Atomic runtime records and content-addressed tool results outside deliverables."""

import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import insert, select, update

from agentbuddy.persistence.schema import settings_table
from agentbuddy.providers.native import ProviderFailure


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class RuntimeStore:
    def __init__(self, db, project_id, run_id, lane="main"):
        self.db, self.project_id, self.run_id, self.lane = db, project_id, run_id, lane
        self.root = db.path.parent / "projects" / project_id / "runtime" / run_id / lane
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, name):
        return f"runtime:{self.run_id}:{self.lane}:{name}"

    def get(self, name, default=None):
        with self.db.engine.connect() as conn:
            value = conn.scalar(
                select(settings_table.c.value_json).where(settings_table.c.key == self.key(name))
            )
        return default if value is None else value

    def put(self, name, value):
        with self.db.write() as conn:
            key = self.key(name)
            if conn.scalar(select(settings_table.c.key).where(settings_table.c.key == key)):
                conn.execute(
                    update(settings_table)
                    .where(settings_table.c.key == key)
                    .values(value_json=value, revision=settings_table.c.revision + 1)
                )
            else:
                conn.execute(insert(settings_table).values(key=key, value_json=value, revision=1))

    def result(self, text, policy):
        data = text.encode()
        if len(data) > policy.result_max_bytes:
            raise ProviderFailure("result_too_large", "工具结果超过单结果存储上限")
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / (digest + ".txt")
        if not path.exists():
            # Per-run quota shared by all lanes. FileLock covers parallel workers/threads.
            from filelock import FileLock

            with FileLock(str(self.root.parent / ".results.lock")):
                used = sum(p.stat().st_size for p in self.root.parent.rglob("*.txt"))
                if used + len(data) > policy.result_quota_bytes:
                    raise ProviderFailure("result_quota", "本轮结果存储额度已用尽")
                temporary = path.with_suffix("." + str(uuid4()) + ".tmp")
                temporary.write_bytes(data)
                os.replace(temporary, path)
        return {
            "result_id": digest,
            "sha256": digest,
            "bytes": len(data),
            "type": "text/plain; charset=utf-8",
            "complete": True,
            "preview": data[:512].decode(errors="replace"),
            "read_tool": "read_result",
        }

    def read(self, identifier, offset=0, limit=8192):
        if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid result id")
        if offset < 0 or not 1 <= limit <= 16384:
            raise ValueError("Invalid result range")
        path = self.root / (identifier + ".txt")
        if not path.is_file():
            raise ProviderFailure("result_missing", "结果引用不存在或已过期")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != identifier:
            raise ProviderFailure("result_changed", "结果引用完整性校验失败")
        if offset < len(data) and data[offset] & 0xC0 == 0x80:
            raise ValueError("Result offset must be a UTF-8 character boundary")
        end = min(offset + max(limit, 4), len(data))
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        return {
            "result_id": identifier,
            "text": data[offset:end].decode(),
            "next_offset": end,
            "eof": end >= len(data),
        }

import importlib.util
import json
import sys
from uuid import uuid4

from sqlalchemy import insert, select, update

from agentbuddy.catalog.service import DomainError
from agentbuddy.persistence.schema import capabilities
from agentbuddy.runtime.files import manifest, relative, unpack


class Capabilities:
    def __init__(self, db):
        self.db = db

    def list(self, kind):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(capabilities).where(capabilities.c.kind == kind)
                ).mappings()
            ]

    def get(self, identifier, conn=None):
        if conn is None:
            with self.db.engine.connect() as connection:
                return self.get(identifier, connection)
        row = (
            conn.execute(select(capabilities).where(capabilities.c.id == identifier))
            .mappings()
            .first()
        )
        if not row:
            raise DomainError("NOT_FOUND", "能力不存在", 404)
        return dict(row)

    def save(self, kind, config, identifier=None, revision=None):
        if not isinstance(config, dict):
            raise DomainError("INVALID_CONFIG", "配置必须是对象", 422)
        if kind in {"skill", "expert", "connector"} and not isinstance(config.get("name"), str):
            raise DomainError("INVALID_CONFIG", "请填写能力名称", 422)
        if kind == "expert":
            for field in ("skill_ids", "connector_ids", "collaborator_ids"):
                value = config.get(field, [])
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise DomainError("INVALID_CONFIG", "绑定项必须是标识列表", 422)
        if kind == "connector":
            from urllib.parse import urlsplit

            transport = config.get("transport")
            if transport == "stdio":
                if not isinstance(config.get("command"), str) or not config["command"]:
                    raise DomainError("INVALID_CONFIG", "请填写进程启动命令", 422)
                if not isinstance(config.get("args", []), list) or not all(
                    isinstance(v, str) for v in config.get("args", [])
                ):
                    raise DomainError("INVALID_CONFIG", "进程参数必须是字符串列表", 422)
            elif transport == "http":
                if not isinstance(config.get("url"), str) or urlsplit(config["url"]).scheme not in (
                    "http",
                    "https",
                ):
                    raise DomainError("INVALID_CONFIG", "请填写 HTTP MCP 地址", 422)
            else:
                raise DomainError("INVALID_CONFIG", "不支持该 MCP 传输类型", 422)
        identifier = identifier or str(uuid4())
        with self.db.write() as conn:
            existing = (
                conn.execute(select(capabilities).where(capabilities.c.id == identifier))
                .mappings()
                .first()
            )
            if existing and (existing["revision"] != revision or existing["kind"] != kind):
                raise DomainError("REVISION_CONFLICT", "配置已被修改，请刷新")
            row = dict(
                id=identifier,
                kind=kind,
                revision=(existing["revision"] + 1 if existing else 1),
                config_json=config,
            )
            if existing:
                conn.execute(
                    update(capabilities).where(capabilities.c.id == identifier).values(**row)
                )
            else:
                conn.execute(insert(capabilities).values(**row))
        return row

    def install(self, content, skill_id=None):
        if len(content) > 20 * 1024 * 1024:
            raise DomainError("PACKAGE_LIMIT", "Skill 包超过 20 MiB", 422)
        identifier = str(uuid4())
        root = self.db.path.parent / "skills" / identifier
        root.mkdir(parents=True)
        unpack(content, root, limit=100 * 1024 * 1024)
        try:
            config = json.loads((root / "skill.json").read_text(encoding="utf-8"))
            instructions = (root / "SKILL.md").read_text(encoding="utf-8")
            if not isinstance(config, dict) or not config.get("name") or not instructions.strip():
                raise ValueError()
            for script in config.get("scripts", []):
                if not (root / relative(script)).is_file() or not script.endswith(".py"):
                    raise ValueError()
            for dependency in config.get("requires", []):
                try:
                    available = importlib.util.find_spec(dependency) is not None
                except (ModuleNotFoundError, ValueError, TypeError):
                    available = False
                if not available:
                    raise DomainError(
                        "MISSING_DEPENDENCY", f"服务器缺少 Python 模块：{dependency}", 422
                    )
        except (OSError, ValueError, TypeError) as exc:
            raise DomainError(
                "INVALID_SKILL", "需要有效的 skill.json、SKILL.md 和 Python 脚本入口", 422
            ) from exc
        previous = self.get(skill_id) if skill_id else None
        if previous:
            if previous["kind"] != "skill":
                raise DomainError("INVALID_SKILL", "目标不是 Skill")
            self.save("skill_version", {"skill_id": skill_id, "package": previous["config_json"]})
        return self.save(
            "skill",
            {
                **config,
                "package_id": identifier,
                "instructions": instructions,
                "files": manifest(root),
                "enabled": previous["config_json"].get("enabled", False) if previous else False,
                "installed": True,
            },
            skill_id,
            previous["revision"] if previous else None,
        )

    def resolve(self, selection, conn=None):
        expert = (
            self.get(selection["expert_id"], conn)
            if selection.get("expert_id") and selection.get("mode") != "team"
            else None
        )
        if expert and (
            expert["kind"] != "expert" or not expert["config_json"].get("enabled", True)
        ):
            raise DomainError("EXPERT_UNAVAILABLE", "专家不存在或已禁用")
        config = expert["config_json"] if expert else {}
        defaults = (
            config.get("skill_ids", [])
            if expert
            else [s["id"] for s in self.list("skill") if s["config_json"].get("enabled")]
        )
        ids = (set(defaults) | set(selection.get("add_skills", []))) - set(
            selection.get("remove_skills", [])
        )
        skills = [self.get(identifier, conn) for identifier in sorted(ids)]
        if any(s["kind"] != "skill" or not s["config_json"].get("enabled") for s in skills):
            raise DomainError("SKILL_UNAVAILABLE", "所选 Skill 未启用，请在能力目录补齐")
        connector_ids = (
            set(config.get("connector_ids", [])) | set(selection.get("add_connectors", []))
        ) - set(selection.get("remove_connectors", []))
        connectors = [self.get(identifier, conn) for identifier in sorted(connector_ids)]
        if any(c["kind"] != "connector" for c in connectors):
            raise DomainError("INVALID_BINDING", "连接器绑定不正确")
        return {
            "expert": expert,
            "skills": skills,
            "connectors": connectors,
            "selection": selection,
        }

    def import_connector(self, content):
        if len(content) > 20 * 1024 * 1024:
            raise DomainError("PACKAGE_LIMIT", "MCP 包超过 20 MiB", 413)
        root = self.db.path.parent / "mcp_packages" / str(uuid4())
        root.mkdir(parents=True)
        unpack(content, root, limit=100 * 1024 * 1024)
        try:
            config = json.loads((root / "mcp.json").read_text(encoding="utf-8"))
            if not isinstance(config, dict) or not config.get("name"):
                raise ValueError()
            entry = root / relative(config["entry"])
            if not entry.is_file() or entry.suffix != ".py":
                raise ValueError()
            for dependency in config.get("requires", []):
                try:
                    available = importlib.util.find_spec(dependency) is not None
                except (ModuleNotFoundError, ValueError, TypeError):
                    available = False
                if not available:
                    raise DomainError("MISSING_DEPENDENCY", f"服务器缺少模块：{dependency}", 422)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DomainError(
                "INVALID_PACKAGE", "MCP 包需要 mcp.json 和 Python entry", 422
            ) from exc
        return self.save(
            "connector",
            {
                "name": config["name"],
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(entry), *config.get("args", [])],
                "enabled": True,
                "effects": config.get("effects", {}),
                "tools": [],
            },
        )

    def seed_presets(self):
        import io
        import zipfile
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "presets" / "skills"
        existing = {s["config_json"].get("preset_key"): s for s in self.list("skill")}
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name in existing:
                continue
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for path in sorted(folder.rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts:
                        archive.write(path, path.relative_to(folder).as_posix())
            row = self.install(buffer.getvalue())
            self.save(
                "skill",
                {
                    **row["config_json"],
                    "enabled": True,
                    "preset_key": folder.name,
                    "source": "platform",
                },
                row["id"],
                row["revision"],
            )
        skills = {s["config_json"].get("preset_key"): s["id"] for s in self.list("skill")}
        existing_experts = {e["config_json"].get("preset_key") for e in self.list("expert")}
        for key, name, skill_keys, prompt in [
            (
                "developer",
                "开发专家",
                ["project-files", "development"],
                "分析需求，编写项目文件并检查可运行性。不得声称未运行的测试已通过。",
            ),
            (
                "researcher",
                "资料整理专家",
                ["project-files"],
                "整理用户提供的资料，区分事实与推断，输出带来源的报告。没有网络连接时不得虚构检索。",
            ),
            ("word", "文档专家", ["office-word"], "生成和修改 Word 报告，先加载文档 Skill。"),
            (
                "excel",
                "表格专家",
                ["office-excel"],
                "生成和修改 Excel 表格，使用公式表达计算，先加载表格 Skill。",
            ),
            (
                "powerpoint",
                "演示专家",
                ["office-powerpoint"],
                "生成和修改演示文稿，先加载演示 Skill，控制每页内容密度。",
            ),
        ]:
            if key not in existing_experts:
                self.save(
                    "expert",
                    {
                        "name": name,
                        "enabled": True,
                        "preset_key": key,
                        "source": "platform",
                        "prompt": prompt,
                        "skill_ids": [skills[k] for k in skill_keys],
                        "connector_ids": [],
                        "collaborator_ids": [],
                    },
                )

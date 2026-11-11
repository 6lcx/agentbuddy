import asyncio
import json
import os
import signal
import subprocess
import sys
from contextlib import AsyncExitStack, asynccontextmanager

import httpx2
from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit, ToolResponse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.catalog.service import DomainError
from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.collaboration import TaskPlan
from agentbuddy.runtime.files import manifest, relative


@asynccontextmanager
async def mcp_session(config):
    async with AsyncExitStack() as stack:
        if config["transport"] == "stdio":
            manager = stdio_client(
                StdioServerParameters(
                    command=config["command"],
                    args=config.get("args", []),
                    env={**os.environ, **config.get("env", {})},
                )
            )
        else:
            client = await stack.enter_async_context(
                httpx2.AsyncClient(headers=config.get("headers", {}), timeout=120)
            )
            manager = streamable_http_client(config["url"], http_client=client)
        streams = await stack.enter_async_context(manager)
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        yield session


async def discover(config):
    async with asyncio.timeout(30):
        async with mcp_session(config) as session:
            result = await session.list_tools()
            return [t.model_dump(mode="json") for t in result.tools]


class RunTools:
    def __init__(self, db, claimed, control, root):
        self.db, self.claimed, self.control, self.root = db, claimed, control, root
        from agentbuddy.runtime.context_policy import ContextPolicy
        from agentbuddy.runtime.runtime_store import RuntimeStore

        self.policy = ContextPolicy.model_validate(
            claimed["snapshot_json"].get("context_policy", {})
        )
        self.store = RuntimeStore(
            db, claimed["project_id"], claimed["id"], getattr(control, "lane_id", "main")
        )
        self.loaded_connectors = set()
        self.skills = {
            s["id"]: s["config_json"]
            for s in claimed["snapshot_json"].get("capabilities", {}).get("skills", [])
        }
        self.loaded = set()
        self.uncertain = set()
        self.supervisor = None

    def toolkit(self):
        tools = []
        names = [
            "read_result",
            "search_files",
            "discover_tools",
            "describe_tool",
            "list_files",
            "ask_user",
            "read_file",
            "write_file",
            "load_skill",
            "read_skill_file",
            "run_script",
            "call_connector",
            "generate_office",
            "get_office_template",
            "edit_office",
            "read_office",
        ]
        if self.supervisor is not None:
            names += ["delegate_tasks", "resolve_conflict", "read_task_file"]
        for name in names:
            function = getattr(self, name)
            schema = FunctionTool(function).input_schema

            def instrument(function, name):
                async def invoke(**arguments):
                    self.control.event("tool.started", {"name": name})
                    try:
                        result = await function(**arguments)
                        payload = {"name": name}
                        if result.content and isinstance(result.content[0], TextBlock):
                            payload["summary"] = result.content[0].text[:16384]
                            try:
                                content = json.loads(result.content[0].text)
                                if isinstance(content, dict) and content.get("log_url"):
                                    payload["log_url"] = content["log_url"]
                            except ValueError:
                                pass
                        self.control.event("tool.completed", payload)
                        return result
                    except Exception:
                        self.control.event("tool.failed", {"name": name})
                        raise

                return invoke

            tools.append(
                FunctionTool(
                    instrument(function, name),
                    name=name,
                    description=function.__doc__,
                    input_schema=schema,
                    permission=PermissionDecision(
                        behavior=PermissionBehavior.ALLOW,
                        message="Platform gate handles confirmation",
                    ),
                    is_concurrency_safe=False,
                )
            )
        return Toolkit(tools=tools)

    def response(self, value):
        text = json.dumps(value, ensure_ascii=False)
        if len(text.encode()) > self.policy.result_inline_bytes:
            reference = self.store.result(text, self.policy)
            self.control.event("result.offloaded", reference)
            text = json.dumps(reference, ensure_ascii=False)
        return ToolResponse(content=[TextBlock(text=text)])

    def resource(self, key, value, version):
        loaded = self.store.get("loaded_resources", {})
        previous = loaded.get(key)
        if previous and previous["version"] == version:
            self.control.event("resource.reused", {"resource": key, "version": version})
            return self.response({"already_loaded": True, **previous["reference"]})
        reference = self.store.result(json.dumps(value, ensure_ascii=False), self.policy)
        loaded[key] = {"version": version, "reference": reference}
        self.store.put("loaded_resources", loaded)
        self.control.event("resource.loaded", {"resource": key, "version": version})
        return self.response(value)

    def supervisor_state(self):
        if self.supervisor is None:
            return None
        s = self.supervisor
        return {
            "conflicts": s.conflicts,
            "failed": sorted(s.failed),
            "unsafe_failures": sorted(s.unsafe_failures),
            "dispatched": s.dispatched,
            "children": {
                k: v.relative_to(self.db.path.parent).as_posix() for k, v in s.children.items()
            },
        }

    async def read_result(self, result_id: str, offset: int = 0) -> ToolResponse:
        """Read a page of this agent's immutable stored result; offsets are bytes."""
        self.control.check(reserve=True)
        value = self.store.read(result_id, offset, self.policy.read_bytes)
        self.control.event("resource.read", {"result_id": result_id, "offset": offset})
        return ToolResponse(content=[TextBlock(text=json.dumps(value, ensure_ascii=False))])

    async def search_files(self, query: str, path: str = ".") -> ToolResponse:
        """Find up to 50 UTF-8 text matches without loading all project files into context."""
        self.control.check(reserve=True)
        folder = self.root if path == "." else self.root / relative(path)
        matches = []
        for file in sorted(folder.rglob("*")):
            if file.is_file() and not file.is_symlink() and file.stat().st_size <= 1024 * 1024:
                try:
                    for index, line in enumerate(file.read_text(encoding="utf-8").splitlines()):
                        if query.casefold() in line.casefold():
                            matches.append(
                                {
                                    "path": file.relative_to(self.root).as_posix(),
                                    "line": index + 1,
                                    "text": line[:300],
                                }
                            )
                            if len(matches) == 50:
                                return self.response({"matches": matches, "limited": True})
                except UnicodeError:
                    continue
        return self.response({"matches": matches, "limited": False})

    async def discover_tools(
        self, connector_id: str, query: str = "", offset: int = 0
    ) -> ToolResponse:
        """Discover 20 tool names/descriptions from an allowed connector; no full schemas."""
        self.control.check(reserve=True)
        if offset < 0:
            raise ValueError("Invalid offset")
        connector = next(
            (
                c
                for c in self.claimed["snapshot_json"].get("capabilities", {}).get("connectors", [])
                if c["id"] == connector_id
            ),
            None,
        )
        if connector is None:
            raise DomainError("CONNECTOR_UNAVAILABLE", "连接器不在本轮能力范围")
        definitions = connector["config_json"].get("tools", [])
        rows = [
            {"name": t["name"], "description": t.get("description", "")[:200]}
            for t in definitions
            if query.casefold() in (t["name"] + t.get("description", "")).casefold()
        ]
        return self.response(
            {
                "tools": rows[offset : offset + 20],
                "next_offset": offset + 20 if len(rows) > offset + 20 else None,
            }
        )

    async def describe_tool(self, connector_id: str, tool: str) -> ToolResponse:
        """Load only the selected MCP tool schema; selection never adds capabilities."""
        self.control.check(reserve=True)
        connector = next(
            (
                c
                for c in self.claimed["snapshot_json"].get("capabilities", {}).get("connectors", [])
                if c["id"] == connector_id
            ),
            None,
        )
        if connector is None:
            raise DomainError("CONNECTOR_UNAVAILABLE", "连接器不在本轮能力范围")
        definition = next(
            (t for t in connector["config_json"].get("tools", []) if t["name"] == tool), None
        )
        if definition is None:
            raise DomainError("TOOL_UNAVAILABLE", "请先测试连接器并刷新工具目录")
        key = connector_id + ":" + tool
        self.loaded_connectors.add(key)
        reference = self.store.result(json.dumps(definition, ensure_ascii=False), self.policy)
        loaded = self.store.get("loaded_resources", {})
        previous = loaded.get(key)
        loaded[key] = {"revision": connector["revision"], "reference": reference}
        self.store.put("loaded_resources", loaded)
        if previous == loaded[key]:
            return self.response({"already_loaded": True, **reference})
        return self.response(
            {"revision": connector["revision"], "definition": definition, "reference": reference}
        )

    async def delegate_tasks(self, tasks: list[TaskPlan]) -> ToolResponse:
        """Delegate only worthwhile isolated tasks, at most 3 concurrently.
        Declare explicit inputs, constraints, acceptance, parallelizable,
        context_isolated, estimated_iterations (>3), dispatch_reason and outputs.
        Depends_on orders dependencies. A named exception requires policy approval.
        Child agent_policy can only narrow limits. Children cannot delegate.
        Read task results and resolve conflicts before reporting completion.
        """
        return self.response(await self.supervisor.delegate(tasks))

    async def resolve_conflict(self, path: str, task_id: str) -> ToolResponse:
        """Explicitly choose one subtask's file to resolve a reported path conflict."""
        self.control.check(reserve=True)
        self.supervisor.resolve(path, task_id)
        return self.response({"resolved": path})

    async def read_task_file(self, task_id: str, path: str) -> ToolResponse:
        """Inspect a completed child's text file before resolving a merge conflict."""
        self.control.check(reserve=True)
        source = self.supervisor.children[task_id] / relative(path)
        return self.response(source.read_text(encoding="utf-8")[:262144])

    def office_skill(self, kind):
        if not any(self.skills[s].get("preset_key") == "office-" + kind for s in self.loaded):
            raise ValueError(
                "Load the corresponding enabled Office Skill before generating or editing"
            )

    async def office_dependency(self):
        from agentbuddy.runtime.office import renderer

        if not renderer():
            approved = await self.control.confirm(
                {
                    "kind": "dependency",
                    "question": (
                        "服务器缺少 LibreOffice。请安装并设置 SOFFICE_PATH 后确认继续；"
                        "输入和能力选择已保留。"
                    ),
                }
            )
            if not approved or not renderer():
                raise ProviderFailure("office_dependency", "办公渲染依赖尚未准备")

    async def get_office_template(self, kind: str) -> ToolResponse:
        """Get a basic word weekly report, excel budget or powerpoint project template.
        Load matching Skill first. Fill from user sources; leave unknown facts explicit.
        """
        from agentbuddy.runtime.office_templates import office_template

        self.control.check(reserve=True)
        self.office_skill(kind)
        return self.response(office_template(kind))

    async def generate_office(self, kind: str, path: str, content: dict) -> ToolResponse:
        """Generate Office.
        kind word: content {title,sections:[{heading,paragraphs:[text],tables:[rows]}]};
        excel: {sheets:[{name,rows,formulas:[{cell,formula,expected}],chart:boolean,
        chart_range:{start_row:2,end_row:4}}]}; chart_range optional, defaults to
        data rows excluding trailing total rows.
        powerpoint: {slides:[{title,bullets:[text],chart:{categories:[],
        series:[{name,values:[]}]}}]}.
        Long slide body text automatically flows into continuation slides.
        File extension must be docx/xlsx/pptx. Load matching Skill first.
        Real validation runs before delivery."""
        from agentbuddy.runtime.office import Office

        self.control.check(reserve=True)
        self.office_skill(kind)
        await self.office_dependency()
        return self.response(Office(self.root).generate(kind, path, content))

    async def edit_office(self, path: str, content: dict) -> ToolResponse:
        """Regenerate a platform-created Office file using its complete updated content.
        Read its .agentbuddy.json first and preserve unmodified sections.
        External originals support extraction only."""
        from agentbuddy.runtime.office import EXTENSIONS, Office

        self.control.check(reserve=True)
        kind = next(k for k, ext in EXTENSIONS.items() if path.endswith(ext))
        self.office_skill(kind)
        await self.office_dependency()
        return self.response(Office(self.root).edit(path, content))

    async def read_office(self, path: str) -> ToolResponse:
        """Extract DOCX paragraphs/tables, XLSX rows/formulas, or PPTX slide text.
        No complex-format preservation promise."""
        from agentbuddy.runtime.office import extract

        self.control.check(reserve=True)
        result = json.dumps(extract(self.root / relative(path)), ensure_ascii=False, default=str)
        return self.response({"content": result, "truncated": False})

    async def ask_user(self, question: str) -> ToolResponse:
        """Pause the run to request a missing requirement from the user."""
        approved = await self.control.confirm({"kind": "clarification", "question": question})
        self.control.check(reserve=True)
        interaction = next(
            i
            for i in reversed(self.control.pending())
            if i["payload_json"].get("question") == question
        )
        return self.response(
            {"answered": approved, "answer": interaction["payload_json"].get("user_answer", "")}
        )

    async def list_files(self) -> ToolResponse:
        """List all project workspace files."""
        self.control.check(reserve=True)
        return self.response(
            [p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()]
        )

    async def read_file(self, path: str, offset: int = 0) -> ToolResponse:
        """Read a bounded UTF-8 text page. offset is a byte offset."""
        self.control.check(reserve=True)
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        with (self.root / relative(path)).open("rb") as stream:
            stream.seek(offset)
            data = stream.read(self.policy.read_bytes)
        return self.response(
            {"text": data.decode("utf-8", errors="replace"), "next_offset": offset + len(data)}
        )

    async def write_file(self, path: str, content: str) -> ToolResponse:
        """Create or replace a UTF-8 workspace file; original inputs and versions are immutable."""
        self.control.check(reserve=True)
        target = self.root / relative(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.response({"path": path, "bytes": target.stat().st_size})

    async def load_skill(self, skill_id: str) -> ToolResponse:
        """Load the full instructions of one available Skill when needed."""
        self.control.check(reserve=True)
        if skill_id not in self.skills:
            raise DomainError("SKILL_UNAVAILABLE", "Skill 不在本轮能力清单中")
        package_root = self.db.path.parent / "skills" / self.skills[skill_id]["package_id"]
        if manifest(package_root) != self.skills[skill_id]["files"]:
            raise DomainError("SKILL_CHANGED", "Skill 包完整性检查失败")
        self.control.event("skill.loaded", {"skill_id": skill_id})
        self.loaded.add(skill_id)
        return self.resource(
            "skill:" + skill_id, self.skills[skill_id], self.skills[skill_id]["package_id"]
        )

    async def read_skill_file(self, skill_id: str, path: str, offset: int = 0) -> ToolResponse:
        """Read an installed Skill reference file after loading its instructions."""
        self.control.check(reserve=True)
        if skill_id not in self.loaded or offset < 0:
            raise DomainError("SKILL_UNAVAILABLE", "请先加载 Skill，并使用非负偏移")
        root = self.db.path.parent / "skills" / self.skills[skill_id]["package_id"]
        if manifest(root) != self.skills[skill_id]["files"]:
            raise DomainError("SKILL_CHANGED", "Skill 包版本已变化，请重新开始任务")
        with (root / relative(path)).open("rb") as source:
            source.seek(offset)
            content = source.read(self.policy.read_bytes)
        from agentbuddy.runtime.runtime_store import fingerprint

        return self.resource(
            "skill-file:" + skill_id + ":" + path + ":" + str(offset),
            {
                "text": content.decode("utf-8", errors="replace"),
                "next_offset": offset + len(content),
            },
            fingerprint(self.skills[skill_id]["files"]),
        )

    async def run_script(self, skill_id: str, script: str, args: list[str]) -> ToolResponse:
        """Execute a loaded Skill's declared Python script in the project workspace."""
        if skill_id not in self.loaded or script not in self.skills[skill_id].get("scripts", []):
            raise DomainError("SCRIPT_UNAVAILABLE", "请先加载 Skill，并选择其声明的脚本")
        path = (
            self.db.path.parent / "skills" / self.skills[skill_id]["package_id"] / relative(script)
        )

        if self.skills[skill_id].get("effects", {}).get(script, "workspace") != "workspace":
            approved = await self.control.confirm(
                {"kind": "script", "skill_id": skill_id, "script": script, "args": args}
            )
            if not approved:
                return self.response({"executed": False, "reason": "用户拒绝"})

        async def execute():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(path),
                *args,
                cwd=self.root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            output = bytearray()
            try:
                while data := await process.stdout.read(8192):
                    if len(output) + len(data) > self.policy.result_max_bytes:
                        raise ProviderFailure("result_too_large", "脚本输出超出上限，执行已停止")
                    output.extend(data)
                code = await process.wait()
            finally:
                if process.returncode is None:
                    if os.name == "nt":
                        killer = await asyncio.create_subprocess_exec(
                            "taskkill",
                            "/PID",
                            str(process.pid),
                            "/T",
                            "/F",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await killer.wait()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            log = self.db.path.parent / "projects" / self.claimed["project_id"] / "logs"
            log.mkdir(parents=True, exist_ok=True)
            from uuid import uuid4

            name = f"{uuid4()}.log"
            reference = self.store.result(output.decode(errors="replace"), self.policy)
            (log / name).write_bytes(output)
            return {
                "exit_code": code,
                "output": output[:1024].decode(errors="replace"),
                "result": reference,
                "stored_complete": True,
                "log": name,
                "log_url": f"/runs/{self.claimed['id']}/logs/{name}",
                "truncated": len(output) > 1024,
            }

        return self.response(await self.control.guard(execute()))

    async def call_connector(self, connector_id: str, tool: str, arguments: dict) -> ToolResponse:
        """Call an MCP tool; external writes require confirmation of exact arguments."""
        connectors = self.claimed["snapshot_json"].get("capabilities", {}).get("connectors", [])
        connector = next((c for c in connectors if c["id"] == connector_id), None)
        if not connector:
            raise DomainError("CONNECTOR_UNAVAILABLE", "连接器不在本轮配置中")
        config = connector["config_json"]
        call_key = json.dumps([connector_id, tool, arguments], sort_keys=True)
        if call_key in self.uncertain:
            raise ProviderFailure(
                "external_result_unknown", "此前操作结果未知，禁止自动重试，请先核对外部结果"
            )
        while True:
            effect = config.get("effects", {}).get(tool, "external_write")
            if effect != "read":
                approved = await self.control.confirm(
                    {
                        "kind": "external_write",
                        "connector": config.get("name"),
                        "revision": connector["revision"],
                        "tool": tool,
                        "arguments": arguments,
                    }
                )
                if not approved:
                    return self.response({"executed": False, "reason": "用户拒绝"})
            entered = False

            async def execute():
                nonlocal entered
                if not config.get("enabled"):
                    raise DomainError("CONNECTOR_DISABLED", "连接器未启用")
                async with mcp_session(config) as session:
                    available = await session.list_tools()
                    if tool not in {t.name for t in available.tools}:
                        raise DomainError("TOOL_UNAVAILABLE", "连接器中没有所选工具")
                    entered = True
                    result = await session.call_tool(tool, arguments)
                    if result.is_error:
                        raise RuntimeError("MCP tool reported an error")
                    return result.model_dump(mode="json")

            try:
                return self.response(await self.control.guard(execute()))
            except Exception:
                self.control.check()
                if entered:
                    if effect != "read":
                        self.uncertain.add(call_key)
                        raise ProviderFailure(
                            "external_result_unknown", "外部操作结果未知，请核对后再发起新任务"
                        ) from None
                    raise ProviderFailure("tool_failed", "工具执行失败，请检查连接器") from None
                approved = await self.control.confirm(
                    {
                        "kind": "dependency",
                        "connector_id": connector_id,
                        "message": "必需连接器不可用，请在能力目录修复后确认重试",
                        "tool": tool,
                        "arguments": arguments,
                    }
                )
                if not approved:
                    return self.response({"executed": False, "reason": "依赖未补齐"})
                connector = Capabilities(self.db).get(connector_id)
                config = connector["config_json"]

import asyncio
import logging
import time

from agentscope.agent import Agent, ContextConfig, ModelConfig, ReActConfig
from agentscope.event import TextBlockDeltaEvent
from agentscope.message import Msg, TextBlock

from agentbuddy.catalog.service import Catalog, DomainError
from agentbuddy.providers.native import NativeModel, ProviderFailure, probe
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.delivery import plan_delivery
from agentbuddy.runtime.files import Files, manifest
from agentbuddy.runtime.history import compact_history
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.tools import RunTools


class Executor:
    def __init__(self, db, model_factory=NativeModel):
        self.db = db
        self.catalog = Catalog(db)
        self.runs = RunRepository(db)
        self.factory = model_factory

    async def model_probe(self, snapshot):
        secret = await asyncio.to_thread(self.catalog.secret, snapshot["provider"]["credential_id"])
        model = self.factory(snapshot, secret)
        try:
            try:
                result = await asyncio.wait_for(probe(model), 50)
            except (ProviderFailure, TimeoutError) as exc:
                await asyncio.to_thread(self.catalog.record_test, snapshot, False)
                return {
                    "passed": False,
                    "error": {
                        "code": getattr(exc, "code", "timeout"),
                        "message": str(exc) or "模型测试超时",
                    },
                }
            await asyncio.to_thread(self.catalog.record_test, snapshot, True)
            return {"passed": True, "capabilities": result}
        finally:
            await model.close()

    def make_agent(self, snapshot, model, run_tools, coordinator=False):
        capabilities = snapshot.get("capabilities", {})
        expert = (capabilities.get("expert") or {}).get("config_json", {})
        candidates = [
            {
                "id": c["capabilities"]["expert"]["id"],
                "name": c["capabilities"]["expert"]["config_json"]["name"],
                "role": c["capabilities"]["expert"]["config_json"].get("prompt", ""),
            }
            for c in snapshot.get("candidates", [])
        ]
        prompt = (
            "文件操作必须使用真实工具；先 load_skill 再使用相应办公或脚本工具。"
            "不要声称未执行的检查。任务完成后的总结保持简短。逐一读取核对最终交付文件，确保用户指定的日期、金额、名称等关键信息没有遗漏或矛盾；多份文档不能只核对其中一份。资料以外的背景、角色、指标和功能必须标为假设或建议，不能写成已知事实；只生成配置或文档时不能宣称程序功能已经实现。"
        )
        if coordinator:
            prompt += (
                "你是固定总控。按用户目标从候选专家组建单层团队，调用 delegate_tasks。"
                "独立任务并行，依赖任务标 depends_on。检查子任务失败并在预算内重新分派"
                "（replaces 指定失败 UUID）；显式解决文件冲突后汇总交付。"
            )
        else:
            prompt += expert.get("prompt", "")
        prompt += str(
            {
                "skills": [
                    {
                        "id": s["id"],
                        "name": s["config_json"].get("name"),
                        "description": s["config_json"].get("description", ""),
                    }
                    for s in capabilities.get("skills", [])
                ],
                "connectors": [
                    {
                        "id": c["id"],
                        "name": c["config_json"].get("name"),
                        "tools": c["config_json"].get("tools", []),
                    }
                    for c in capabilities.get("connectors", [])
                ],
                "collaboration_candidates": candidates,
            }
        )
        return Agent(
            name="固定总控" if coordinator else expert.get("name", "通用助手"),
            system_prompt=prompt,
            model=model,
            toolkit=run_tools.toolkit(),
            model_config=ModelConfig(max_retries=0),
            context_config=ContextConfig(compression_tool_enabled=False),
            react_config=ReActConfig(max_iters=50, interruption_raise_cancelled_error=True),
        )

    async def child_reply(self, parent, snapshot, instruction, root, control):
        secret = await asyncio.to_thread(self.catalog.secret, snapshot["provider"]["credential_id"])
        model = self.factory(snapshot, secret)
        model.control = control
        child = {**parent, "snapshot_json": snapshot}
        tools = RunTools(self.db, child, control, root)
        try:
            agent = self.make_agent(snapshot, model, tools)
            final = None
            async for event in agent.reply_stream(
                [Msg(name="user", role="user", content=[TextBlock(text=instruction)])],
                yield_final_msg=True,
            ):
                if isinstance(event, Msg):
                    final = event
            if tools.uncertain:
                raise ProviderFailure("external_result_unknown", "子专家外部操作结果未知")
            if final is None or final.error or str(final.finished_reason) != "completed":
                raise ProviderFailure("agent_incomplete", "子专家未完成")
            return "".join(b.text for b in final.content if isinstance(b, TextBlock))
        finally:
            await model.close()

    async def execute(self, claimed):
        snapshot = claimed["snapshot_json"]
        secret = await asyncio.to_thread(self.catalog.secret, snapshot["provider"]["credential_id"])
        model = self.factory(snapshot, secret)
        control = Control(self.db, claimed["id"])
        model.control = control
        files = Files(self.db)
        pending = ""
        flushed_at = time.monotonic()
        try:
            root = await asyncio.to_thread(files.workspace, claimed)
            control.lane("active")
            run_tools = RunTools(self.db, claimed, control, root)
            from agentbuddy.runtime.collaboration import Supervisor

            supervisor = None
            if snapshot.get("mode") in ("team", "expert") and snapshot.get("candidates"):

                async def child_runner(child_snapshot, instruction, child_root, lane):
                    return await self.child_reply(
                        claimed, child_snapshot, instruction, child_root, lane
                    )

                supervisor = Supervisor(self.db, claimed, root, child_runner)
                run_tools.supervisor = supervisor
            history = await asyncio.to_thread(self.runs.history, claimed["project_id"])
            # Failed turns stay visible but are excluded from model context.
            previous_runs = await asyncio.to_thread(self.runs.project_runs, claimed["project_id"])
            valid = {r["id"] for r in previous_runs if r["status"] == "succeeded"} | {claimed["id"]}
            history = [m for m in history if m["run_id"] in valid]
            history = await compact_history(
                history, model, snapshot["model"]["max_output_tokens"], control
            )
            agent = self.make_agent(snapshot, model, run_tools, snapshot.get("mode") == "team")
            inputs = [
                Msg(name=m["role"], role=m["role"], content=[TextBlock(text=m["content"])])
                for m in history
            ]
            final = None
            async with asyncio.TaskGroup() as group:
                execution_task = asyncio.current_task()

                async def monitor():
                    while True:
                        await asyncio.sleep(0.2)
                        try:
                            await asyncio.to_thread(control.check)
                        except ProviderFailure as exc:
                            await asyncio.to_thread(
                                self.runs.finish,
                                claimed,
                                error={"code": exc.code, "message": str(exc)},
                            )
                            execution_task.cancel()
                            return

                watcher = group.create_task(monitor())
                baseline = {f["path"]: f["sha256"] for f in manifest(root)}
                delivery = await plan_delivery(model, inputs, root)
                control.event("delivery.planned", delivery.model_dump())
                inputs.append(
                    Msg(
                        name="platform_delivery",
                        role="user",
                        content=[
                            TextBlock(
                                text="本轮交付契约（结束前由程序核对，不能省略）："
                                + delivery.model_dump_json()
                            )
                        ],
                    )
                )
                async for event in agent.reply_stream(inputs, yield_final_msg=True):
                    if isinstance(event, TextBlockDeltaEvent):
                        pending += event.delta
                        if len(pending) >= 4096 or time.monotonic() - flushed_at >= 0.25:
                            await asyncio.to_thread(self.runs.append, claimed, pending)
                            pending, flushed_at = "", time.monotonic()
                    elif isinstance(event, Msg):
                        final = event
                if pending:
                    await asyncio.to_thread(self.runs.append, claimed, pending)
                    pending = ""
                if final is None or final.error or str(final.finished_reason) != "completed":
                    raise ProviderFailure("agent_incomplete", "智能体未完整完成本轮回答")
                text = "".join(b.text for b in final.content if isinstance(b, TextBlock))
                if not text:
                    raise ProviderFailure("empty_response", "模型没有返回文本回答")
                if run_tools.uncertain:
                    raise ProviderFailure(
                        "external_result_unknown", "外部操作结果未知，请核对后再继续"
                    )
                if supervisor and (
                    supervisor.conflicts
                    or supervisor.failed
                    or (snapshot.get("mode") == "team" and not supervisor.dispatched)
                ):
                    raise ProviderFailure(
                        "collaboration_incomplete", "协作仍有未完成任务或文件冲突，未发布成功版本"
                    )
                control.check()
                from agentbuddy.runtime.office import Office

                delivery.verify(root, baseline)
                control.event(
                    "delivery.checked",
                    {
                        "status": "passed",
                        "content_checks": [c.model_dump() for c in delivery.content_checks],
                        "scope": "文件存在、变更及显式文字要求；业务结论仍需审阅",
                    },
                )
                await Office(root).validate_all()
                version = await asyncio.to_thread(files.stage_version, claimed, root)
                await asyncio.to_thread(self.runs.finish, claimed, text=text, version=version)
                watcher.cancel()
        except asyncio.CancelledError:
            if self.runs.get(claimed["id"])["status"] == "running":
                raise
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            logging.getLogger(__name__).exception("Run failed: %s", claimed["id"])
            if pending:
                await asyncio.to_thread(self.runs.append, claimed, pending)
            error = {
                "code": getattr(exc, "code", "run_failed"),
                "message": str(exc)
                if isinstance(exc, (ProviderFailure, DomainError, ValueError))
                else "执行失败或超时，请检查模型连接",
            }
            await asyncio.to_thread(self.runs.finish, claimed, error=error)
        finally:
            await model.close()

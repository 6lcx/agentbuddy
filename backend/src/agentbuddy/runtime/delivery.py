"""Freeze a turn's delivery requirements before execution; verify actual files afterwards."""

import json
from typing import Literal

from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.tool import ToolChoice
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentbuddy.catalog.service import DomainError
from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.files import manifest, relative


class ContentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    expected: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["answer", "files"]
    required: list[str]
    deleted: list[str]
    changed: list[str]
    content_checks: list[ContentCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_paths(self):
        for check in self.content_checks:
            check.path = relative(check.path)
            if check.path not in self.required:
                raise ValueError("内容检查必须指向交付文件")
        for paths in (self.required, self.deleted, self.changed):
            try:
                paths[:] = [relative(path) for path in paths]
            except DomainError as exc:
                raise ValueError(str(exc)) from exc
        if set(self.required) & set(self.deleted) or not set(self.changed) <= set(self.required):
            raise ValueError("交付路径要求互相冲突")
        if self.kind == "files" and not (self.required or self.deleted):
            raise ValueError("文件任务必须包含交付路径")
        if self.kind == "answer" and (self.required or self.deleted or self.changed):
            raise ValueError("问答不能包含文件交付要求")
        return self

    def verify(self, root, baseline):
        current = {f["path"]: f["sha256"] for f in manifest(root)}
        missing = [p for p in self.required if p not in current]
        retained = [p for p in self.deleted if p in current]
        unchanged = [p for p in self.changed if p in current and current[p] == baseline.get(p)]
        if missing or retained or unchanged:
            raise ProviderFailure(
                "delivery_incomplete",
                f"交付未完成：缺失文件 {missing}；未删除 {retained}；未修改 {unchanged}",
            )
        for check in self.content_checks:
            path = root / check.path
            if path.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                from agentbuddy.runtime.office import extract

                content = json.dumps(extract(path), ensure_ascii=False, default=str)
            else:
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeError as exc:
                    raise ProviderFailure("content_check_unsupported", check.path) from exc
            if check.expected not in content:
                raise ProviderFailure(
                    "delivery_content_missing",
                    f"{check.path} 缺少要求的内容：{check.expected}；来源：{check.source}",
                )


async def plan_delivery(model, inputs, root):
    schema = {
        "type": "function",
        "function": {
            "name": "declare_delivery",
            "description": "Declare this turn's required deliverables before execution.",
            "parameters": Delivery.model_json_schema(),
        },
    }
    prompt = (
        "content_checks 提取用户明确要求原样出现的名称、日期或文字，逐文件列出"
        "path、expected 和 source（用户原文依据）；不要推测或把可改写内容强制逐字匹配。"
        "同一事实要求出现在多个文件时逐文件声明。没有逐字要求则留空。"
        "你只负责提取最后一轮用户的交付要求，不执行任务。必须调用 declare_delivery。"
        "普通问答/读取分析只需文字回答时 kind=answer，三个列表为空；"
        "明确生成/修改/删除文件时 kind=files。required 列出必须存在的文件；"
        "deleted 列出必须删除的旧路径（重命名须同时包含新旧路径）；"
        "changed 是 required 的子集，包含明确要求新建或修改内容的文件。"
        "沿用用户指定的相对路径；未指定则选简洁合理的文件名。不要把读取的资料列为产物。"
        "不要求改动的历史文件不要放 changed。不要为未知附件虚构内容。"
        "仅提问澄清而尚不能确定产物时使用 answer。现有文件："
        + json.dumps([f["path"] for f in manifest(root)], ensure_ascii=False)
    )
    result = None
    async for result in await model(
        [Msg(name="system", role="system", content=[TextBlock(text=prompt)]), *inputs],
        tools=[schema],
        tool_choice=ToolChoice(mode="declare_delivery"),
    ):
        pass
    calls = [b for b in result.content if isinstance(b, ToolCallBlock)] if result else []
    try:
        if len(calls) != 1 or calls[0].name != "declare_delivery":
            raise ValueError("missing declaration")
        return Delivery.model_validate_json(calls[0].input)
    except ValueError as exc:
        raise ProviderFailure(
            "delivery_plan_invalid", "未能确定本轮交付要求，请补充明确要求"
        ) from exc

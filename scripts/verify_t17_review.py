"""Apply human review feedback through the Agent, preserving original versions."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker
from verify_t17 import check, text


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, nargs="*")
    args = parser.parse_args()
    settings = Settings(data_dir=Path("data/validation/t17-uat"))
    db = Database(settings.database_path)
    repo, files = RunRepository(db), Files(db)
    rows = json.loads(
        (settings.data_dir / "uat-results.json").read_text(encoding="utf-8")
    )
    feedback = {
        2: "请修改docs/spec.md：保留原始图书借阅规则及四项验收条件。审阅发现你把公司现状、统一登录、角色、日志保留1年等未提供的信息写成事实或硬性要求。请删除无依据背景，其他扩展内容标注为建议/假设/待确认，不得强加新需求。澄清14天为借出日期+14天，删除含糊的借出时间计入说法。",
        4: "请修改README.md，保持config/app.json完全不变。审阅要求：limits.calls表示单个任务最大调用次数，limits.seconds表示单个任务执行时长上限，不是每30分钟限流；明确本项目仅交付配置示例和说明，未实现运行程序。删除或改为实现建议：自动建库、读取配置挂载路由、返回429等尚不存在的功能。保留环境变量密钥说明。",
    }
    feedback[7] = (
        "请修改index.html，styles.css保持不变：保留星河读书会、2026-12-01、杭州图书馆、30人、reading@example.com及响应式布局。人工审阅发现第十二期、三楼读者交流区、免费、自动邮件通知均无资料依据，请删除这些断言。主题和具体日程可以作为建议，但必须清楚标注为拟定/待确认。页面明确是活动介绍草稿，不具备报名提交或发送通知功能，不要编造实际报名成功。"
    )
    feedback[8] = (
        "请读取weekly.docx的结构化元数据，使用Word编辑工具重新生成weekly.docx，完整保留所有原内容和表格，仅应用平台更新后的中文标题字体。不要额外增加背景或事项，检查渲染通过后交付。"
    )
    report_path = settings.data_dir / "manual-review.json"
    results = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else []
    )
    try:
        for number, prompt in feedback.items():
            if args.only and number not in args.only:
                continue
            prior = next(
                r for r in reversed(rows) if r["number"] == number and r["passed"]
            )
            original = files.archive(prior["version_id"])
            result = repo.submit(
                {
                    "message": prompt,
                    "project_id": prior["project_id"],
                    "selection": {"mode": "general"},
                    "calls_limit": 80,
                    "seconds_limit": 300,
                },
                str(uuid4()),
            )
            await Worker(settings).run(once=True)
            run = repo.get(result["run_id"])
            record = {
                "number": number,
                "feedback": prompt,
                **result,
                "passed": False,
                "status": run["status"],
                "failure": run.get("failure_json"),
            }
            try:
                assert run["status"] == "succeeded", run.get("failure_json")
                version = [
                    v
                    for v in files.list(result["project_id"])
                    if v["kind"] == "version"
                ][-1]
                root = settings.data_dir / "versions" / version["id"]
                check(number, root, run, [], [])
                assert files.archive(prior["version_id"]) == original
                if number == 2:
                    value = text(root, "docs/spec.md")
                    assert "待确认" in value and "14" in value
                    assert "日志保留不少于 1 年" not in value
                elif number == 4:
                    value = text(root, "README.md")
                    assert "单个任务" in value and "未实现" in value
                    assert (root / "config/app.json").read_bytes() == (
                        Path(prior["root"]) / "config/app.json"
                    ).read_bytes()
                elif number == 7:
                    value = text(root, "index.html")
                    assert "草稿" in value and "待确认" in value
                    assert "三楼" not in value and "第十二期" not in value
                    assert (root / "styles.css").read_bytes() == (
                        Path(prior["root"]) / "styles.css"
                    ).read_bytes()
                record.update(
                    passed=True,
                    root=str(root),
                    version_id=version["id"],
                    previous_zip_sha256=hashlib.sha256(original).hexdigest(),
                    answer=run["partial_text"],
                )
            except Exception as exc:  # noqa: BLE001 - retain review evidence
                record["error"] = str(exc)
            results.append(record)
            (settings.data_dir / "manual-review.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"REVIEW {number} passed={record['passed']}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

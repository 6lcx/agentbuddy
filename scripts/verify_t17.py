"""15 real DeepSeek tasks. This harness supplies inputs, never writes Agent deliverables."""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.schema import run_controls, run_events, subtasks
from agentbuddy.runtime.files import Files
from agentbuddy.runtime.office import extract
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.tools import discover
from agentbuddy.worker.service import Worker
from dotenv import dotenv_values
from sqlalchemy import select
from verify_deepseek import configure

CASES = [
    (
        "会议纪要",
        "根据附件 notes.txt 整理 docs/minutes.md 和 actions.csv。纪要包含决定、风险、待办；CSV列 task,owner,due，恰好两条待办。不得编造已完成事项。",
        ["docs/minutes.md", "actions.csv"],
    ),
    (
        "需求与验收标准",
        "为内部图书借阅工具编写 docs/spec.md：只能借阅有库存图书，每人最多借3本，借期14天，不做支付。包含正常借阅、无库存拒绝、超过3本拒绝、归还恢复库存四项可执行验收条件；明确范围外支付。",
        ["docs/spec.md"],
    ),
    (
        "销售数据汇总",
        "读取 sales.csv，按 region 汇总 quantity*price，生成 summary.csv（列region,revenue，每地区一行，不加总计行）和 analysis.md，说明总收入及最高收入地区，不引入外部数据。",
        ["summary.csv", "analysis.md"],
    ),
    (
        "环境配置整理",
        "生成 config/app.json 和 README.md。JSON顶层必须为 name=agentbuddy-demo、api_prefix=/api/v1、database={type:sqlite,path:data/app.db}、limits={calls:120,seconds:1800}。README解释各配置并说明密钥使用环境变量，文件不能包含真实密钥。",
        ["config/app.json", "README.md"],
    ),
    (
        "Python 数据处理工具",
        "生成 app.py 和 README.md：Python标准库CLI，使用 --input 输入CSV、--output 输出JSON，CSV列category,amount，按category累计amount（支持负数退款），JSON为类别到数值的对象。非法数值应非零退出。实际运行示例验证。",
        ["app.py", "README.md"],
    ),
    (
        "为已有代码补测试",
        "读取已有app.py，新增 test_app.py，使用标准库unittest，通过subprocess验证CLI：重复类别累加、负数退款、非法数值非零退出。至少3个测试。执行 python -m unittest -v，通过后交付；app.py和README.md不改。",
        ["test_app.py"],
    ),
    (
        "静态活动页面",
        "生成 index.html 和 styles.css，中文星河读书会活动报名介绍页，活动日期2026-12-01，地点杭州图书馆，名额30人。HTML引用styles.css，包含活动介绍、日程、联系邮箱reading@example.com；响应式布局，不使用外部CDN，不做实际提交或发送。",
        ["index.html", "styles.css"],
    ),
    (
        "Word 项目周报",
        "生成 weekly.docx，标题星河项目周报，三节：本周完成、风险、下周计划。完成需求评审，接口联调尚未完成且负责人李明；下周2026-11-17完成联调。包含任务/负责人/状态三列表格，不能把未完成写成已完成。",
        ["weekly.docx"],
    ),
    (
        "Excel 费用预算",
        "生成 budget.xlsx，工作表预算，A1项目B1金额；第2-4行依次开发12000、测试3000、设计5000；A5合计，B5使用SUM(B2:B4)公式且结果20000；添加各项目金额图表。",
        ["budget.xlsx"],
    ),
    (
        "PPT 项目汇报",
        "生成 launch.pptx，恰好3页中文：项目目标、预算分配、下一步。项目星河笔记，总预算20000，开发12000测试3000设计5000；预算页需图表；下一步2026-11-17验收。排版清楚，不增加续页。",
        ["launch.pptx"],
    ),
    (
        "上传办公资料提取",
        "读取上传的 source.docx、source.xlsx、source.pptx，生成 extraction.md，逐文件注明提取的信息和来源：文档项目编号、表格总额、演示里的发布日期。不修改原附件，不联网。",
        ["extraction.md"],
    ),
    (
        "专家团并行策划",
        "自动协作，至少两个专家并行完成不同文件：brief.md写活动方案，channels.csv列channel,budget两行video=12000、community=8000，copy.md写发布草稿。项目星河笔记，日期2026-12-01，总预算20000。三个文件预算一致，CSV只写两个渠道行。只交付文件，不实际发布。",
        ["brief.md", "channels.csv", "copy.md"],
    ),
    (
        "跨文件持续修改",
        "读取已有brief.md、channels.csv、copy.md，把总预算同步改为30000，video改18000，community改12000。项目星河笔记和日期2026-12-01不变。CSV保持原列和两行。三个文件都要修改。",
        ["brief.md", "channels.csv", "copy.md"],
    ),
    (
        "协作整理项目目录",
        "自动协作，请开发专家把上传的 old.txt 重命名为 docs/current.txt，内容必须保持不变，删除 old.txt 和 obsolete.txt；其他专家生成 README.md说明目录。keep.txt必须不变。通过实际工具或脚本完成，不要只说明操作步骤。",
        ["docs/current.txt", "README.md"],
    ),
    (
        "连接器读取归档",
        "调用已启用的本地查询连接器 echo，参数text为订单ORD-20261110，状态已发货，金额168。将真实返回结果保存到 order.md，包含订单号、状态、金额；必须实际调用连接器，不允许直接根据本段文字假装查询。",
        ["order.md"],
    ),
]


def archive_inputs(mapping):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in mapping.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def inputs_for(number):
    if number == 1:
        return {
            "notes.txt": "会议2026-11-10。决定首版不做支付。李明在2026-11-12完成接口文档，王芳在2026-11-13完成测试清单。风险：第三方接口尚未联通。两个待办均未完成。"
        }
    if number == 3:
        return {
            "sales.csv": "region,quantity,price\n华东,2,100\n华南,3,50\n华东,1,80\n华南,2,25\n"
        }
    if number == 14:
        return {
            "old.txt": "CURRENT_CONTENT_42",
            "obsolete.txt": "obsolete",
            "keep.txt": "KEEP_17",
        }
    if number == 11:
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation

        doc = Document()
        doc.add_paragraph("项目编号 PROJECT-2048")
        word = io.BytesIO()
        doc.save(word)
        book = Workbook()
        book.active.append(["总额", 24680])
        sheet = io.BytesIO()
        book.save(sheet)
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = "发布日期 2026-12-18"
        slides = io.BytesIO()
        deck.save(slides)
        return {
            "source.docx": word.getvalue(),
            "source.xlsx": sheet.getvalue(),
            "source.pptx": slides.getvalue(),
        }
    return {}


def text(root, path):
    return (root / path).read_text(encoding="utf-8-sig")


def contains(value, *items):
    for item in items:
        assert item in value, f"missing content: {item}"


def check(number, root, run, tasks, events):
    for path in CASES[number - 1][2]:
        assert (root / path).is_file() and (root / path).stat().st_size > 0, path
    if number == 1:
        contains(text(root, "docs/minutes.md"), "李明", "王芳", "支付", "接口")
        rows = list(csv.DictReader(io.StringIO(text(root, "actions.csv"))))
        assert len(rows) == 2
        assert {(r["owner"], r["due"]) for r in rows} == {
            ("李明", "2026-11-12"),
            ("王芳", "2026-11-13"),
        }
    elif number == 2:
        contains(text(root, "docs/spec.md"), "14", "3", "库存", "归还", "支付", "验收")
    elif number == 3:
        rows = list(csv.DictReader(io.StringIO(text(root, "summary.csv"))))
        assert len(rows) == 2 and {r["region"]: float(r["revenue"]) for r in rows} == {
            "华东": 280,
            "华南": 200,
        }
        contains(text(root, "analysis.md"), "480", "华东")
    elif number == 4:
        assert json.loads(text(root, "config/app.json")) == {
            "name": "agentbuddy-demo",
            "api_prefix": "/api/v1",
            "database": {"type": "sqlite", "path": "data/app.db"},
            "limits": {"calls": 120, "seconds": 1800},
        }
        contains(text(root, "README.md"), "环境变量")
    elif number == 5:
        # Execute the delivered code in a separate verification directory, never modify its version.
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, target = directory / "input.csv", directory / "result.json"
            source.write_text(
                "category,amount\nfood,10.5\nfood,-2\ntravel,3\n", encoding="utf-8"
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(root / "app.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(target),
                ],
                check=False,
                capture_output=True,
                timeout=20,
            )
            assert proc.returncode == 0, proc.stderr.decode(errors="replace")
            assert json.loads(target.read_text()) == {"food": 8.5, "travel": 3}
            source.write_text("category,amount\nfood,invalid\n", encoding="utf-8")
            bad = subprocess.run(
                [
                    sys.executable,
                    str(root / "app.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(target),
                ],
                check=False,
                capture_output=True,
                timeout=20,
            )
            assert bad.returncode != 0
    elif number == 6:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            shutil.copytree(root, temp, dirs_exist_ok=True)
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "-v"],
                cwd=temp,
                check=False,
                capture_output=True,
                timeout=30,
            )
            output = proc.stderr.decode(errors="replace")
            assert proc.returncode == 0 and "OK" in output, output
            import re

            count = re.search(r"Ran (\d+) tests?", output)
            assert count and int(count[1]) >= 3, output
    elif number == 7:
        contains(
            text(root, "index.html"),
            "styles.css",
            "星河读书会",
            "2026-12-01",
            "杭州图书馆",
            "30",
            "reading@example.com",
        )
        contains(text(root, "styles.css"), "@media")
    elif number == 8:
        value = json.dumps(extract(root / "weekly.docx"), ensure_ascii=False)
        contains(value, "星河项目周报", "李明", "2026-11-17", "风险", "下周")
        from docx import Document

        assert Document(root / "weekly.docx").tables
    elif number == 9:
        from openpyxl import load_workbook

        values = load_workbook(root / "budget.xlsx", data_only=True)
        assert values["预算"]["B5"].value == 20000
        values.close()
        formulas = load_workbook(root / "budget.xlsx")
        assert formulas["预算"]["B5"].value.replace(" ", "").upper() == "=SUM(B2:B4)"
        assert formulas["预算"]._charts
        assert formulas["预算"]._charts[0].series[0].val.numRef.f.endswith("$B$2:$B$4")
        formulas.close()
    elif number == 10:
        from pptx import Presentation

        deck = Presentation(root / "launch.pptx")
        assert len(deck.slides) == 3
        value = json.dumps(extract(root / "launch.pptx"), ensure_ascii=False)
        contains(value.replace(",", ""), "星河笔记", "20000", "2026-11-17")
        assert any(shape.has_chart for shape in deck.slides[1].shapes)
    elif number == 11:
        contains(
            text(root, "extraction.md"),
            "PROJECT-2048",
            "24680",
            "2026-12-18",
            "source.docx",
            "source.xlsx",
            "source.pptx",
        )
    elif number in (12, 13):
        rows = list(csv.DictReader(io.StringIO(text(root, "channels.csv"))))
        expected = (
            {"video": 12000, "community": 8000}
            if number == 12
            else {"video": 18000, "community": 12000}
        )
        assert (
            len(rows) == 2
            and {r["channel"]: float(r["budget"]) for r in rows} == expected
        )
        for path in ("brief.md", "copy.md"):
            contains(
                text(root, path), "星河笔记", "2026-12-01", str(sum(expected.values()))
            )
        if number == 12:
            assert len(tasks) >= 2 and any(
                a["started_at"] < b["finished_at"]
                and b["started_at"] < a["finished_at"]
                for a in tasks
                for b in tasks
                if a["id"] != b["id"]
            )
    elif number == 14:
        assert text(root, "docs/current.txt") == "CURRENT_CONTENT_42"
        assert text(root, "keep.txt") == "KEEP_17"
        assert not (root / "old.txt").exists() and not (root / "obsolete.txt").exists()
        assert any(t["result_json"].get("deleted") for t in tasks if t["result_json"])
    elif number == 15:
        contains(text(root, "order.md"), "ORD-20261110", "已发货", "168")
        assert any(
            e["type"] == "tool.completed"
            and e["payload_json"].get("name") == "call_connector"
            for e in events
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--only", type=int, nargs="*")
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)
    report_path = settings.data_dir / "uat-results.json"
    if settings.database_path.exists():
        db = Database(settings.database_path)
    else:
        db, _ = await configure(
            settings,
            dotenv_values(args.source_env),
            max_output_tokens=8192,
            context_window=131072,
        )
    Capabilities(db).seed_presets()
    registry = Capabilities(db)
    if not any(
        c["config_json"]["name"] == "验收本地查询" for c in registry.list("connector")
    ):
        config = {
            "name": "验收本地查询",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(Path("backend/tests/mcp_fixture.py").resolve())],
            "enabled": True,
            "effects": {"echo": "read"},
        }
        config["tools"] = await discover(config)
        registry.save("connector", config)
    reports = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else []
    )
    repo, files = RunRepository(db), Files(db)
    try:
        for number, (name, prompt, expected) in enumerate(CASES, 1):
            if args.only and number not in args.only:
                continue
            draft = str(uuid4())
            payload = {
                "message": prompt,
                "selection": {"mode": "team" if number in (12, 14) else "general"},
                "calls_limit": 120,
                "seconds_limit": 420,
            }
            if number == 15:
                payload["selection"]["add_connectors"] = [
                    c["id"]
                    for c in registry.list("connector")
                    if c["config_json"]["name"] == "验收本地查询"
                ]
            predecessor = {6: 5, 13: 12}.get(number)
            before = {}
            if predecessor:
                previous = next(
                    (
                        r
                        for r in reversed(reports)
                        if r["number"] == predecessor and r["passed"]
                    ),
                    None,
                )
                if previous is None:
                    reports.append(
                        {
                            "number": number,
                            "name": name,
                            "prompt": prompt,
                            "passed": False,
                            "status": "blocked",
                            "check_error": "前置任务未通过",
                        }
                    )
                    report_path.write_text(
                        json.dumps(reports, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        f"BLOCKED {number:02d}: prerequisite {predecessor}", flush=True
                    )
                    continue
                payload["project_id"] = previous["project_id"]
                before = {
                    v["id"]: hashlib.sha256(files.archive(v["id"])).hexdigest()
                    for v in files.list(previous["project_id"])
                    if v["kind"] == "version"
                }
            inputs = inputs_for(number)
            if inputs:
                asset = files.upload("inputs.zip", archive_inputs(inputs), draft)
                payload.update(upload_ids=[asset["id"]], draft_id=draft)
            started = time.monotonic()
            result = repo.submit(payload, str(uuid4()))
            print(f"START {number:02d} {name} {result['run_id']}", flush=True)
            await Worker(settings).run(once=True)
            run = repo.get(result["run_id"])
            record = {
                "number": number,
                "name": name,
                "prompt": prompt,
                **result,
                "status": run["status"],
                "seconds": round(time.monotonic() - started, 2),
                "passed": False,
                "failure": run.get("failure_json"),
                "answer": run.get("partial_text", ""),
            }
            with db.engine.connect() as conn:
                tasks = [
                    dict(r)
                    for r in conn.execute(
                        select(subtasks).where(subtasks.c.run_id == result["run_id"])
                    ).mappings()
                ]
                events = [
                    dict(r)
                    for r in conn.execute(
                        select(run_events).where(
                            run_events.c.run_id == result["run_id"]
                        )
                    ).mappings()
                ]
                record["calls"] = conn.scalar(
                    select(run_controls.c.calls_used).where(
                        run_controls.c.run_id == result["run_id"]
                    )
                )
            record["tasks"] = tasks
            record["delivery"] = [
                e["payload_json"] for e in events if e["type"] == "delivery.planned"
            ]
            try:
                assert run["status"] == "succeeded", run.get("failure_json")
                versions = [
                    v
                    for v in files.list(result["project_id"])
                    if v["kind"] == "version"
                ]
                assert versions and versions[-1]["run_id"] == result["run_id"], (
                    "no new version"
                )
                version = versions[-1]
                root = settings.data_dir / "versions" / version["id"]
                record["version_id"], record["root"] = version["id"], str(root)
                check(number, root, run, tasks, events)
                with zipfile.ZipFile(
                    io.BytesIO(files.archive(version["id"]))
                ) as archive:
                    assert (
                        set(expected) <= set(archive.namelist())
                        and archive.testzip() is None
                    )
                for identifier, digest in before.items():
                    assert (
                        hashlib.sha256(files.archive(identifier)).hexdigest() == digest
                    )
                if number == 6:
                    old_root = settings.data_dir / "versions" / next(reversed(before))
                    for path in ("app.py", "README.md"):
                        assert (root / path).read_bytes() == (
                            old_root / path
                        ).read_bytes()
                for path, content in inputs.items():
                    original = settings.data_dir / "uploads" / asset["id"] / path
                    assert original.read_bytes() == (
                        content.encode() if isinstance(content, str) else content
                    )
                record["passed"] = True
            except Exception as exc:  # noqa: BLE001 - retain each failed acceptance case
                record["check_error"] = str(exc) or type(exc).__name__
            reports.append(record)
            report_path.write_text(
                json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"RESULT {number:02d} passed={record['passed']} seconds={record['seconds']} error={record.get('check_error', '')}",
                flush=True,
            )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Generate persistent, real Office render evidence on the current host."""

import argparse
import asyncio
import json
from pathlib import Path

from agentbuddy.runtime.office import Office


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    office = Office(args.output)
    samples = [
        (
            "word",
            "report.docx",
            {
                "title": "项目验收报告",
                "sections": [
                    {
                        "heading": "中文与表格",
                        "paragraphs": ["Linux 实际渲染验证。"],
                        "tables": [[["项目", "数量"], ["开发", 12], ["测试", 8]]],
                    }
                ],
            },
        ),
        (
            "excel",
            "budget.xlsx",
            {
                "sheets": [
                    {
                        "name": "预算",
                        "rows": [["项目", "金额"], ["开发", 12], ["测试", 8], ["总计"]],
                        "formulas": [
                            {"cell": "B4", "formula": "=SUM(B2:B3)", "expected": 20}
                        ],
                        "chart": True,
                    }
                ]
            },
        ),
        (
            "powerpoint",
            "slides.pptx",
            {
                "slides": [
                    {
                        "title": "项目验收",
                        "bullets": [
                            "验证中文内容完整、长段落自动分页和图表可读。" * 25
                        ],
                        "chart": {
                            "categories": ["开发", "测试"],
                            "series": [{"name": "预算", "values": [12, 8]}],
                        },
                    }
                ]
            },
        ),
    ]
    results = []
    for kind, name, content in samples:
        office.generate(kind, name, content)
        report = await office.validate(name)
        results.append(
            {"path": name, "pages": len(report["pages"]), "checks": report["checks"]}
        )
    (args.output / "evidence.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

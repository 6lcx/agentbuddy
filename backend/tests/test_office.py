import pytest

from agentbuddy.runtime.office import Office, extract, renderer


def test_templates_match_content_contract_and_are_independent():
    from agentbuddy.runtime.office_models import CONTENT_MODELS
    from agentbuddy.runtime.office_templates import TEMPLATES, office_template

    for kind in TEMPLATES:
        content = office_template(kind)
        CONTENT_MODELS[kind].model_validate(content)
        content.clear()
        assert office_template(kind)


@pytest.mark.asyncio
async def test_real_office_generation_recalculation_render_and_edit(tmp_path):
    assert renderer(), "Install LibreOffice before Office integration tests"
    office = Office(tmp_path)
    examples = [
        (
            "word",
            "report.docx",
            {
                "title": "项目报告",
                "sections": [
                    {
                        "heading": "执行结果",
                        "paragraphs": ["中文内容可读取，文件版本可以修改。"],
                        "tables": [[["项目", "数量"], ["任务", "3"]]],
                    }
                ],
            },
        ),
        (
            "excel",
            "data.xlsx",
            {
                "sheets": [
                    {
                        "name": "预算",
                        "rows": [["项目", "金额"], ["开发", 12], ["测试", 8], ["总计"]],
                        "formulas": [{"cell": "B4", "formula": "=SUM(B2:B3)", "expected": 20}],
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
                    {"title": "项目进展", "bullets": ["开发完成", "测试通过"]},
                    {"title": "下一步", "bullets": ["验收交付"]},
                ]
            },
        ),
    ]
    for kind, name, content in examples:
        office.generate(kind, name, content)
        report = await office.validate(name)
        assert report["pages"] and office.proof(name)
        original = extract(tmp_path / name)
        assert original
        assert any("项目" in p["text"] for p in report["pages"])
        import copy

        changed = copy.deepcopy(content)
        if kind == "word":
            changed["sections"][0]["paragraphs"].append("新增验收结论")
        elif kind == "excel":
            changed["sheets"][0]["rows"][1][1] = 22
            changed["sheets"][0]["formulas"][0]["expected"] = 30
        else:
            changed["slides"][0]["title"] = "修改后项目进展"
        office.edit(name, changed)
        await office.validate(name)
        actual = extract(tmp_path / name)
        if kind == "word":
            assert "新增验收结论" in actual["paragraphs"]
            assert actual["tables"] == original["tables"]
        elif kind == "powerpoint":
            assert actual["slides"][0][0] == "修改后项目进展"
            assert actual["slides"][1][0] == "下一步"
    from openpyxl import load_workbook

    await office.validate("data.xlsx")
    book = load_workbook(tmp_path / "data.xlsx", data_only=True)
    assert book["预算"]["B4"].value == 30
    book.close()
    (tmp_path / "report.docx").write_bytes(b"corrupt")
    assert office.proof("report.docx") is None
    with pytest.raises(ValueError):
        office.edit("report.docx", {})


@pytest.mark.asyncio
async def test_bad_formula_not_publishable(tmp_path):
    office = Office(tmp_path)
    office.generate(
        "excel",
        "bad.xlsx",
        {
            "sheets": [
                {"name": "Data", "rows": [[1]], "formulas": [{"cell": "A2", "formula": "=1/0"}]}
            ]
        },
    )
    with pytest.raises(ValueError, match="formula"):
        await office.validate("bad.xlsx")
    assert office.proof("bad.xlsx") is None


def test_reject_malformed_table_before_writing(tmp_path):
    office = Office(tmp_path)
    with pytest.raises(ValueError):
        office.generate(
            "word",
            "bad.docx",
            {"title": "Bad", "sections": [{"tables": [["项目", "金额"], ["开发", 12]]}]},
        )
    assert not (tmp_path / "bad.docx").exists()


async def test_slide_chart_and_long_content_render(tmp_path):
    office = Office(tmp_path)
    office.generate(
        "powerpoint",
        "long.pptx",
        {
            "slides": [
                {
                    "title": "项目预算",
                    "bullets": ["持续验证中文长段落能够自动分页并完整保留。" * 30],
                    "chart": {
                        "categories": ["开发", "测试"],
                        "series": [{"name": "预算", "values": [12, 8]}],
                    },
                }
            ]
        },
    )
    report = await office.validate("long.pptx")
    assert len(report["pages"]) >= 3
    assert "source_text_coverage" in report["checks"]


async def test_deep_project_path_uses_short_renderer_workspace(tmp_path):
    root = tmp_path / ("project" * 12) / ("version" * 12)
    root.mkdir(parents=True)
    office = Office(root)
    office.generate(
        "word",
        "deep.docx",
        {"title": "Deep path", "sections": [{"paragraphs": ["preserved content"]}]},
    )
    report = await office.validate("deep.docx")
    assert len(str(root / report["pages"][0]["image"])) > 260
    assert "preserved content" in report["pages"][0]["text"]


async def test_wrapped_table_cell_and_cjk_title_coverage(tmp_path):
    office = Office(tmp_path)
    office.generate(
        "word",
        "weekly.docx",
        {
            "title": "星河项目周报",
            "sections": [
                {
                    "heading": "下周计划",
                    "tables": [
                        [
                            ["任务", "负责人", "状态"],
                            ["完成接口联调（目标日期 2026-11-17）", "李明", "计划中"],
                        ]
                    ],
                }
            ],
        },
    )
    report = await office.validate("weekly.docx")
    assert "source_text_coverage" in report["checks"]


@pytest.mark.parametrize("bounds,last", [(None, 3), ({"start_row": 2, "end_row": 4}, 4)])
def test_excel_category_chart_excludes_total(tmp_path, bounds, last):
    from openpyxl import load_workbook

    Office(tmp_path).generate(
        "excel",
        "chart.xlsx",
        {
            "sheets": [
                {
                    "name": "Budget",
                    "chart_range": bounds,
                    "rows": [["Item", "Amount"], ["Dev", 12], ["QA", 8], ["合计", 20]],
                    "chart": True,
                }
            ]
        },
    )
    book = load_workbook(tmp_path / "chart.xlsx")
    series = book.active._charts[0].series[0]
    assert series.val.numRef.f.endswith(f"$B$2:$B${last}")
    assert series.cat.numRef.f.endswith(f"$A$2:$A${last}")
    book.close()

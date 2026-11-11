"""Small content templates with explicit unknowns, not fabricated sample facts."""

from copy import deepcopy

TEMPLATES = {
    "word": {
        "title": "项目周报",
        "sections": [
            {"heading": heading, "paragraphs": ["待确认：请根据用户资料填写"]}
            for heading in ["报告周期与来源", "本期完成", "风险与待办", "下期计划"]
        ],
    },
    "excel": {
        "sheets": [
            {
                "name": "预算",
                "rows": [["项目", "金额", "来源或假设"], ["待确认", None, "待补充"]],
                "formulas": [],
                "chart": False,
            }
        ],
    },
    "powerpoint": {
        "slides": [
            {"title": title, "bullets": ["待确认：请根据用户资料填写"]}
            for title in ["项目目标与来源", "进展与证据", "风险和下一步"]
        ],
    },
}


def office_template(kind):
    if kind not in TEMPLATES:
        raise ValueError("Unknown Office kind")
    return deepcopy(TEMPLATES[kind])

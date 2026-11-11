"""Structured Office generation and real LibreOffice/PDF verification."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from agentbuddy.catalog.service import DomainError
from agentbuddy.runtime.files import relative

EXTENSIONS = {"word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx"}
FONT = "Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC"


def renderer():
    local = Path(__file__).resolve().parents[4] / "data/runtime/libreoffice/program"
    options = [
        os.environ.get("SOFFICE_PATH"),
        shutil.which("soffice"),
        str(local / "soffice.com"),
        str(local / "soffice"),
    ]
    return next((p for p in options if p and Path(p).is_file()), None)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(path):
    if path.suffix == ".docx":
        from docx import Document

        doc = Document(path)
        return {
            "paragraphs": [p.text for p in doc.paragraphs],
            "tables": [[[c.text for c in r.cells] for r in t.rows] for t in doc.tables],
        }
    if path.suffix == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(path, data_only=False, read_only=True)
        try:
            return {s.title: [list(r) for r in s.iter_rows(values_only=True)] for s in book}
        finally:
            book.close()
    if path.suffix == ".pptx":
        from pptx import Presentation

        return {
            "slides": [
                [s.text for s in slide.shapes if s.has_text_frame]
                for slide in Presentation(path).slides
            ]
        }
    raise ValueError("Only DOCX/XLSX/PPTX are supported")


class Office:
    def __init__(self, root):
        self.root = root

    def generate(self, kind, path, content):
        from agentbuddy.runtime.office_models import CONTENT_MODELS

        if kind not in CONTENT_MODELS:
            raise ValueError("Unknown Office kind")
        content = CONTENT_MODELS[kind].model_validate(content).model_dump(exclude_none=True)
        target = self.root / relative(path)
        if target.suffix != EXTENSIONS.get(kind):
            raise ValueError("Office kind and extension must match")
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "word":
            from docx import Document
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            from docx.shared import Pt

            doc = Document()
            normal = doc.styles["Normal"]
            normal.font.name, normal.font.size = FONT, Pt(11)
            for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
                style = doc.styles[name]
                style.font.name = FONT
                fonts = style.element.get_or_add_rPr().rFonts
                fonts.set(qn("w:eastAsia"), FONT)
                fonts.set(qn("w:cs"), FONT)
                for theme in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
                    fonts.attrib.pop(qn("w:" + theme), None)
            doc.add_heading(content.get("title", "报告"), 0)
            for section in content.get("sections", []):
                if section.get("heading"):
                    doc.add_heading(section["heading"], 1)
                for paragraph in section.get("paragraphs", []):
                    doc.add_paragraph(str(paragraph))
                for rows in section.get("tables", []):
                    if not rows:
                        continue
                    table = doc.add_table(rows=0, cols=max(map(len, rows)))
                    table.style = "Table Grid"
                    for row in rows:
                        cells = table.add_row().cells
                        for cell, value in zip(cells, row):
                            cell.text = str(value)
                    header = OxmlElement("w:tblHeader")
                    table.rows[0]._tr.get_or_add_trPr().append(header)
            doc.save(target)
        elif kind == "excel":
            from openpyxl import Workbook
            from openpyxl.chart import BarChart, Reference
            from openpyxl.styles import Font, PatternFill

            book = Workbook()
            book.remove(book.active)
            for spec in content.get("sheets", []):
                sheet = book.create_sheet(spec["name"])
                for row in spec.get("rows", []):
                    sheet.append(row)
                for formula in spec.get("formulas", []):
                    sheet[formula["cell"]] = formula["formula"]
                for row in sheet:
                    for cell in row:
                        cell.font = Font(name=FONT, size=11)
                for cell in sheet[1]:
                    cell.font = Font(name=FONT, bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="245578")
                for column in sheet.columns:
                    sheet.column_dimensions[column[0].column_letter].width = min(
                        45, max(12, max(len(str(c.value or "")) for c in column) + 4)
                    )
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                sheet.sheet_properties.pageSetUpPr.fitToPage = True
                sheet.page_setup.orientation = "landscape"
                sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
                sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
                if spec.get("chart") and sheet.max_row > 1 and sheet.max_column > 1:
                    chart = BarChart()
                    bounds = spec.get("chart_range") or {}
                    first_row = bounds.get("start_row", 2)
                    last_row = bounds.get("end_row", sheet.max_row)
                    if not bounds:
                        while last_row >= first_row and str(
                            sheet.cell(last_row, 1).value
                        ).strip().lower() in {"合计", "总计", "total", "grand total"}:
                            last_row -= 1
                    if not (2 <= first_row <= last_row <= sheet.max_row):
                        raise ValueError("Invalid chart data row range")
                    chart.add_data(
                        Reference(
                            sheet,
                            min_col=2,
                            max_col=sheet.max_column,
                            min_row=first_row,
                            max_row=last_row,
                        ),
                        titles_from_data=False,
                    )
                    from openpyxl.chart.series import SeriesLabel

                    for index, series in enumerate(chart.series, 2):
                        series.tx = SeriesLabel(v=str(sheet.cell(1, index).value or ""))
                    chart.set_categories(
                        Reference(sheet, min_col=1, min_row=first_row, max_row=last_row)
                    )
                    sheet.add_chart(chart, f"A{sheet.max_row + 3}")
            if not book.worksheets:
                raise ValueError("At least one sheet is required")
            book.save(target)
            book.close()
        else:
            from pptx import Presentation
            from pptx.util import Inches, Pt

            presentation = Presentation()
            presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
            from pptx.chart.data import CategoryChartData
            from pptx.enum.chart import XL_CHART_TYPE

            for spec in content.get("slides", []):
                title = str(spec.get("title", ""))
                if len(title) > 60:
                    raise ValueError("Shorten slide titles to 60 characters")
                bullets = []
                for value in spec.get("bullets", []):
                    text = str(value)
                    bullets.extend(text[i : i + 75] for i in range(0, len(text), 75))
                groups = [bullets[i : i + 4] for i in range(0, len(bullets), 4)] or [[]]
                for index, group in enumerate(groups):
                    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
                    heading = title + (f"（续 {index + 1}）" if index else "")
                    for text, top, height, size in [
                        (heading, 0.5, 1, 30),
                        ("\n".join(group), 1.7, 5.1, 22),
                    ]:
                        box = slide.shapes.add_textbox(
                            Inches(0.7), Inches(top), Inches(11.9), Inches(height)
                        )
                        box.text_frame.word_wrap = True
                        box.text_frame.text = text
                        for paragraph in box.text_frame.paragraphs:
                            paragraph.font.name, paragraph.font.size = FONT, Pt(size)
                            paragraph.space_after = Pt(12)
                if spec.get("chart"):
                    chart = spec["chart"]
                    data = CategoryChartData()
                    data.categories = chart["categories"]
                    for series in chart["series"]:
                        if len(series["values"]) != len(chart["categories"]):
                            raise ValueError("Chart categories and values must have equal length")
                        data.add_series(series["name"], series["values"])
                    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
                    shape = slide.shapes.add_chart(
                        XL_CHART_TYPE.COLUMN_CLUSTERED,
                        Inches(0.7),
                        Inches(0.7),
                        Inches(11.9),
                        Inches(6),
                        data,
                    )
                    shape.chart.has_title = True
                    shape.chart.chart_title.text_frame.text = title
                    shape.chart.font.name = FONT
            for slide in presentation.slides:
                for shape in slide.shapes:
                    if (
                        shape.left < 0
                        or shape.top < 0
                        or shape.left + shape.width > presentation.slide_width
                        or shape.top + shape.height > presentation.slide_height
                    ):
                        raise ValueError("Slide shape outside canvas")
            if not presentation.slides:
                raise ValueError("At least one slide is required")
            presentation.save(target)
        metadata = {"kind": kind, "content": content, "sha256": digest(target)}
        target.with_suffix(target.suffix + ".agentbuddy.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        return {"path": path, "status": "generated_pending_validation"}

    def edit(self, path, content):
        target = self.root / relative(path)
        meta = target.with_suffix(target.suffix + ".agentbuddy.json")
        if not meta.exists():
            raise ValueError("External files support extraction only; generate a new document")
        old = json.loads(meta.read_text(encoding="utf-8"))
        if old["sha256"] != digest(target):
            raise ValueError(
                "File changed outside structured editor; read and regenerate a new file"
            )
        return self.generate(old["kind"], path, content)

    async def convert(self, source, output, format):
        binary = renderer()
        if not binary:
            raise DomainError("OFFICE_DEPENDENCY", "需要安装 LibreOffice 并设置 SOFFICE_PATH", 422)
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ab-office-") as temporary:
            work = Path(temporary)
            profile = work / "profile"
            local_source = work / ("document" + source.suffix)
            local_output = work / "out"
            local_output.mkdir()
            shutil.copyfile(source, local_source)
            process = await asyncio.create_subprocess_exec(
                binary,
                "-env:UserInstallation=" + profile.as_uri(),
                "--headless",
                "--convert-to",
                format,
                "--outdir",
                str(local_output),
                str(local_source),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            try:
                async with asyncio.timeout(120):
                    await process.communicate()
                if process.returncode:
                    raise ValueError("LibreOffice conversion failed")
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
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        await killer.wait()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            rendered = local_output / ("document." + format.split(":")[0])
            if not rendered.is_file() or not rendered.stat().st_size:
                raise ValueError("LibreOffice did not produce output")
            result = output / (source.stem + "." + format.split(":")[0])
            shutil.copyfile(rendered, result)
            return result

    async def validate(self, path):
        target = self.root / relative(path)
        key = hashlib.sha256(path.encode()).hexdigest()[:20]
        destination = self.root / ".agentbuddy" / "previews" / key
        destination.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".xlsx":
            from openpyxl import load_workbook

            recalculated = await self.convert(target, destination / "recalculated", "xlsx")
            formulas = load_workbook(target, data_only=False)
            values = load_workbook(recalculated, data_only=True)
            try:
                for sheet in formulas:
                    for row in sheet:
                        for cell in row:
                            if cell.data_type == "f":
                                value = values[sheet.title][cell.coordinate]
                                if value.value is None or value.data_type == "e":
                                    raise ValueError(
                                        f"Invalid formula cache: {sheet.title}!{cell.coordinate}"
                                    )
                meta = target.with_suffix(target.suffix + ".agentbuddy.json")
                if meta.exists():
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    for sheet in data["content"].get("sheets", []):
                        for f in sheet.get("formulas", []):
                            if (
                                "expected" in f
                                and values[sheet["name"]][f["cell"]].value != f["expected"]
                            ):
                                raise ValueError("Formula result differs from expected")
            finally:
                formulas.close()
                values.close()
            shutil.copyfile(recalculated, target)
        pdf = await self.convert(target, destination, "pdf")
        import pymupdf

        pages = []
        original_order_text = []
        with pymupdf.open(pdf) as document:
            if not len(document):
                raise ValueError("Rendered PDF is empty")
            for number, page in enumerate(document):
                text = page.get_text(sort=True)
                original_order_text.append(page.get_text())
                if "\ufffd" in text:
                    raise ValueError("Rendered text contains missing glyphs")
                for block in page.get_text("blocks"):
                    if (
                        block[0] < -1
                        or block[1] < -1
                        or block[2] > page.rect.width + 1
                        or block[3] > page.rect.height + 1
                    ):
                        raise ValueError("Rendered text outside page bounds")
                image = destination / f"page-{number + 1}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(image)
                pages.append({"image": image.relative_to(self.root).as_posix(), "text": text})
        report = {
            "path": path,
            "sha256": digest(target),
            "renderer": "LibreOffice",
            "checks": ["format_parse", "pdf_render", "page_bounds", "replacement_glyphs"]
            + (["formula_recalculation"] if target.suffix == ".xlsx" else []),
            "pages": pages,
            "scope": "结构、公式缓存和渲染检查；不保证业务结论或复杂排版完全保真",
            "human_review": {
                "status": "pending",
                "checklist": [
                    "核对名称、日期、金额与原始资料一致",
                    "核对假设、待确认事项和来源标注",
                    "逐页查看预览，检查遮挡、分页和表格可读性",
                    "核对跨文件数字、结论和术语一致",
                ],
            },
        }
        extracted = extract(target)
        if target.suffix in {".docx", ".pptx"}:

            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for item in value.values():
                        yield from strings(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from strings(item)

            rendered = "".join("".join(p["text"].split()) for p in pages)
            original = "".join("".join(t.split()) for t in original_order_text)
            for text in strings(extracted):
                normalized = "".join(text.split())
                # Geometric sorting repairs fallback-font runs, while native PDF order
                # preserves wrapped table cells that sorting interleaves with other columns.
                if normalized not in rendered and normalized not in original:
                    raise ValueError(
                        "Rendered document omitted source text; shorten content or repair fonts"
                    )
            report["checks"].append("source_text_coverage")
        destination.joinpath("report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        meta = target.with_suffix(target.suffix + ".agentbuddy.json")
        if meta.exists():
            data = json.loads(meta.read_text(encoding="utf-8"))
            data["sha256"] = digest(target)
            meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return report

    def proof(self, path):
        key = hashlib.sha256(path.encode()).hexdigest()[:20]
        report = self.root / ".agentbuddy/previews" / key / "report.json"
        if not report.exists():
            return None
        data = json.loads(report.read_text(encoding="utf-8"))
        return data if data["sha256"] == digest(self.root / path) else None

    async def validate_all(self):
        paths = [
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.suffix in EXTENSIONS.values()
            and ".agentbuddy" not in p.relative_to(self.root).parts
        ]
        for path in paths:
            if not self.proof(path):
                await self.validate(path)

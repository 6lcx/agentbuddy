from pydantic import BaseModel, ConfigDict, Field, field_validator


class OfficeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WordSection(OfficeModel):
    heading: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[list[list[str | int | float]]] = Field(default_factory=list)


class WordContent(OfficeModel):
    title: str
    sections: list[WordSection] = Field(default_factory=list)


class Formula(OfficeModel):
    cell: str
    formula: str
    expected: str | int | float | bool | None = None

    @field_validator("formula")
    @classmethod
    def equals(cls, value):
        if not value.startswith("="):
            raise ValueError("Formula must start with =")
        return value


class ChartRange(OfficeModel):
    start_row: int = Field(default=2, ge=2)
    end_row: int = Field(ge=2)


class Sheet(OfficeModel):
    name: str
    rows: list[list[str | int | float | bool | None]] = Field(default_factory=list)
    formulas: list[Formula] = Field(default_factory=list)
    chart: bool = False
    chart_range: ChartRange | None = None


class ExcelContent(OfficeModel):
    sheets: list[Sheet] = Field(min_length=1)


class Series(OfficeModel):
    name: str
    values: list[int | float]


class Chart(OfficeModel):
    categories: list[str]
    series: list[Series] = Field(min_length=1)


class Slide(OfficeModel):
    title: str = Field(max_length=60)
    bullets: list[str] = Field(default_factory=list)
    chart: Chart | None = None


class PresentationContent(OfficeModel):
    slides: list[Slide] = Field(min_length=1)


CONTENT_MODELS = {"word": WordContent, "excel": ExcelContent, "powerpoint": PresentationContent}

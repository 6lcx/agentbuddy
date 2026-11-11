from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Data(BaseModel, Generic[T]):
    data: T


class Listing(Data[list[T]], Generic[T]):
    next_cursor: str | None = None


class ProviderView(BaseModel):
    id: str
    name: str
    provider_type: Literal["qwen_dashscope", "deepseek", "claude"]
    base_url: str
    revision: int
    enabled: bool
    config_json: dict[str, str]
    credential_configured: bool


class ModelView(BaseModel):
    id: str
    provider_id: str
    model_id: str
    revision: int
    enabled: bool
    context_window: int
    max_output_tokens: int
    test_status: str
    tested_provider_revision: int | None
    tested_model_revision: int | None
    tested_at: float | None
    capabilities_json: dict[str, bool]
    available: bool = False
    provider_name: str = ""
    provider_type: str = ""
    provider_revision: int = 0
    provider_enabled: bool = False


class SettingsView(BaseModel):
    revision: int
    default_model_id: str | None


class ProjectView(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float


class MessageView(BaseModel):
    id: str
    project_id: str
    run_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: float


class FailureView(BaseModel):
    code: str
    message: str


class RunView(BaseModel):
    id: str
    project_id: str
    status: Literal[
        "queued", "running", "waiting_input", "cancelled", "succeeded", "failed", "interrupted"
    ]
    partial_text: str
    failure_json: FailureView | None
    created_at: float
    finished_at: float | None


class RunAccepted(BaseModel):
    project_id: str
    run_id: str

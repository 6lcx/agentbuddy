from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbuddy.api.contracts import Data, Listing, ModelView, ProviderView, SettingsView
from agentbuddy.api.system import AcceptedOperation, repository
from agentbuddy.catalog.service import Catalog

router = APIRouter()


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    provider_type: Literal["qwen_dashscope", "deepseek", "claude"]
    base_url: str
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    enabled: bool = True
    config_json: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) base URL without embedded credentials or query")
        return value.rstrip("/")

    @field_validator("config_json")
    @classmethod
    def valid_config(cls, value):
        if set(value) - {"workspace_id"}:
            raise ValueError("Only workspace_id is supported")
        return value


class ProviderUpdate(ProviderInput):
    revision: int = Field(ge=1)


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    model_id: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    context_window: int = Field(default=32768, ge=4096, le=2_000_000)
    max_output_tokens: int = Field(default=2048, ge=64, le=32768)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.max_output_tokens + 2048 >= self.context_window:
            raise ValueError("Output limit must leave room for input context")
        return self


class ModelUpdate(ModelInput):
    revision: int = Field(ge=1)


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[Literal["text", "tools"]] = Field(default_factory=lambda: ["text", "tools"])

    @field_validator("checks")
    @classmethod
    def full_probe(cls, value):
        if set(value) != {"text", "tools"}:
            raise ValueError("This stage requires a combined text and tool probe")
        return value


class DefaultModel(BaseModel):
    default_model_id: str
    revision: int = Field(ge=0)


def catalog(repo=Depends(repository)):
    return Catalog(repo.db)


CatalogDependency = Annotated[Catalog, Depends(catalog)]


@router.get("/providers", response_model=Listing[ProviderView])
def provider_list(service: CatalogDependency):
    return {"data": service.provider_list(), "next_cursor": None}


@router.post("/providers", status_code=201, response_model=Data[ProviderView])
def provider_create(body: ProviderInput, service: CatalogDependency):
    return {"data": service.save_provider(body.model_dump())}


@router.patch("/providers/{provider_id}", response_model=Data[ProviderView])
def provider_update(provider_id: str, body: ProviderUpdate, service: CatalogDependency):
    return {"data": service.save_provider(body.model_dump(), provider_id)}


@router.get("/models", response_model=Listing[ModelView])
def model_list(service: CatalogDependency):
    return {"data": service.model_list(), "next_cursor": None}


@router.post("/models", status_code=201, response_model=Data[ModelView])
def model_create(body: ModelInput, service: CatalogDependency):
    return {"data": service.save_model(body.model_dump())}


@router.patch("/models/{model_id}", response_model=Data[ModelView])
def model_update(model_id: str, body: ModelUpdate, service: CatalogDependency):
    return {"data": service.save_model(body.model_dump(), model_id)}


@router.post("/models/{model_id}/test", status_code=202, response_model=Data[AcceptedOperation])
def model_test(
    model_id: str, body: ProbeInput, service: CatalogDependency, repo=Depends(repository)
):
    payload = service.test_payload(model_id)
    operation = repo.enqueue("model_probe", payload)
    return {"data": {"id": operation["id"], "status": "queued"}}


@router.get("/settings", response_model=Data[SettingsView])
def get_settings(service: CatalogDependency):
    return {"data": service.settings()}


@router.patch("/settings", response_model=Data[SettingsView])
def update_settings(body: DefaultModel, service: CatalogDependency):
    return {"data": service.set_default(body.default_model_id, body.revision)}

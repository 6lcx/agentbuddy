import re
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    api_prefix: str = "/api/v1"
    data_dir: Path = Path(__file__).resolve().parents[3] / "data"
    worker_poll_seconds: float = Field(default=0.5, gt=0, le=10)
    worker_heartbeat_seconds: float = Field(default=5, gt=0, le=10)
    worker_stale_seconds: float = Field(default=30, gt=0)
    operation_timeout_seconds: float = Field(default=60, gt=0)
    worker_shutdown_seconds: float = Field(default=10, gt=0)

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def check_heartbeat_window(self):
        if self.worker_stale_seconds <= self.worker_heartbeat_seconds * 2:
            raise ValueError("Worker stale threshold must exceed twice its heartbeat interval")
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / "agentbuddy.sqlite3"

    def prepare_directories(self):
        for name in ("", "tmp", "skills", "projects", "uploads"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    @field_validator("api_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        value = value.rstrip("/")
        if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", value):
            raise ValueError("API_PREFIX must contain non-empty slash-separated path segments")
        return value

"""Frozen per-run context policy, independent of provider SDK defaults."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_ratio: float = Field(default=0.8, gt=0.2, le=0.9)
    target_ratio: float = Field(default=0.6, gt=0.1, lt=0.9)
    reserve_tokens: int = Field(default=2048, ge=256, le=16384)
    result_inline_bytes: int = Field(default=12000, ge=1024, le=65536)
    result_max_bytes: int = Field(default=64 * 1024 * 1024, ge=65536)
    result_quota_bytes: int = Field(default=256 * 1024 * 1024, ge=65536)
    read_bytes: int = Field(default=8192, ge=512, le=16384)
    retention_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def limits(self):
        if self.target_ratio >= self.trigger_ratio:
            raise ValueError("Target must be below trigger")
        if self.result_max_bytes > self.result_quota_bytes:
            raise ValueError("Result limit exceeds run quota")
        return self

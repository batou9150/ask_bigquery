"""Server configuration, read from environment variables (or a `.env` file)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- BigQuery -----------------------------------------------------------
    bq_billing_project: str = Field(
        description="Project that runs (and pays for) the query jobs.",
    )
    bq_allowed_datasets: Annotated[frozenset[str], NoDecode] = Field(
        description="Comma-separated `project.dataset` allowlist. Nothing else is reachable.",
    )
    bq_location: str = "US"

    # --- Guardrails ---------------------------------------------------------
    bq_max_bytes_billed: int = Field(default=1_000_000_000, gt=0)
    bq_max_rows: int = Field(default=1000, gt=0)
    bq_job_timeout_s: float = Field(default=60.0, gt=0)
    bq_sample_rows: int = Field(default=3, ge=0, le=20)
    bq_price_per_tib_usd: float = Field(default=6.25, ge=0)

    # --- Transport ----------------------------------------------------------
    mcp_transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8080

    @field_validator("bq_allowed_datasets", mode="before")
    @classmethod
    def _parse_allowlist(cls, value: object) -> frozenset[str]:
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list | tuple | set | frozenset):
            raise ValueError("expected a comma-separated list of `project.dataset`")
        datasets = frozenset(str(item).strip() for item in items if str(item).strip())
        if not datasets:
            raise ValueError("at least one `project.dataset` is required")
        for dataset in datasets:
            parts = dataset.split(".")
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"{dataset!r} is not of the form `project.dataset`")
        return datasets

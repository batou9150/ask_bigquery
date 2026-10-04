"""Typed tool outputs. They become the tools' MCP output schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    dataset: str = Field(
        description="Fully qualified `project.dataset`, as expected by other tools."
    )
    location: str | None
    description: str | None


class TableInfo(BaseModel):
    table: str = Field(description="Table name, to pass to `get_table_schema`.")
    type: str = Field(description="TABLE, VIEW, MATERIALIZED_VIEW or EXTERNAL.")
    description: str | None
    num_rows: int | None
    num_bytes: int | None


class Column(BaseModel):
    name: str
    type: str
    mode: str = Field(description="NULLABLE, REQUIRED or REPEATED (an ARRAY).")
    description: str | None = None
    fields: list[Column] = Field(default_factory=list, description="Sub-fields of a RECORD.")


class Partitioning(BaseModel):
    type: str = Field(description="DAY, HOUR, MONTH, YEAR (time) or RANGE (integer).")
    field: str | None = Field(description="Partitioning column; null means ingestion time.")
    require_partition_filter: bool


class TableSchema(BaseModel):
    table: str = Field(description="Fully qualified `project.dataset.table`, to use in SQL.")
    type: str
    description: str | None
    num_rows: int | None
    num_bytes: int | None
    columns: list[Column]
    partitioning: Partitioning | None
    clustering: list[str] = Field(description="Clustering columns, in order.")
    sample_rows: list[dict[str, Any]] = Field(description="A few rows, to see real values.")


class CostEstimate(BaseModel):
    allowed: bool = Field(description="Whether `run_query` would accept this query.")
    reason: str | None = Field(description="Why the query would be refused, if it would.")
    statement_type: str | None
    bytes_processed: int
    bytes_processed_human: str
    estimated_cost_usd: float = Field(description="On-demand pricing; 0 within the free tier.")
    max_bytes_billed: int
    referenced_tables: list[str]


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(description="Number of rows returned here.")
    total_rows: int | None = Field(description="Number of rows the query produced.")
    truncated: bool = Field(description="True if rows were cut at `max_rows`.")
    bytes_billed: int | None
    job_id: str | None

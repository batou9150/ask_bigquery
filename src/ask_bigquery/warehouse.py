"""All BigQuery access goes through `Warehouse`, which applies the guardrails."""

from __future__ import annotations

import base64
import concurrent.futures
import datetime
import decimal
from collections.abc import Mapping
from typing import Any

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery

from ask_bigquery.config import Settings
from ask_bigquery.errors import GuardrailError, QueryError
from ask_bigquery.guardrails import check_query, dry_run, ensure_dataset_allowed, format_bytes
from ask_bigquery.models import (
    Column,
    CostEstimate,
    DatasetInfo,
    Partitioning,
    QueryResult,
    TableInfo,
    TableSchema,
)

JOB_LABELS = {"mcp-server": "ask-bigquery"}
MAX_TABLES_LISTED = 200
BYTES_PER_TIB = 1024**4


class Warehouse:
    def __init__(self, client: bigquery.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    # --- metadata -----------------------------------------------------------
    def list_datasets(self) -> list[DatasetInfo]:
        datasets = []
        for dataset_id in sorted(self._settings.bq_allowed_datasets):
            dataset = self._client.get_dataset(dataset_id)
            datasets.append(
                DatasetInfo(
                    dataset=dataset_id, location=dataset.location, description=dataset.description
                )
            )
        return datasets

    def list_tables(self, dataset: str) -> list[TableInfo]:
        ensure_dataset_allowed(dataset, self._settings.bq_allowed_datasets)
        items = list(self._client.list_tables(dataset))[:MAX_TABLES_LISTED]
        tables = [self._client.get_table(item.reference) for item in items]
        return [
            TableInfo(
                table=table.table_id,
                type=table.table_type,
                description=table.description,
                num_rows=table.num_rows,
                num_bytes=table.num_bytes,
            )
            for table in tables
        ]

    def get_table_schema(self, dataset: str, table: str) -> TableSchema:
        ensure_dataset_allowed(dataset, self._settings.bq_allowed_datasets)
        project, dataset_id = dataset.split(".")
        ref = bigquery.DatasetReference(project, dataset_id).table(table)
        meta = self._client.get_table(ref)

        sample_rows: list[dict[str, Any]] = []
        # `list_rows` reads table storage directly: free, no query job. Not available on views.
        if self._settings.bq_sample_rows and meta.table_type == "TABLE":
            rows = self._client.list_rows(meta, max_results=self._settings.bq_sample_rows)
            sample_rows = [to_json_row(row) for row in rows]

        return TableSchema(
            table=f"{project}.{dataset_id}.{meta.table_id}",
            type=meta.table_type,
            description=meta.description,
            num_rows=meta.num_rows,
            num_bytes=meta.num_bytes,
            columns=[to_column(field) for field in meta.schema],
            partitioning=to_partitioning(meta),
            clustering=list(meta.clustering_fields or []),
            sample_rows=sample_rows,
        )

    # --- queries ------------------------------------------------------------
    def estimate_query_cost(self, sql: str) -> CostEstimate:
        plan = dry_run(self._client, sql, self._settings.bq_location)
        reason = None
        try:
            check_query(
                plan, self._settings.bq_allowed_datasets, self._settings.bq_max_bytes_billed
            )
        except GuardrailError as error:
            reason = str(error)
        return CostEstimate(
            allowed=reason is None,
            reason=reason,
            statement_type=plan.statement_type,
            bytes_processed=plan.total_bytes_processed,
            bytes_processed_human=format_bytes(plan.total_bytes_processed),
            estimated_cost_usd=round(
                plan.total_bytes_processed / BYTES_PER_TIB * self._settings.bq_price_per_tib_usd, 6
            ),
            max_bytes_billed=self._settings.bq_max_bytes_billed,
            referenced_tables=list(plan.referenced_tables),
        )

    def run_query(self, sql: str, max_rows: int) -> QueryResult:
        settings = self._settings
        plan = dry_run(self._client, sql, settings.bq_location)
        check_query(plan, settings.bq_allowed_datasets, settings.bq_max_bytes_billed)

        max_rows = min(max_rows, settings.bq_max_rows)
        config = bigquery.QueryJobConfig(
            # Enforced by BigQuery itself, even if the dry-run estimate was wrong.
            maximum_bytes_billed=settings.bq_max_bytes_billed,
            job_timeout_ms=int(settings.bq_job_timeout_s * 1000),
            labels=JOB_LABELS,
            use_legacy_sql=False,
        )
        try:
            job = self._client.query(sql, job_config=config, location=settings.bq_location)
            rows = job.result(max_results=max_rows, timeout=settings.bq_job_timeout_s)
        except GoogleAPICallError as error:
            raise QueryError.from_api(error) from error
        except concurrent.futures.TimeoutError as error:
            raise QueryError(
                f"The query did not finish within {settings.bq_job_timeout_s:g}s."
            ) from error

        result_rows = [to_json_row(row) for row in rows]
        return QueryResult(
            columns=[field.name for field in rows.schema],
            rows=result_rows,
            row_count=len(result_rows),
            total_rows=rows.total_rows,
            truncated=rows.total_rows is not None and rows.total_rows > len(result_rows),
            bytes_billed=job.total_bytes_billed,
            job_id=job.job_id,
        )


# --- conversions --------------------------------------------------------------
def to_column(field: bigquery.SchemaField) -> Column:
    return Column(
        name=field.name,
        type=field.field_type,
        mode=field.mode,
        description=field.description,
        fields=[to_column(sub) for sub in field.fields],
    )


def to_partitioning(table: bigquery.Table) -> Partitioning | None:
    if table.time_partitioning is not None:
        return Partitioning(
            type=table.time_partitioning.type_,
            field=table.time_partitioning.field,
            require_partition_filter=bool(table.require_partition_filter),
        )
    if table.range_partitioning is not None:
        return Partitioning(
            type="RANGE",
            field=table.range_partitioning.field,
            require_partition_filter=bool(table.require_partition_filter),
        )
    return None


def to_json_row(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {key: to_json_value(value) for key, value in row.items()}


def to_json_value(value: Any) -> Any:
    """Make BigQuery values JSON-friendly without losing precision."""
    match value:
        case decimal.Decimal():
            return str(value)
        case datetime.date() | datetime.time():  # also matches datetime.datetime
            return value.isoformat()
        case bytes():
            return base64.b64encode(value).decode("ascii")
        case Mapping():
            return {key: to_json_value(item) for key, item in value.items()}
        case list() | tuple():
            return [to_json_value(item) for item in value]
        case _:
            return value

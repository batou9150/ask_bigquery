"""Guardrails. Every query goes through `dry_run` then `check_query` before it runs.

The checks rely on what BigQuery itself reports in the dry-run (statement type,
referenced tables, bytes processed), never on parsing or regex-matching the SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery

from ask_bigquery.errors import GuardrailError, QueryError

# The only statement type that may run. Multi-statement scripts are reported as
# "SCRIPT", DML as "INSERT"/"UPDATE"/..., DDL as "CREATE_TABLE"/"DROP_TABLE"/...
READ_ONLY_STATEMENT_TYPE = "SELECT"


@dataclass(frozen=True)
class DryRun:
    statement_type: str | None
    total_bytes_processed: int
    referenced_tables: tuple[str, ...]  # fully qualified: `project.dataset.table`


def dry_run(client: bigquery.Client, sql: str, location: str) -> DryRun:
    """Ask BigQuery to plan the query without running it (free, no data read)."""
    config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    try:
        job = client.query(sql, job_config=config, location=location)
    except GoogleAPICallError as error:
        raise QueryError(error.message) from error
    return DryRun(
        statement_type=job.statement_type,
        total_bytes_processed=job.total_bytes_processed or 0,
        referenced_tables=tuple(
            f"{table.project}.{table.dataset_id}.{table.table_id}"
            for table in job.referenced_tables
        ),
    )


def ensure_dataset_allowed(dataset: str, allowed_datasets: Iterable[str]) -> None:
    """Refuse any `project.dataset` that is not explicitly allowlisted."""
    if dataset not in allowed_datasets:
        raise GuardrailError(
            f"Dataset `{dataset}` is not allowed. "
            f"Allowed datasets: {', '.join(sorted(allowed_datasets))}."
        )


def check_query(plan: DryRun, allowed_datasets: Iterable[str], max_bytes_billed: int) -> None:
    """Raise `GuardrailError` unless the plan is a read-only, allowlisted, affordable SELECT."""
    allowed = frozenset(allowed_datasets)

    if plan.statement_type != READ_ONLY_STATEMENT_TYPE:
        raise GuardrailError(
            f"Only a single read-only SELECT statement is allowed; "
            f"BigQuery reports this query as `{plan.statement_type}`."
        )

    # Fail closed: a query that reads no table we can see cannot be checked against
    # the allowlist (e.g. some INFORMATION_SCHEMA views), so it is refused.
    if not plan.referenced_tables:
        raise GuardrailError(
            "The query does not reference any table in the allowed datasets. "
            "Query the tables listed by `list_tables` instead."
        )

    for table in plan.referenced_tables:
        project, dataset, _ = table.split(".", 2)
        ensure_dataset_allowed(f"{project}.{dataset}", allowed)

    if plan.total_bytes_processed > max_bytes_billed:
        raise GuardrailError(
            f"The query would scan {format_bytes(plan.total_bytes_processed)}, above the "
            f"{format_bytes(max_bytes_billed)} limit. Select fewer columns, or filter on "
            f"the partitioning / clustering columns."
        )


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")

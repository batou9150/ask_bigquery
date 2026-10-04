"""An in-memory stand-in for `google.cloud.bigquery.Client`, covering what the server uses."""

from __future__ import annotations

import datetime
import decimal
from dataclasses import dataclass, field
from typing import Any

from google.api_core.exceptions import BadRequest, NotFound
from google.cloud import bigquery

from ask_bigquery.config import Settings

DATASET = "bigquery-public-data.thelook_ecommerce"
ORDERS = f"{DATASET}.orders"
GROUP_BY = "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"


@dataclass
class FakeJob:
    statement_type: str | None = "SELECT"
    total_bytes_processed: int | None = 1_000
    referenced_tables: list[bigquery.TableReference] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    schema: list[bigquery.SchemaField] = field(default_factory=list)
    job_id: str = "job_123"
    total_bytes_billed: int | None = 10_485_760

    def result(self, max_results: int | None = None, timeout: float | None = None) -> FakeRows:
        rows = self.rows if max_results is None else self.rows[:max_results]
        return FakeRows(rows=rows, schema=self.schema, total_rows=len(self.rows))


@dataclass
class FakeRows:
    rows: list[dict[str, Any]]
    schema: list[bigquery.SchemaField]
    total_rows: int

    def __iter__(self) -> Any:
        return iter(self.rows)


def table_ref(fqn: str) -> bigquery.TableReference:
    return bigquery.TableReference.from_string(fqn)


class FakeClient:
    """Answers `query()` from a dict keyed by SQL and records every job config it receives."""

    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.errors: dict[str, str] = {}
        self.datasets: dict[str, bigquery.Dataset] = {}
        self.tables: dict[str, bigquery.Table] = {}
        self.table_rows: dict[str, list[dict[str, Any]]] = {}
        self.query_calls: list[tuple[str, bigquery.QueryJobConfig]] = []

    # --- queries ------------------------------------------------------------
    def query(
        self, sql: str, job_config: bigquery.QueryJobConfig, location: str | None = None
    ) -> FakeJob:
        self.query_calls.append((sql, job_config))
        if sql in self.errors:
            raise BadRequest(self.errors[sql])  # type: ignore[no-untyped-call]
        return self.jobs.get(sql, FakeJob())

    @property
    def executed(self) -> list[bigquery.QueryJobConfig]:
        """Configs of the jobs that actually ran (i.e. not dry-runs)."""
        return [config for _, config in self.query_calls if not config.dry_run]

    # --- metadata -----------------------------------------------------------
    def get_dataset(self, ref: str) -> bigquery.Dataset:
        return self.datasets[ref]

    def list_tables(self, ref: str) -> list[bigquery.Table]:
        return [t for fqn, t in self.tables.items() if fqn.rsplit(".", 1)[0] == ref]

    def get_table(self, ref: str | bigquery.TableReference) -> bigquery.Table:
        if str(ref) not in self.tables:
            raise NotFound(f"Not found: Table {ref}")  # type: ignore[no-untyped-call]
        return self.tables[str(ref)]

    def list_rows(self, table: bigquery.Table, max_results: int) -> list[dict[str, Any]]:
        return self.table_rows.get(str(table.reference), [])[:max_results]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "bq_billing_project": "billing-project",
        "bq_allowed_datasets": frozenset({DATASET}),
        "bq_max_rows": 50,
        "bq_sample_rows": 2,
    }
    return Settings(_env_file=None, **(values | overrides))


def thelook_client() -> FakeClient:
    """A fake BigQuery client with one dataset, one partitioned table and one view."""
    client = FakeClient()
    dataset = bigquery.Dataset(DATASET)
    dataset.location = "US"
    dataset.description = "Fictitious e-commerce data"
    client.datasets[DATASET] = dataset

    orders = bigquery.Table(
        ORDERS,
        schema=[
            bigquery.SchemaField("order_id", "INTEGER", "REQUIRED", description="Order id"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField(
                "items", "RECORD", "REPEATED", fields=[bigquery.SchemaField("sku", "STRING")]
            ),
        ],
    )
    orders.description = "One row per order"
    orders.time_partitioning = bigquery.TimePartitioning(field="created_at")
    orders.clustering_fields = ["status"]
    orders._properties["type"] = "TABLE"
    orders._properties["numRows"] = "125000"
    orders._properties["numBytes"] = "9000000"
    client.tables[ORDERS] = orders
    client.table_rows[ORDERS] = [
        {"order_id": 1, "status": "Shipped", "created_at": datetime.datetime(2024, 1, 2, 3, 4)},
        {"order_id": 2, "status": "Complete", "created_at": None},
        {"order_id": 3, "status": "Returned", "created_at": None},
    ]

    view = bigquery.Table(f"{DATASET}.orders_view")
    view._properties["type"] = "VIEW"
    client.tables[f"{DATASET}.orders_view"] = view

    client.jobs[GROUP_BY] = FakeJob(
        referenced_tables=[table_ref(ORDERS)],
        schema=[bigquery.SchemaField("status", "STRING"), bigquery.SchemaField("n", "INTEGER")],
        rows=[
            {"status": "Shipped", "n": 10, "amount": decimal.Decimal("1.10")},
            {"status": "Complete", "n": 20, "amount": decimal.Decimal("2.20")},
        ],
    )
    return client

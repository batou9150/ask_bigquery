import datetime
import decimal
from typing import cast

import pytest
from google.cloud import bigquery
from mcp.server.mcpserver import MCPServer

from ask_bigquery.config import Settings
from ask_bigquery.server import create_server
from tests.fakes import FakeClient, FakeJob, table_ref

DATASET = "bigquery-public-data.thelook_ecommerce"
ORDERS = f"{DATASET}.orders"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        bq_billing_project="billing-project",
        bq_allowed_datasets=frozenset({DATASET}),
        bq_max_bytes_billed=1_000_000_000,
        bq_max_rows=50,
        bq_sample_rows=2,
    )


@pytest.fixture
def client() -> FakeClient:
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

    client.jobs["SELECT status, COUNT(*) AS n FROM orders GROUP BY status"] = FakeJob(
        referenced_tables=[table_ref(ORDERS)],
        schema=[bigquery.SchemaField("status", "STRING"), bigquery.SchemaField("n", "INTEGER")],
        rows=[
            {"status": "Shipped", "n": 10, "amount": decimal.Decimal("1.10")},
            {"status": "Complete", "n": 20, "amount": decimal.Decimal("2.20")},
        ],
    )
    return client


@pytest.fixture
def server(settings: Settings, client: FakeClient) -> MCPServer:
    return create_server(settings, cast(bigquery.Client, client))

"""Against real BigQuery: `BQ_BILLING_PROJECT=my-project uv run pytest -m integration`.

Uses `bigquery-public-data.thelook_ecommerce` (a few hundred MB), billed to your project.
"""

import os

import pytest
from google.cloud import bigquery

from ask_bigquery.errors import AskBigQueryError, GuardrailError
from ask_bigquery.warehouse import Warehouse
from tests.fakes import DATASET, make_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> bigquery.Client:
    project = os.environ.get("BQ_BILLING_PROJECT")
    if not project:
        pytest.skip("BQ_BILLING_PROJECT is not set")
    return bigquery.Client(project=project, location="US")


@pytest.fixture
def warehouse(client: bigquery.Client) -> Warehouse:
    return Warehouse(client, make_settings(bq_billing_project=client.project))


def test_metadata(warehouse: Warehouse) -> None:
    (dataset,) = warehouse.list_datasets()
    assert dataset.dataset == DATASET
    assert {"orders", "order_items", "products", "users"} <= {
        t.table for t in warehouse.list_tables(DATASET)
    }
    schema = warehouse.get_table_schema(DATASET, "orders")
    assert "status" in {c.name for c in schema.columns}
    assert len(schema.sample_rows) == 2


def test_select_runs(warehouse: Warehouse) -> None:
    sql = f"SELECT status, COUNT(*) AS n FROM `{DATASET}.orders` GROUP BY status ORDER BY status"
    estimate = warehouse.estimate_query_cost(sql)
    assert estimate.allowed, estimate.reason
    assert estimate.bytes_processed > 0

    result = warehouse.run_query(sql, max_rows=100)
    assert result.columns == ["status", "n"]
    assert {row["status"] for row in result.rows} >= {"Complete", "Shipped"}


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(f"DELETE FROM `{DATASET}.orders` WHERE TRUE", id="dml"),
        pytest.param(f"DROP TABLE `{DATASET}.orders`", id="ddl"),
        pytest.param(f"SELECT 1 FROM `{DATASET}.orders`; SELECT 2", id="multi-statement"),
        pytest.param(
            f"CREATE TEMP TABLE t AS SELECT * FROM `{DATASET}.orders`; SELECT * FROM t",
            id="script-with-temp-table",
        ),
        pytest.param("SELECT * FROM `bigquery-public-data.samples.shakespeare`", id="allowlist"),
        pytest.param(
            "SELECT * FROM `bigquery-public-data.samples`.INFORMATION_SCHEMA.TABLES",
            id="information-schema-other-dataset",
        ),
        pytest.param(
            "SELECT * FROM `region-us`.INFORMATION_SCHEMA.JOBS", id="information-schema-jobs"
        ),
        pytest.param("SELECT 1", id="no-table"),
    ],
)
def test_refused(warehouse: Warehouse, sql: str) -> None:
    # Refused either by a guardrail, or by BigQuery itself (e.g. no write permission).
    with pytest.raises(AskBigQueryError) as error:
        warehouse.run_query(sql, max_rows=10)
    print(f"{sql!r} -> {error.value}")


def test_information_schema_of_allowed_dataset_runs(warehouse: Warehouse) -> None:
    sql = f"SELECT table_name FROM `{DATASET}`.INFORMATION_SCHEMA.TABLES"
    assert warehouse.run_query(sql, max_rows=100).row_count > 0


def test_cost_cap(client: bigquery.Client) -> None:
    settings = make_settings(bq_billing_project=client.project, bq_max_bytes_billed=1000)
    with pytest.raises(GuardrailError, match="above the 1000 B limit"):
        Warehouse(client, settings).run_query(f"SELECT * FROM `{DATASET}.orders`", max_rows=1)

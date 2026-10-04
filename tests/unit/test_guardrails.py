from typing import cast

import pytest
from google.cloud import bigquery

from ask_bigquery.errors import GuardrailError, QueryError
from ask_bigquery.guardrails import DryRun, check_query, dry_run, ensure_dataset_allowed
from tests.fakes import FakeClient, FakeJob, table_ref

ALLOWED = {"bigquery-public-data.thelook_ecommerce"}
ORDERS = "bigquery-public-data.thelook_ecommerce.orders"
MAX_BYTES = 1_000_000_000


def plan(
    statement_type: str | None = "SELECT", tables: tuple[str, ...] = (ORDERS,), bytes: int = 1_000
) -> DryRun:
    return DryRun(statement_type, total_bytes_processed=bytes, referenced_tables=tables)


def test_select_on_allowed_table_passes() -> None:
    check_query(plan(), ALLOWED, MAX_BYTES)


# --- read-only ------------------------------------------------------------------
@pytest.mark.parametrize(
    "statement_type",
    ["INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE_TABLE",  # DML
     "CREATE_TABLE", "CREATE_TABLE_AS_SELECT", "DROP_TABLE", "ALTER_TABLE",  # DDL
     "CREATE_FUNCTION", "EXPORT_DATA", "CALL", "GRANT",
     "SCRIPT",  # multi-statement, e.g. `SELECT 1; DROP TABLE x`
     None],
)  # fmt: skip
def test_non_select_statements_are_refused(statement_type: str | None) -> None:
    with pytest.raises(GuardrailError, match="read-only SELECT"):
        check_query(plan(statement_type), ALLOWED, MAX_BYTES)


# --- allowlist ------------------------------------------------------------------
def test_table_outside_allowlist_is_refused() -> None:
    with pytest.raises(GuardrailError, match=r"`bigquery-public-data\.samples` is not allowed"):
        check_query(plan(tables=("bigquery-public-data.samples.shakespeare",)), ALLOWED, MAX_BYTES)


def test_join_with_one_forbidden_table_is_refused() -> None:
    tables = (ORDERS, "other-project.thelook_ecommerce.orders")
    with pytest.raises(GuardrailError, match="not allowed"):
        check_query(plan(tables=tables), ALLOWED, MAX_BYTES)


def test_same_dataset_name_in_another_project_is_refused() -> None:
    with pytest.raises(GuardrailError, match="not allowed"):
        ensure_dataset_allowed("attacker-project.thelook_ecommerce", ALLOWED)


# Shapes of `referenced_tables` observed on real BigQuery dry-runs.
def test_information_schema_of_an_allowed_dataset_passes() -> None:
    tables = ("bigquery-public-data.thelook_ecommerce.INFORMATION_SCHEMA.TABLES",)
    check_query(plan(tables=tables), ALLOWED, MAX_BYTES)


@pytest.mark.parametrize(
    "table",
    [
        # `region-us`.INFORMATION_SCHEMA.JOBS is reported under the billing project
        "billing-project.region-us.INFORMATION_SCHEMA.JOBS",
        "billing-project.region-us.INFORMATION_SCHEMA.SCHEMATA",
        "bigquery-public-data.samples.INFORMATION_SCHEMA.TABLES",
    ],
)
def test_information_schema_outside_allowlist_is_refused(table: str) -> None:
    with pytest.raises(GuardrailError, match="not allowed"):
        check_query(plan(tables=(table,)), ALLOWED, MAX_BYTES)


def test_query_without_referenced_tables_is_refused() -> None:
    with pytest.raises(GuardrailError, match="does not reference any table"):
        check_query(plan(tables=()), ALLOWED, MAX_BYTES)


# --- cost -----------------------------------------------------------------------
def test_query_above_byte_limit_is_refused() -> None:
    with pytest.raises(GuardrailError, match=r"2\.00 GB, above the 1\.00 GB limit"):
        check_query(plan(bytes=2_000_000_000), ALLOWED, MAX_BYTES)


def test_query_at_byte_limit_passes() -> None:
    check_query(plan(bytes=MAX_BYTES), ALLOWED, MAX_BYTES)


def test_statement_type_is_checked_before_cost() -> None:
    with pytest.raises(GuardrailError, match="read-only"):
        check_query(plan("DROP_TABLE", bytes=10**15), ALLOWED, MAX_BYTES)


# --- dry run --------------------------------------------------------------------
def test_dry_run_reads_bigquery_plan() -> None:
    client = FakeClient()
    client.jobs["SELECT 1"] = FakeJob(
        statement_type="SELECT", total_bytes_processed=42, referenced_tables=[table_ref(ORDERS)]
    )
    result = dry_run(cast(bigquery.Client, client), "SELECT 1", "US")

    assert result == DryRun("SELECT", 42, (ORDERS,))
    ((_, config),) = client.query_calls
    assert config.dry_run is True
    assert config.use_query_cache is False


def test_bigquery_error_messages_drop_the_request_url() -> None:
    client = FakeClient()
    client.errors["DELETE FROM t WHERE TRUE"] = (
        "POST https://bigquery.googleapis.com/bigquery/v2/projects/billing/jobs?prettyPrint=false: "
        "Access Denied: Table p:d.t: Permission bigquery.tables.updateData denied"
    )
    with pytest.raises(QueryError) as error:
        dry_run(cast(bigquery.Client, client), "DELETE FROM t WHERE TRUE", "US")
    assert str(error.value) == (
        "[BIGQUERY_ERROR] Access Denied: Table p:d.t: Permission bigquery.tables.updateData denied"
    )


def test_dry_run_turns_bigquery_errors_into_query_errors() -> None:
    client = FakeClient()
    client.errors["SELEC"] = "Syntax error: Unexpected identifier at [1:1]"
    with pytest.raises(QueryError, match=r"\[BIGQUERY_ERROR\] Syntax error"):
        dry_run(cast(bigquery.Client, client), "SELEC", "US")

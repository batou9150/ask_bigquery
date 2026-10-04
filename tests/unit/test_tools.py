"""The tools, called through a real MCP client connected in-process to the server."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from tests.fakes import DATASET, GROUP_BY, ORDERS, FakeClient, FakeJob, table_ref

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mcp(server: MCPServer) -> AsyncIterator[Client]:
    async with Client(server) as client:
        yield client


async def call(mcp: Client, tool: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def call_error(mcp: Client, tool: str, **arguments: Any) -> str:
    result = await mcp.call_tool(tool, arguments)
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    prefix = f"Error executing tool {tool}: "
    assert result.content[0].text.startswith(prefix)
    return result.content[0].text.removeprefix(prefix)


async def test_tools_are_listed_with_docs_and_read_only_hint(mcp: Client) -> None:
    tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
    assert set(tools) == {
        "list_datasets",
        "list_tables",
        "get_table_schema",
        "estimate_query_cost",
        "run_query",
    }
    for tool in tools.values():
        assert tool.description
        assert tool.output_schema is not None
        assert tool.annotations is not None and tool.annotations.read_only_hint


async def test_list_datasets_returns_only_allowlisted(mcp: Client) -> None:
    result = await call(mcp, "list_datasets")
    assert result["result"] == [
        {"dataset": DATASET, "location": "US", "description": "Fictitious e-commerce data"}
    ]


async def test_list_tables(mcp: Client) -> None:
    result = await call(mcp, "list_tables", dataset=DATASET)
    assert {t["table"]: (t["type"], t["num_rows"]) for t in result["result"]} == {
        "orders": ("TABLE", 125000),
        "orders_view": ("VIEW", None),
    }


async def test_list_tables_outside_allowlist_is_refused(mcp: Client) -> None:
    message = await call_error(mcp, "list_tables", dataset="bigquery-public-data.samples")
    assert message.startswith("[REFUSED] Dataset `bigquery-public-data.samples` is not allowed")


async def test_get_table_schema(mcp: Client) -> None:
    schema = await call(mcp, "get_table_schema", dataset=DATASET, table="orders")
    assert schema["table"] == ORDERS
    assert [c["name"] for c in schema["columns"]] == ["order_id", "status", "created_at", "items"]
    assert schema["columns"][3]["fields"][0]["name"] == "sku"
    assert schema["partitioning"] == {
        "type": "DAY",
        "field": "created_at",
        "require_partition_filter": False,
    }
    assert schema["clustering"] == ["status"]
    assert schema["sample_rows"] == [
        {"order_id": 1, "status": "Shipped", "created_at": "2024-01-02T03:04:00"},
        {"order_id": 2, "status": "Complete", "created_at": None},
    ]


async def test_get_table_schema_of_a_view_has_no_sample_rows(mcp: Client) -> None:
    schema = await call(mcp, "get_table_schema", dataset=DATASET, table="orders_view")
    assert schema["sample_rows"] == []


async def test_get_table_schema_outside_allowlist_is_refused(mcp: Client) -> None:
    message = await call_error(
        mcp, "get_table_schema", dataset="other-project.thelook_ecommerce", table="orders"
    )
    assert "[REFUSED]" in message


async def test_estimate_query_cost(mcp: Client, client: FakeClient) -> None:
    client.jobs[GROUP_BY].total_bytes_processed = 2 * 1024**4  # 2 TiB
    estimate = await call(mcp, "estimate_query_cost", sql=GROUP_BY)
    assert estimate["allowed"] is False
    assert "above the 1.00 GB limit" in estimate["reason"]
    assert estimate["estimated_cost_usd"] == 12.5
    assert estimate["referenced_tables"] == [ORDERS]
    assert client.executed == []


async def test_run_query(mcp: Client, client: FakeClient) -> None:
    result = await call(mcp, "run_query", sql=GROUP_BY)
    assert result["columns"] == ["status", "n"]
    assert result["rows"][0] == {"status": "Shipped", "n": 10, "amount": "1.10"}
    assert result["truncated"] is False

    (config,) = client.executed
    assert config.maximum_bytes_billed == 1_000_000_000
    assert config.labels == {"mcp-server": "ask-bigquery"}
    assert int(config.job_timeout_ms) == 60_000


async def test_run_query_truncates_rows(mcp: Client) -> None:
    result = await call(mcp, "run_query", sql=GROUP_BY, max_rows=1)
    assert result["row_count"] == 1
    assert result["total_rows"] == 2
    assert result["truncated"] is True


async def test_run_query_caps_max_rows_to_server_limit(mcp: Client, client: FakeClient) -> None:
    client.jobs[GROUP_BY].rows *= 100  # 200 rows, server limit is 50
    result = await call(mcp, "run_query", sql=GROUP_BY, max_rows=10_000)
    assert result["row_count"] == 50


# --- guardrails, end to end through the tool ---------------------------------------
@pytest.mark.parametrize(
    ("sql", "statement_type"),
    [
        (f"DELETE FROM `{ORDERS}` WHERE TRUE", "DELETE"),
        (f"DROP TABLE `{ORDERS}`", "DROP_TABLE"),
        (f"SELECT 1 FROM `{ORDERS}`; DROP TABLE `{ORDERS}`", "SCRIPT"),
    ],
)
async def test_run_query_refuses_non_select(
    mcp: Client, client: FakeClient, sql: str, statement_type: str
) -> None:
    client.jobs[sql] = FakeJob(statement_type=statement_type, referenced_tables=[table_ref(ORDERS)])
    message = await call_error(mcp, "run_query", sql=sql)
    assert f"reports this query as `{statement_type}`" in message
    assert client.executed == []


async def test_run_query_refuses_dataset_outside_allowlist(mcp: Client, client: FakeClient) -> None:
    sql = "SELECT * FROM `bigquery-public-data.samples.shakespeare`"
    client.jobs[sql] = FakeJob(
        referenced_tables=[table_ref("bigquery-public-data.samples.shakespeare")]
    )
    message = await call_error(mcp, "run_query", sql=sql)
    assert "not allowed" in message
    assert client.executed == []


async def test_run_query_refuses_query_over_budget(mcp: Client, client: FakeClient) -> None:
    client.jobs[GROUP_BY].total_bytes_processed = 5_000_000_000
    message = await call_error(mcp, "run_query", sql=GROUP_BY)
    assert "5.00 GB, above the 1.00 GB limit" in message
    assert client.executed == []


async def test_bigquery_errors_are_returned_without_stack_trace(
    mcp: Client, client: FakeClient
) -> None:
    client.errors["SELECT nope FROM t"] = "Unrecognized name: nope at [1:8]"
    message = await call_error(mcp, "run_query", sql="SELECT nope FROM t")
    assert message == "[BIGQUERY_ERROR] Unrecognized name: nope at [1:8]"


async def test_unknown_table_is_reported_clearly(mcp: Client) -> None:
    message = await call_error(mcp, "get_table_schema", dataset=DATASET, table="nope")
    assert message == f"[BIGQUERY_ERROR] Not found: Table {DATASET}.nope"


async def test_analyze_question_prompt(mcp: Client) -> None:
    prompt = await mcp.get_prompt("analyze_question", {"question": "Top 3 categories?"})
    content = prompt.messages[0].content
    assert isinstance(content, TextContent)
    assert "Top 3 categories?" in content.text
    assert "estimate_query_cost" in content.text

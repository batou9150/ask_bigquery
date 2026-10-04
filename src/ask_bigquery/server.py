"""The MCP server: five read-only tools and one prompt, on top of `Warehouse`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from google.api_core.client_info import ClientInfo
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ask_bigquery import __version__
from ask_bigquery.config import Settings
from ask_bigquery.errors import AskBigQueryError, QueryError
from ask_bigquery.models import CostEstimate, DatasetInfo, QueryResult, TableInfo, TableSchema
from ask_bigquery.warehouse import Warehouse

INSTRUCTIONS = """\
Read-only access to a fixed set of BigQuery datasets (GoogleSQL dialect).
Workflow: list_datasets -> list_tables -> get_table_schema -> estimate_query_cost -> run_query.
Every query is dry-run first: only a single SELECT on allowlisted datasets, under a byte
budget, is executed. Always use fully qualified table names: `project.dataset.table`.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

DatasetArg = Annotated[
    str, Field(description="Fully qualified `project.dataset`, as returned by `list_datasets`.")
]
SqlArg = Annotated[
    str,
    Field(
        description="One GoogleSQL SELECT statement, with fully qualified table names "
        "(`project.dataset.table`)."
    ),
]


def create_server(settings: Settings, client: bigquery.Client | None = None) -> MCPServer:
    if client is None:
        client = bigquery.Client(
            project=settings.bq_billing_project,
            location=settings.bq_location,
            client_info=ClientInfo(user_agent=f"ask-bigquery-mcp/{__version__}"),  # type: ignore[no-untyped-call]
        )
    warehouse = Warehouse(client, settings)
    mcp = MCPServer(name="ask-bigquery", version=__version__, instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    def list_datasets() -> list[DatasetInfo]:
        """List the BigQuery datasets this server may access.

        Start here. Only these datasets can be explored or queried; anything else is refused.
        """
        with tool_errors():
            return warehouse.list_datasets()

    @mcp.tool(annotations=READ_ONLY)
    def list_tables(dataset: DatasetArg) -> list[TableInfo]:
        """List the tables and views of a dataset, with their description and size.

        Use the row counts and sizes to spot the large tables, whose queries need filters.
        """
        with tool_errors():
            return warehouse.list_tables(dataset)

    @mcp.tool(annotations=READ_ONLY)
    def get_table_schema(
        dataset: DatasetArg,
        table: Annotated[str, Field(description="Table name, as returned by `list_tables`.")],
    ) -> TableSchema:
        """Describe a table: columns (name, type, mode, description), partitioning,
        clustering, and a few sample rows.

        Read it before writing SQL against the table. Filtering on the partitioning column
        (and then on the clustering columns) reduces the bytes scanned, hence the cost.
        REPEATED columns are ARRAYs (use UNNEST); RECORD columns have sub-fields (use dots).
        """
        with tool_errors():
            return warehouse.get_table_schema(dataset, table)

    @mcp.tool(annotations=READ_ONLY)
    def estimate_query_cost(sql: SqlArg) -> CostEstimate:
        """Dry-run a query: bytes it would scan, estimated cost, and whether `run_query`
        would accept it (and if not, why). Nothing is executed and nothing is billed.

        Call it before `run_query` for any query on a large table. If `allowed` is false,
        fix the query according to `reason` (e.g. select fewer columns, add a filter on the
        partitioning column) and estimate again.
        """
        with tool_errors():
            return warehouse.estimate_query_cost(sql)

    @mcp.tool(annotations=READ_ONLY)
    def run_query(
        sql: SqlArg,
        max_rows: Annotated[
            int, Field(ge=1, description="Maximum rows to return. Prefer aggregates to raw rows.")
        ] = 100,
    ) -> QueryResult:
        """Run a read-only SQL query on BigQuery and return its rows.

        The query is dry-run first and refused unless it is a single SELECT reading only
        allowlisted datasets, under the byte budget (see `estimate_query_cost`). The number
        of rows returned is capped by the server; `truncated` tells if rows were cut.
        Aggregate in SQL (GROUP BY, COUNT, SUM...) rather than fetching raw rows.
        """
        with tool_errors():
            return warehouse.run_query(sql, max_rows)

    @mcp.prompt()
    def analyze_question(
        question: Annotated[str, Field(description="A business question about the data.")],
    ) -> str:
        """Answer a business question from BigQuery data, step by step and within budget."""
        return f"""\
Answer this question using the ask-bigquery tools: {question}

1. Explore: call `list_datasets`, then `list_tables` on the relevant dataset, then
   `get_table_schema` on each table you plan to use. Do not guess column names.
2. Write a single GoogleSQL SELECT with fully qualified table names. Aggregate in SQL and
   select only the columns you need; filter on partitioning columns when there are some.
3. Call `estimate_query_cost`. If `allowed` is false, revise the query and estimate again.
4. Call `run_query`. If BigQuery returns an error, read it, fix the query, and retry.
5. Answer in plain language, give the key numbers, and show the SQL you ran.
   Say so if the result was truncated or if the data cannot answer the question.
"""

    return mcp


@contextmanager
def tool_errors() -> Iterator[None]:
    """Turn expected failures into `ToolError`: the LLM gets a clear message, no stack trace."""
    try:
        yield
    except AskBigQueryError as error:
        raise ToolError(str(error)) from error
    except GoogleAPICallError as error:  # e.g. NotFound on a table name
        raise ToolError(str(QueryError.from_api(error))) from error

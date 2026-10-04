from typing import cast

import pytest
from google.cloud import bigquery
from mcp.server.mcpserver import MCPServer

from ask_bigquery.config import Settings
from ask_bigquery.server import create_server
from tests.fakes import FakeClient, make_settings, thelook_client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client() -> FakeClient:
    return thelook_client()


@pytest.fixture
def server(settings: Settings, client: FakeClient) -> MCPServer:
    return create_server(settings, cast(bigquery.Client, client))

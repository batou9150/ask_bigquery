"""Launch the server as a subprocess and talk to it with the MCP SDK client, over stdio and HTTP."""

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from tests.fakes import DATASET, GROUP_BY

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).parents[2]
SERVER = [sys.executable, "-m", "tests.e2e.fake_server"]


async def exercise(client: Client) -> None:
    tools = await client.list_tools()
    assert {tool.name for tool in tools.tools} >= {"list_datasets", "run_query"}

    datasets = await client.call_tool("list_datasets", {})
    assert not datasets.is_error
    assert datasets.structured_content == {
        "result": [
            {"dataset": DATASET, "location": "US", "description": "Fictitious e-commerce data"}
        ]
    }

    result = await client.call_tool("run_query", {"sql": GROUP_BY})
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["columns"] == ["status", "n"]

    refused = await client.call_tool("list_tables", {"dataset": "secret-project.hr"})
    assert refused.is_error


async def test_stdio() -> None:
    params = StdioServerParameters(command=SERVER[0], args=SERVER[1:], cwd=ROOT)
    async with Client(params) as client:
        await exercise(client)


@pytest.fixture
def http_server() -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([*SERVER, "http", str(port)], cwd=ROOT, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("HTTP server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        process.wait(timeout=10)


async def test_streamable_http(http_server: str) -> None:
    async with Client(http_server) as client:
        await exercise(client)

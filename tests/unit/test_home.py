import pytest
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from ask_bigquery.home import render_home
from tests.fakes import DATASET, make_settings


def test_home_page_is_served_over_http(server: MCPServer) -> None:
    with TestClient(server.streamable_http_app()) as http:
        response = http.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f"<code>{DATASET}</code>" in response.text
    assert "1.00 GB" in response.text
    assert "claude mcp add --transport http bigquery" in response.text


def test_home_page_shows_the_proxy_command_on_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K_SERVICE", "ask-bigquery")
    monkeypatch.setenv("CLOUD_RUN_REGION", "europe-west1")
    page = render_home(make_settings())
    assert (
        "gcloud run services proxy ask-bigquery --region europe-west1 "
        "--project billing-project --port 3000"
    ) in page


def test_home_page_without_cloud_run_needs_no_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert "no tunnel is needed" in render_home(make_settings())


def test_home_page_escapes_configuration_values() -> None:
    settings = make_settings(bq_allowed_datasets=frozenset({"p.<script>"}))
    page = render_home(settings)
    assert "<code>p.&lt;script&gt;</code>" in page

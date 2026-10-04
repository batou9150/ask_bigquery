"""Command line entry point: `ask-bigquery [--transport stdio|http]`."""

from __future__ import annotations

import argparse
import sys

from google.cloud import bigquery
from pydantic import ValidationError

from ask_bigquery.config import Settings
from ask_bigquery.server import create_server


def serve(settings: Settings, client: bigquery.Client | None = None) -> None:
    server = create_server(settings, client)
    if settings.mcp_transport == "stdio":
        server.run("stdio")
    else:
        # Stateless + JSON responses: any Cloud Run instance can answer any request.
        server.run(
            "streamable-http",
            host=settings.host,
            port=settings.port,
            stateless_http=True,
            json_response=True,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ask-bigquery", description="Read-only BigQuery MCP server with guardrails."
    )
    parser.add_argument("--transport", choices=["stdio", "http"], help="default: stdio")
    parser.add_argument("--host", help="HTTP bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="HTTP port (default: $PORT or 8080)")
    args = parser.parse_args(argv)

    overrides = {"mcp_transport": args.transport, "host": args.host, "port": args.port}
    try:
        settings = Settings(**{key: value for key, value in overrides.items() if value})
    except ValidationError as error:
        problems = "\n".join(
            f"  {'.'.join(map(str, e['loc'])).upper()}: {e['msg']}" for e in error.errors()
        )
        sys.exit(f"ask-bigquery: invalid configuration\n{problems}")
    serve(settings)


if __name__ == "__main__":
    main()

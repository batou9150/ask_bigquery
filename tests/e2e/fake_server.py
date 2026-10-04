"""Run the real server entry point with the fake BigQuery client (used by the e2e tests)."""

import sys
from typing import cast

from google.cloud import bigquery

from ask_bigquery.__main__ import serve
from tests.fakes import make_settings, thelook_client

if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    settings = make_settings(mcp_transport=transport, port=port)
    serve(settings, cast(bigquery.Client, thelook_client()))

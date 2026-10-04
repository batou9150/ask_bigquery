"""The home page served at `/` over HTTP: how to connect an MCP client to this deployment.

On Cloud Run, users reach it through `gcloud run services proxy`, so the page shows the
values of this deployment (allowlist, limits, proxy command). The MCP URL is filled in
by the browser from `location.origin`, since behind the proxy only the browser knows it.
"""

from __future__ import annotations

import os
from html import escape

from ask_bigquery import __version__
from ask_bigquery.config import Settings
from ask_bigquery.guardrails import format_bytes

REPO_URL = "https://github.com/batou9150/ask_bigquery"
URL_PLACEHOLDER = "http://localhost:3000/mcp"  # replaced by the script below


def render_home(settings: Settings) -> str:
    datasets = "".join(
        f"<li><code>{escape(dataset)}</code></li>"
        for dataset in sorted(settings.bq_allowed_datasets)
    )
    return PAGE.format(
        version=escape(__version__),
        datasets=datasets,
        max_bytes=escape(format_bytes(settings.bq_max_bytes_billed)),
        max_rows=settings.bq_max_rows,
        timeout=f"{settings.bq_job_timeout_s:g}",
        location=escape(settings.bq_location),
        tunnel=tunnel_step(settings),
        url=URL_PLACEHOLDER,
        repo=REPO_URL,
    )


def tunnel_step(settings: Settings) -> str:
    service = os.environ.get("K_SERVICE")  # set by Cloud Run
    if not service:
        return (
            "<p>This server runs locally over HTTP: no tunnel is needed. "
            "Point your client at the URL below.</p>"
        )
    region = os.environ.get("CLOUD_RUN_REGION", "REGION")
    command = (
        f"gcloud run services proxy {service} --region {region} "
        f"--project {settings.bq_billing_project} --port 3000"
    )
    return f"""
<p>The service is private. Your client reaches it through an authenticated tunnel, which
must stay open while you use it. If you can read this page, the tunnel works.</p>
<ol>
  <li>Install the <a href="https://cloud.google.com/sdk/docs/install">gcloud CLI</a>,
    then <code>gcloud auth login</code>.</li>
  <li>Ask the service owner for <code>roles/run.invoker</code> on <code>{escape(service)}</code>.</li>
  <li>Open the tunnel (keep this terminal open):</li>
</ol>
<div class="code"><pre>{escape(command)}</pre><button>Copy</button></div>"""


PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ask-bigquery setup</title>
<link rel="icon" href="data:,">
<style>
  :root {{
    --bg: #fafaf9; --fg: #1c1917; --muted: #57534e; --card: #ffffff;
    --border: #e7e5e4; --code: #f5f5f4; --accent: #1d4ed8; --ok: #15803d;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0c0a09; --fg: #e7e5e4; --muted: #a8a29e; --card: #1c1917;
      --border: #292524; --code: #292524; --accent: #93c5fd; --ok: #4ade80;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  main {{ max-width: 760px; margin: 0 auto; padding: 40px 16px 64px; }}
  h1 {{ font-size: 1.8rem; margin: 0 0 4px; }}
  h2 {{ font-size: 1.15rem; margin: 0 0 12px; }}
  h3 {{ font-size: 1rem; margin: 20px 0 8px; }}
  p, li {{ color: var(--muted); }}
  a {{ color: var(--accent); }}
  .lead {{ margin: 0 0 8px; }}
  .ok {{ color: var(--ok); font-weight: 600; }}
  section {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 24px; margin-top: 20px;
  }}
  .step {{ color: var(--accent); font-weight: 700; margin-right: 6px; }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 12px 0 0; }}
  dt {{ color: var(--muted); }}
  dd {{ margin: 0; }}
  code {{ font: 0.9em ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .code {{ position: relative; }}
  pre {{
    background: var(--code); border-radius: 8px; padding: 12px 14px; margin: 8px 0;
    white-space: pre-wrap; overflow-wrap: anywhere;
    font: 0.85rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .code pre {{ padding-right: 72px; }}
  .code button {{
    position: absolute; top: 6px; right: 6px; font: inherit; font-size: 0.75rem;
    padding: 2px 8px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--card); color: var(--fg); cursor: pointer;
  }}
  @media (max-width: 600px) {{
    main {{ padding-top: 24px; }}
    section {{ padding: 16px; }}
    .code pre {{ padding-right: 14px; padding-top: 36px; }}
  }}
</style>
</head>
<body>
<main>
  <h1>ask-bigquery</h1>
  <p class="lead">Read-only BigQuery MCP server, v{version}. <span class="ok">✓ Server is up.</span></p>
  <p>Your AI assistant writes the SQL; this server runs it on BigQuery, after checking that
  it is a single read-only <code>SELECT</code>, on the datasets below, within budget.</p>

  <section>
    <h2>What this server can access</h2>
    <ul>{datasets}</ul>
    <dl>
      <dt>Max scanned per query</dt><dd>{max_bytes}</dd>
      <dt>Max rows returned</dt><dd>{max_rows}</dd>
      <dt>Query timeout</dt><dd>{timeout} s</dd>
      <dt>BigQuery location</dt><dd>{location}</dd>
    </dl>
  </section>

  <section>
    <h2><span class="step">1</span>Open the tunnel</h2>
    {tunnel}
  </section>

  <section>
    <h2><span class="step">2</span>Add the server to your MCP client</h2>
    <p>MCP endpoint: <code class="mcp-url">{url}</code></p>

    <h3>Claude Code</h3>
    <div class="code"><pre>claude mcp add --transport http bigquery <span class="mcp-url">{url}</span></pre><button>Copy</button></div>

    <h3>Gemini CLI</h3>
    <p>In <code>~/.gemini/settings.json</code>:</p>
    <div class="code"><pre>{{
  "mcpServers": {{
    "bigquery": {{ "httpUrl": "<span class="mcp-url">{url}</span>" }}
  }}
}}</pre><button>Copy</button></div>

    <h3>Claude Desktop</h3>
    <p>In <code>claude_desktop_config.json</code> (Settings → Developer → Edit Config), through the
    <a href="https://www.npmjs.com/package/mcp-remote">mcp-remote</a> bridge (needs Node.js):</p>
    <div class="code"><pre>{{
  "mcpServers": {{
    "bigquery": {{
      "command": "npx",
      "args": ["-y", "mcp-remote", "<span class="mcp-url">{url}</span>"]
    }}
  }}
}}</pre><button>Copy</button></div>
  </section>

  <section>
    <h2><span class="step">3</span>Ask a question</h2>
    <p>Restart your client, check that the <code>bigquery</code> server is connected
    (<code>/mcp</code> in Claude Code), then ask a question about your data, for example:</p>
    <pre>What are the 5 product categories with the most revenue last year?</pre>
    <p>In Claude Code, the <code>/mcp__bigquery__analyze_question</code> prompt guides the
    assistant step by step: explore the schema, estimate the cost, then run the query.</p>
  </section>

  <p>Source, guardrails and self-hosting: <a href="{repo}">{repo}</a></p>
</main>
<script>
  const url = location.origin + "/mcp";
  document.querySelectorAll(".mcp-url").forEach((el) => (el.textContent = url));
  document.querySelectorAll(".code button").forEach((button) => {{
    button.addEventListener("click", async () => {{
      try {{
        await navigator.clipboard.writeText(button.previousElementSibling.textContent);
        button.textContent = "Copied";
      }} catch {{
        button.textContent = "Select and copy";
      }}
      setTimeout(() => (button.textContent = "Copy"), 1500);
    }});
  }});
</script>
</body>
</html>
"""

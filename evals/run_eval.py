"""Ask an LLM 10 questions about thelook_ecommerce through this MCP server, and score the answers.

The LLM is Claude Code in headless mode (`claude -p`), connected to the server over stdio.
Run by hand, it is not part of CI (it needs a billing project and costs a few cents):

    BQ_BILLING_PROJECT=my-project uv run python evals/run_eval.py [--model sonnet] [--only id,...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from google.cloud import bigquery

from ask_bigquery.config import Settings
from ask_bigquery.warehouse import Warehouse

ROOT = Path(__file__).resolve().parents[1]
DATASET = "bigquery-public-data.thelook_ecommerce"
PROMPT = """\
Answer this question about the BigQuery dataset `{dataset}`, using the bigquery tools: {question}
End your reply with a last line of the form `ANSWER: <value>`, with only the value \
(a number without units or thousands separators, or a name)."""


@dataclass
class Outcome:
    id: str
    expected: str
    answer: str | None
    passed: bool
    seconds: float
    cost_usd: float
    turns: int


def ask_claude(question: str, project: str, model: str) -> dict[str, Any]:
    mcp_config = {
        "mcpServers": {
            "bigquery": {
                "command": "uv",
                "args": ["run", "--project", str(ROOT), "ask-bigquery"],
                "env": {"BQ_BILLING_PROJECT": project, "BQ_ALLOWED_DATASETS": DATASET},
            }
        }
    }
    command = [
        "claude", "-p", PROMPT.format(dataset=DATASET, question=question),
        "--model", model,
        "--output-format", "json",
        "--mcp-config", json.dumps(mcp_config),
        "--strict-mcp-config",
        "--allowedTools", "mcp__bigquery",
    ]  # fmt: skip
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600, check=True)
    result: dict[str, Any] = json.loads(completed.stdout)
    return result


def extract_answer(text: str) -> str | None:
    matches = re.findall(r"^\W*ANSWER:\W*(.+?)\W*$", text, flags=re.MULTILINE | re.IGNORECASE)
    return matches[-1] if matches else None


def is_correct(answer: str | None, expected: str, kind: str) -> bool:
    if answer is None:
        return False
    if kind == "text":
        return answer.strip().lower() == expected.strip().lower()
    try:
        value = float(re.sub(r"[^\d.\-]", "", answer))
    except ValueError:
        return False
    return abs(value - float(expected)) <= max(0.01, abs(float(expected)) * 0.01)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--only", help="comma-separated question ids")
    args = parser.parse_args()

    project = os.environ.get("BQ_BILLING_PROJECT") or sys.exit("set BQ_BILLING_PROJECT")
    questions = yaml.safe_load((ROOT / "evals" / "questions.yaml").read_text())
    if args.only:
        questions = [q for q in questions if q["id"] in args.only.split(",")]

    settings = Settings(bq_billing_project=project, bq_allowed_datasets=frozenset({DATASET}))
    warehouse = Warehouse(bigquery.Client(project=project), settings)

    outcomes = []
    for q in questions:
        reference = warehouse.run_query(q["reference_sql"], max_rows=1)
        expected = str(next(iter(reference.rows[0].values())))
        start = time.monotonic()
        result = ask_claude(q["question"], project, args.model)
        answer = extract_answer(result.get("result", ""))
        outcome = Outcome(
            id=q["id"],
            expected=expected,
            answer=answer,
            passed=is_correct(answer, expected, q["kind"]),
            seconds=round(time.monotonic() - start, 1),
            cost_usd=float(result.get("total_cost_usd", 0)),
            turns=int(result.get("num_turns", 0)),
        )
        outcomes.append(outcome)
        print(
            f"{'PASS' if outcome.passed else 'FAIL'}  {q['id']}: {answer!r} (expected {expected!r})"
        )

    passed = sum(o.passed for o in outcomes)
    print(f"\n**{passed}/{len(outcomes)} correct** with `{args.model}`\n")
    print("| question | expected | answer | ok | turns | time (s) | LLM cost ($) |")
    print("|---|---|---|---|---|---|---|")
    for o in outcomes:
        mark = "✅" if o.passed else "❌"
        print(
            f"| {o.id} | {o.expected} | {o.answer} | {mark} | {o.turns} | {o.seconds} "
            f"| {o.cost_usd:.3f} |"
        )


if __name__ == "__main__":
    main()

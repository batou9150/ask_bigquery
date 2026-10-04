"""Errors that are safe to show to the LLM: one clear sentence, no stack trace."""

from __future__ import annotations


class AskBigQueryError(Exception):
    """Base class. `str(error)` is what the MCP client (and the LLM) will read."""

    code = "ERROR"

    def __str__(self) -> str:
        return f"[{self.code}] {self.args[0] if self.args else ''}"


class GuardrailError(AskBigQueryError):
    """The request was refused by a guardrail, before anything ran on BigQuery."""

    code = "REFUSED"


class QueryError(AskBigQueryError):
    """BigQuery rejected the query (syntax error, unknown column, quota...)."""

    code = "BIGQUERY_ERROR"

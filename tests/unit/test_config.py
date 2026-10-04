import pytest
from pydantic import ValidationError

from ask_bigquery.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/")  # ignore any local .env
    monkeypatch.setenv("BQ_BILLING_PROJECT", "billing")


def test_allowlist_is_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BQ_ALLOWED_DATASETS", " p1.d1 , p2.d2,, ")
    assert Settings().bq_allowed_datasets == {"p1.d1", "p2.d2"}


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BQ_ALLOWED_DATASETS", "p.d")
    settings = Settings()
    assert settings.bq_max_bytes_billed == 1_000_000_000
    assert settings.bq_sample_rows == 3
    assert settings.mcp_transport == "stdio"


@pytest.mark.parametrize("value", ["", "dataset_only", "a.b.c", "p."])
def test_invalid_allowlist_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BQ_ALLOWED_DATASETS", value)
    with pytest.raises(ValidationError):
        Settings()


def test_allowlist_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BQ_ALLOWED_DATASETS", raising=False)
    with pytest.raises(ValidationError):
        Settings()

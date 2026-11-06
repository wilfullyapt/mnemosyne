"""Unit tests that do not require Postgres."""

from __future__ import annotations

import pytest

from mnemosyne.db import (
    DatabaseConfigError,
    normalize_database_url,
    redacted_database_url,
    require_database_url,
)
from mnemosyne.migrate import list_migrations
from mnemosyne.textutil import (
    propose_claims_from_passage,
    sha256_text,
    split_passages,
)


def test_sha256_stable() -> None:
    assert sha256_text("abc") == sha256_text("abc")
    assert len(sha256_text("abc")) == 64


def test_split_passages_paragraphs() -> None:
    text = (
        "First paragraph stays together.\n\n"
        "Second paragraph is separate and longer than the first."
    )
    parts = split_passages(text, max_chars=80)
    assert len(parts) >= 1
    assert all(parts)


def test_propose_claims_deterministic() -> None:
    body = (
        "Compound interest is the eighth wonder of the world. "
        "Patience turns deposits into outcomes over decades."
    )
    a = propose_claims_from_passage(body, max_claims=2)
    b = propose_claims_from_passage(body, max_claims=2)
    assert a == b
    assert a
    assert all(len(s) >= 24 for s in a)


def test_normalize_database_url() -> None:
    url = normalize_database_url("postgres://u:p@localhost:5432/mnemosyne")
    assert url.startswith("postgresql://")
    assert url.endswith("/mnemosyne")


def test_normalize_rejects_missing_db() -> None:
    with pytest.raises(DatabaseConfigError):
        normalize_database_url("postgresql://u:p@localhost:5432")


def test_redacted_hides_password() -> None:
    red = redacted_database_url("postgresql://alice:s3cret@db.example:5432/mnemosyne")
    assert "s3cret" not in red
    assert "***" in red
    assert "alice" in red


def test_require_database_url_missing() -> None:
    with pytest.raises(DatabaseConfigError):
        require_database_url({})


def test_migrations_packaged() -> None:
    items = list_migrations()
    assert items
    versions = [v for v, _, _ in items]
    assert versions == sorted(versions)
    assert any(name.startswith("001_") for _, name, _ in items)

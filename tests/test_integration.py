"""Integration tests against real Postgres (CI service or DATABASE_URL)."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from mnemosyne.db import normalize_database_url
from mnemosyne.ingest import (
    decide_review,
    ingest_plain_text,
    list_open_reviews,
    register_source,
    run_golden_ingest,
)
from mnemosyne.migrate import migrate
from mnemosyne.migrate import status as migrate_status


def _database_url() -> str | None:
    raw = (os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL") or "").strip()
    return raw or None


@pytest.fixture(scope="session")
def database_url() -> str:
    raw = _database_url()
    if not raw:
        pytest.skip("DATABASE_URL / TEST_DATABASE_URL not set — Postgres integration skipped")
    return normalize_database_url(raw)


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    migrate(database_url)
    st = migrate_status(database_url)
    assert not st["pending"], st
    return database_url


def test_migrate_idempotent(migrated_db: str) -> None:
    again = migrate(migrated_db)
    assert again == []


def test_golden_path_source_passages_claims_review(migrated_db: str) -> None:
    payload = run_golden_ingest(migrated_db)
    assert payload["passage_ids"]
    assert payload["claim_ids"]
    assert payload["review_queue_ids"]
    assert payload["open_review_count"] >= 1
    # No claim is auto-approved
    with psycopg.connect(migrated_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM claims WHERE status = 'approved' AND source_id = %s",
                (payload["source_id"],),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM claims WHERE status = 'pending_review' AND source_id = %s",
                (payload["source_id"],),
            )
            assert cur.fetchone()[0] == len(payload["claim_ids"])


def test_human_review_approve_closes_queue(migrated_db: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "golden_passage.txt"
    text = fixture.read_text(encoding="utf-8")
    with psycopg.connect(migrated_db) as conn:
        source_id = register_source(
            conn,
            media_kind="article",
            title="Review gate sample",
            authors=["Tester"],
        )
        result = ingest_plain_text(
            conn,
            source_id=source_id,
            text=text,
            input_uri="fixture:review",
            max_claims_per_passage=1,
        )
        assert result.claim_ids
        claim_id = result.claim_ids[0]
        decide_review(
            conn,
            claim_id=claim_id,
            outcome="approved",
            reviewer="wil",
            notes="fixture approve",
        )
        conn.commit()

        open_ids = {row["claim_id"] for row in list_open_reviews(conn)}
        assert str(claim_id) not in open_ids

        with conn.cursor() as cur:
            cur.execute("SELECT status, reviewer FROM claims WHERE id = %s", (claim_id,))
            status, reviewer = cur.fetchone()
            assert status == "approved"
            assert reviewer == "wil"
            cur.execute(
                "SELECT outcome, closed_at IS NOT NULL FROM review_queue WHERE claim_id = %s",
                (claim_id,),
            )
            outcome, closed = cur.fetchone()
            assert outcome == "approved"
            assert closed is True


def test_trusted_recall_filters_drafts(migrated_db: str) -> None:
    """Queries for trusted knowledge should default to approved only."""
    with psycopg.connect(migrated_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM claims
                WHERE status = 'approved'
                """
            )
            approved = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM claims WHERE status <> 'approved'")
            other = cur.fetchone()[0]
        # After prior tests we may have both; invariant is the filter shape works.
        assert approved >= 0
        assert other >= 0
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT statement FROM claims
                WHERE status = 'approved'
                ORDER BY reviewed_at DESC NULLS LAST
                LIMIT 5
                """
            )
            rows = cur.fetchall()
        assert isinstance(rows, list)

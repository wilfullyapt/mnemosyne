"""Ingest path: source → passages → draft claims → review_queue."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mnemosyne import __version__
from mnemosyne.db import require_database_url
from mnemosyne.textutil import (
    estimate_tokens,
    propose_claims_from_passage,
    sha256_bytes,
    sha256_text,
    split_passages,
)

PARSER_NAME = "mnemosyne.plain_text"
PARSER_VERSION = __version__


@dataclass(frozen=True)
class IngestResult:
    source_id: UUID
    batch_id: UUID
    passage_ids: list[UUID]
    claim_ids: list[UUID]
    review_queue_ids: list[UUID]
    stats: dict[str, Any]


def register_source(
    conn: psycopg.Connection,
    *,
    media_kind: str,
    title: str,
    authors: Sequence[str] | None = None,
    language: str = "en",
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
    isbn_or_sku: str | None = None,
    external_url: str | None = None,
    series_title: str | None = None,
) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (
              media_kind, title, authors, language, notes, metadata,
              isbn_or_sku, external_url, series_title
            )
            VALUES (
              %s::media_kind, %s, %s, %s, %s, %s,
              %s, %s, %s
            )
            RETURNING id
            """,
            (
                media_kind,
                title.strip(),
                list(authors or ()),
                language,
                notes,
                Jsonb(metadata or {}),
                isbn_or_sku,
                external_url,
                series_title,
            ),
        )
        return cur.fetchone()[0]


def _create_batch(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    input_uri: str | None,
    input_sha256: str | None,
) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_batches (
              source_id, status, parser_name, parser_version, input_uri, input_sha256
            )
            VALUES (%s, 'parsing'::ingest_status, %s, %s, %s, %s)
            RETURNING id
            """,
            (source_id, PARSER_NAME, PARSER_VERSION, input_uri, input_sha256),
        )
        return cur.fetchone()[0]


def _finish_batch(
    conn: psycopg.Connection,
    batch_id: UUID,
    *,
    status: str,
    stats: dict[str, Any],
    error_summary: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest_batches
            SET status = %s::ingest_status,
                stats = %s,
                error_summary = %s,
                finished_at = now()
            WHERE id = %s
            """,
            (status, Jsonb(stats), error_summary, batch_id),
        )


def _insert_passage(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    batch_id: UUID,
    locator: str,
    sequence_no: int,
    body: str,
) -> UUID:
    digest = sha256_text(body)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO passages (
              source_id, batch_id, locator, sequence_no, body, body_sha256, token_estimate
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, locator, body_sha256) DO UPDATE
              SET sequence_no = EXCLUDED.sequence_no
            RETURNING id
            """,
            (
                source_id,
                batch_id,
                locator,
                sequence_no,
                body,
                digest,
                estimate_tokens(body),
            ),
        )
        return cur.fetchone()[0]


def enqueue_draft_claim(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    passage_id: UUID,
    statement: str,
    confidence: float | None = None,
    priority: int = 100,
) -> tuple[UUID, UUID]:
    """Insert claim at draft → pending_review and open a review_queue row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO claims (
              source_id, primary_passage_id, statement, confidence, status
            )
            VALUES (%s, %s, %s, %s, 'pending_review'::review_status)
            RETURNING id
            """,
            (source_id, passage_id, statement.strip(), confidence),
        )
        claim_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO claim_passages (claim_id, passage_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (claim_id, passage_id),
        )
        cur.execute(
            """
            INSERT INTO review_queue (claim_id, priority)
            VALUES (%s, %s)
            RETURNING id
            """,
            (claim_id, priority),
        )
        queue_id = cur.fetchone()[0]
    return claim_id, queue_id


def ingest_plain_text(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    text: str,
    input_uri: str | None = None,
    input_sha256: str | None = None,
    locator_prefix: str = "p",
    max_passage_chars: int = 1200,
    max_claims_per_passage: int = 2,
    propose_claims: bool = True,
) -> IngestResult:
    """
    Parse plain text into passages; optionally propose draft claims + review rows.

    Claims always land as pending_review with a review_queue entry — never auto-approved.
    """
    digest = input_sha256 or sha256_text(text)
    batch_id = _create_batch(
        conn, source_id=source_id, input_uri=input_uri, input_sha256=digest
    )
    passage_ids: list[UUID] = []
    claim_ids: list[UUID] = []
    review_ids: list[UUID] = []

    try:
        chunks = split_passages(text, max_chars=max_passage_chars)
        for idx, body in enumerate(chunks, start=1):
            locator = f"{locator_prefix}.{idx}"
            pid = _insert_passage(
                conn,
                source_id=source_id,
                batch_id=batch_id,
                locator=locator,
                sequence_no=idx,
                body=body,
            )
            passage_ids.append(pid)
            if propose_claims:
                for statement in propose_claims_from_passage(
                    body, max_claims=max_claims_per_passage
                ):
                    cid, rid = enqueue_draft_claim(
                        conn,
                        source_id=source_id,
                        passage_id=pid,
                        statement=statement,
                    )
                    claim_ids.append(cid)
                    review_ids.append(rid)

        stats = {
            "passage_count": len(passage_ids),
            "claim_count": len(claim_ids),
            "review_queue_count": len(review_ids),
            "input_sha256": digest,
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
        }
        _finish_batch(conn, batch_id, status="parsed", stats=stats)
        return IngestResult(
            source_id=source_id,
            batch_id=batch_id,
            passage_ids=passage_ids,
            claim_ids=claim_ids,
            review_queue_ids=review_ids,
            stats=stats,
        )
    except Exception as exc:  # noqa: BLE001 — record failure then re-raise
        _finish_batch(
            conn,
            batch_id,
            status="failed",
            stats={"error": type(exc).__name__},
            error_summary=str(exc)[:2000],
        )
        raise


def ingest_file(
    conn: psycopg.Connection,
    *,
    source_id: UUID,
    path: Path,
    **kwargs: Any,
) -> IngestResult:
    data = path.read_bytes()
    text = data.decode("utf-8")
    return ingest_plain_text(
        conn,
        source_id=source_id,
        text=text,
        input_uri=str(path),
        input_sha256=sha256_bytes(data),
        **kwargs,
    )


def list_open_reviews(conn: psycopg.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              rq.id AS queue_id,
              rq.priority,
              rq.opened_at,
              c.id AS claim_id,
              c.statement,
              c.status,
              c.source_id,
              c.primary_passage_id,
              s.title AS source_title
            FROM review_queue rq
            JOIN claims c ON c.id = rq.claim_id
            JOIN sources s ON s.id = c.source_id
            WHERE rq.closed_at IS NULL
            ORDER BY rq.priority ASC, rq.opened_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    # UUID → str for JSON friendliness
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key, val in list(item.items()):
            if isinstance(val, UUID):
                item[key] = str(val)
        out.append(item)
    return out


def decide_review(
    conn: psycopg.Connection,
    *,
    claim_id: UUID,
    outcome: str,
    reviewer: str,
    notes: str | None = None,
) -> None:
    """Human gate: approve | reject | superseded."""
    if outcome not in {"approved", "rejected", "superseded"}:
        raise ValueError("outcome must be approved|rejected|superseded")
    if not reviewer.strip():
        raise ValueError("reviewer is required")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE claims
            SET status = %s::review_status,
                reviewer = %s,
                reviewed_at = now(),
                review_notes = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (outcome, reviewer.strip(), notes, claim_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"claim not found: {claim_id}")
        cur.execute(
            """
            UPDATE review_queue
            SET closed_at = now(),
                outcome = %s::review_status
            WHERE claim_id = %s AND closed_at IS NULL
            """,
            (outcome, claim_id),
        )


def run_golden_ingest(database_url: str | None = None) -> dict[str, Any]:
    """End-to-end golden fixture path used by CLI and tests."""
    url = database_url or require_database_url()
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden_passage.txt"
    if not fixture.is_file():
        # Packaged fallback: small embedded sample
        text = (
            "Compound interest is the eighth wonder of the world. "
            "He who understands it, earns it; he who doesn't, pays it. "
            "Patience turns small consistent deposits into large outcomes over decades."
        )
        input_uri = "embedded:golden"
        digest = sha256_text(text)
    else:
        data = fixture.read_bytes()
        text = data.decode("utf-8")
        input_uri = str(fixture)
        digest = sha256_bytes(data)

    with psycopg.connect(url) as conn:
        source_id = register_source(
            conn,
            media_kind="book",
            title="Mnemosyne Golden Fixture",
            authors=["Fixture Author"],
            notes="Golden-path demo only — not a bulk library ingest.",
            metadata={"fixture": True, "suite": "golden"},
        )
        result = ingest_plain_text(
            conn,
            source_id=source_id,
            text=text,
            input_uri=input_uri,
            input_sha256=digest,
            locator_prefix="g",
        )
        open_reviews = list_open_reviews(conn)
        conn.commit()

    return {
        "source_id": str(result.source_id),
        "batch_id": str(result.batch_id),
        "passage_ids": [str(x) for x in result.passage_ids],
        "claim_ids": [str(x) for x in result.claim_ids],
        "review_queue_ids": [str(x) for x in result.review_queue_ids],
        "stats": result.stats,
        "open_review_count": len(open_reviews),
        "sample_open_reviews": open_reviews[:5],
    }


def dumps_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)

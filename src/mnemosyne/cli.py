"""CLI: migrate, ingest golden path, list/decide reviews."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

import psycopg

from mnemosyne import __version__
from mnemosyne.db import DatabaseConfigError, redacted_database_url, require_database_url
from mnemosyne.ingest import (
    decide_review,
    dumps_result,
    ingest_file,
    list_open_reviews,
    register_source,
    run_golden_ingest,
)
from mnemosyne.migrate import list_migrations, migrate
from mnemosyne.migrate import status as migrate_status


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemosyne",
        description="Mnemosyne knowledge-base foundation (schema + ingest + review).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_migrate = sub.add_parser("migrate", help="Apply pending SQL migrations")
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show pending migrations without applying",
    )

    sub.add_parser("migrate-status", help="Show applied vs pending migrations")

    p_golden = sub.add_parser(
        "ingest-golden",
        help="Run golden fixture: source → passages → draft claims → review_queue",
    )
    p_golden.add_argument(
        "--no-migrate",
        action="store_true",
        help="Skip auto-migrate before ingest (default: migrate first)",
    )

    p_ingest = sub.add_parser("ingest-file", help="Ingest a UTF-8 text file into a new source")
    p_ingest.add_argument("path", type=Path)
    p_ingest.add_argument("--title", required=True)
    p_ingest.add_argument(
        "--media-kind",
        default="book",
        choices=[
            "book",
            "audiobook",
            "podcast_episode",
            "podcast_series",
            "article",
            "lecture",
            "other",
        ],
    )
    p_ingest.add_argument("--author", action="append", default=[])
    p_ingest.add_argument("--no-claims", action="store_true", help="Passages only")

    p_list = sub.add_parser("reviews", help="List open review_queue items")
    p_list.add_argument("--limit", type=int, default=50)

    p_decide = sub.add_parser("review-decide", help="Human gate on a claim")
    p_decide.add_argument("--claim-id", required=True, type=UUID)
    p_decide.add_argument(
        "--outcome",
        required=True,
        choices=["approved", "rejected", "superseded"],
    )
    p_decide.add_argument("--reviewer", required=True)
    p_decide.add_argument("--notes", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        url = require_database_url()
    except DatabaseConfigError as exc:
        if args.command in {"migrate", "migrate-status"} and args.command == "migrate":
            # still need URL for migrate; surface cleanly
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"database: {redacted_database_url(url)}", file=sys.stderr)

    if args.command == "migrate":
        if args.dry_run:
            st = migrate_status(url)
            for name in st["pending"]:
                print(name)
            if not st["pending"]:
                print("(no pending migrations)", file=sys.stderr)
            return 0
        applied = migrate(url)
        if not applied:
            print("already up to date")
        else:
            for name in applied:
                print(f"applied {name}")
        return 0

    if args.command == "migrate-status":
        st = migrate_status(url)
        print(dumps_result(st))
        # also list files for humans
        for version, name, _ in list_migrations():
            mark = "applied" if version in st["applied"] else "pending"
            print(f"{mark}\t{name}", file=sys.stderr)
        return 0

    if args.command == "ingest-golden":
        if not args.no_migrate:
            migrate(url)
        payload = run_golden_ingest(url)
        print(dumps_result(payload))
        return 0

    if args.command == "ingest-file":
        path: Path = args.path
        if not path.is_file():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2
        migrate(url)
        with psycopg.connect(url) as conn:
            source_id = register_source(
                conn,
                media_kind=args.media_kind,
                title=args.title,
                authors=args.author,
            )
            result = ingest_file(
                conn,
                source_id=source_id,
                path=path,
                propose_claims=not args.no_claims,
            )
            conn.commit()
            print(
                dumps_result(
                    {
                        "source_id": str(result.source_id),
                        "batch_id": str(result.batch_id),
                        "passage_ids": [str(x) for x in result.passage_ids],
                        "claim_ids": [str(x) for x in result.claim_ids],
                        "review_queue_ids": [str(x) for x in result.review_queue_ids],
                        "stats": result.stats,
                    }
                )
            )
        return 0

    if args.command == "reviews":
        with psycopg.connect(url) as conn:
            rows = list_open_reviews(conn, limit=args.limit)
        print(dumps_result({"open_reviews": rows, "count": len(rows)}))
        return 0

    if args.command == "review-decide":
        with psycopg.connect(url) as conn:
            decide_review(
                conn,
                claim_id=args.claim_id,
                outcome=args.outcome,
                reviewer=args.reviewer,
                notes=args.notes,
            )
            conn.commit()
        print(dumps_result({"claim_id": str(args.claim_id), "outcome": args.outcome}))
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

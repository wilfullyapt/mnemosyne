"""Plain-SQL migration runner with schema_migrations ledger."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import psycopg

from mnemosyne.db import require_database_url

MIGRATION_NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     integer PRIMARY KEY,
  name        text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  checksum    text
);
"""


def migrations_dir() -> Path:
    """Resolve packaged migrations/ directory (dev checkout or wheel)."""
    # Prefer repo-relative when developing from a checkout.
    here = Path(__file__).resolve()
    checkout = here.parents[2] / "migrations"
    if checkout.is_dir() and any(checkout.glob("*.sql")):
        return checkout
    # Packaged via hatch force-include → mnemosyne/migrations
    try:
        root = resources.files("mnemosyne").joinpath("migrations")
        if root.is_dir():
            return Path(str(root))
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass
    raise FileNotFoundError("Could not locate migrations/ directory")


def list_migrations(directory: Path | None = None) -> list[tuple[int, str, Path]]:
    """Return sorted (version, name, path) triples."""
    base = directory or migrations_dir()
    found: list[tuple[int, str, Path]] = []
    for path in sorted(base.glob("*.sql")):
        match = MIGRATION_NAME_RE.match(path.name)
        if not match:
            raise ValueError(
                f"Migration filename must match NNN_name.sql, got {path.name!r}"
            )
        version = int(match.group(1))
        found.append((version, path.name, path))
    versions = [v for v, _, _ in found]
    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate migration version numbers")
    return found


def applied_versions(conn: psycopg.Connection) -> set[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            )
            """
        )
        exists = cur.fetchone()[0]
        if not exists:
            return set()
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _file_checksum(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(database_url: str | None = None, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in order. Returns list of applied filenames."""
    url = database_url or require_database_url()
    pending = list_migrations(directory)
    applied_names: list[str] = []

    with psycopg.connect(url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP_SQL)
        conn.commit()

        already = applied_versions(conn)
        for version, name, path in pending:
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            checksum = _file_checksum(path)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO schema_migrations (version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (version, name, checksum),
                )
            conn.commit()
            applied_names.append(name)
            already.add(version)
    return applied_names


def status(database_url: str | None = None, directory: Path | None = None) -> dict:
    """Return migration status without applying."""
    url = database_url or require_database_url()
    pending = list_migrations(directory)
    with psycopg.connect(url) as conn:
        already = applied_versions(conn)
    return {
        "applied": sorted(already),
        "available": [v for v, _, _ in pending],
        "pending": [name for v, name, _ in pending if v not in already],
    }

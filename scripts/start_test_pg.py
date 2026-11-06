#!/usr/bin/env python3
"""Start ephemeral pgserver and write DATABASE_URL for tests."""

from __future__ import annotations

from pathlib import Path

from pgserver import get_server

SCRATCH = Path("/root/seneschal/.hermes/profiles/hephaestus/cache/scratch/mnemosyne-pg")
URL_FILE = Path("/root/seneschal/.hermes/profiles/hephaestus/cache/scratch/mnemosyne-test-url")


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    srv = get_server(SCRATCH, cleanup_mode=None)
    url = srv.get_uri(database="mnemosyne_test")
    # Create DB if needed via default postgres db
    import psycopg

    admin = srv.get_uri(database="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'mnemosyne_test'")
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE mnemosyne_test")
    URL_FILE.write_text(url, encoding="utf-8")
    print("pid", srv.get_pid())
    print("wrote", URL_FILE)
    print("ok")


if __name__ == "__main__":
    main()

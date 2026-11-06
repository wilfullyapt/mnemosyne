# Mnemosyne

Personal **library knowledge base** foundation: Postgres schema, plain-SQL migrations,
plain-text ingest, draft claims, and **human review gates**.

| Layer | Value |
|-------|--------|
| Product / repo | `mnemosyne` |
| Planned Hermes profile | `mnemosyne` (not created in this phase) |
| Reliquary dominion | `domains/memory` |
| Database name | `mnemosyne` |
| Ops host | Stronghold PostgreSQL CT (Lamassu) |

## Status (v0.1.0)

- Schema packaged under `migrations/001_core.sql`
- CLI: migrate · ingest-golden · ingest-file · reviews · review-decide
- Golden fixture only — **no bulk library ingest**
- First **production migrate** blocked until Lamassu DB prep + Wil confirm

## Quick start (dev / CI)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql://USER@HOST:5432/mnemosyne_test
mnemosyne migrate
mnemosyne ingest-golden
mnemosyne reviews
# Human gate (example):
# mnemosyne review-decide --claim-id UUID --outcome approved --reviewer wil
```

Unit tests run without Postgres. Integration tests require `DATABASE_URL` or `TEST_DATABASE_URL`.

```bash
ruff check src tests
pytest -q
```

## Environment

See `.env.example`. **Never commit passwords** or connection URLs with secrets.

| Key | Purpose |
|-----|---------|
| `DATABASE_URL` | `postgresql://…/mnemosyne` (or test DB) |
| `TEST_DATABASE_URL` | Optional CI/local alias read by tests |

Expected role **names** (passwords out of band): `mnemosyne_migrator`, `mnemosyne_ingest`, `mnemosyne_app`, `mnemosyne_ro`.

## Pipeline

1. Register `sources`
2. `ingest_batches` + checksummed `passages`
3. Propose `claims` at `pending_review` (never auto-approved)
4. Open `review_queue` row
5. Human `review-decide` → `approved` \| `rejected` \| `superseded`
6. Trusted recall queries **`status = 'approved'` only**

## Layout

```
migrations/          plain SQL, NNN_name.sql
src/mnemosyne/       package + CLI
tests/               unit + Postgres integration
docs/                design notes
```

## Docs

- `docs/DESIGN.md` — product boundaries and gates
- `docs/APPLY.md` — production migrate checklist (Wil gate)
- Reliquary: `domains/memory/`, shipyard ADR `0006-mnemosyne-kb-foundation.md`

## License

MIT

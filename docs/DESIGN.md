# Mnemosyne design (v0.1)

## Problem

Wil has years of books, audiobooks, and podcasts. Knowledge must live in a
**structured Postgres SoR** with provenance and **human review**, not chat memory dumps.

## Locked names

| Layer | Name |
|-------|------|
| Repo / product | `mnemosyne` |
| DB | `mnemosyne` |
| Planned profile | `mnemosyne` |
| Dominion folder | `domains/memory` |

## Ownership

| Job | Owner |
|-----|--------|
| Schema, migrations, ingest/review tools, CI | Hephaestus |
| CT network, empty DB, roles, secrets path | Lamassu |
| Corpus stewardship / persona | Mnemosyne profile (later) |
| Profile create / Discord routes | Hermes/`default` (later) |

## Core model

```
sources ──< passages ──< claim_passages >── claims
                │                              │
           ingest_batches                 review_queue
```

- **Passages** are checksummed text with locators (`ch.12`, `p.3`, timestamps later).
- **Claims** are distilled statements. Default insert path uses `pending_review`.
- **review_queue** is the explicit human work list. Closing a row requires an outcome.
- Trusted recall: `WHERE status = 'approved'`.

## Non-goals (v0.1)

- Hermes profile / Discord persona
- Bulk library import
- Vector search requirement (`passage_embeddings` stub only)
- Silent auto-approve
- Secrets in git or Reliquary

## Migration strategy

Plain SQL files `migrations/NNN_name.sql` + `schema_migrations` ledger.
Runner: `mnemosyne migrate`. No ORM required for v1.

## Ingest strategy

`mnemosyne.plain_text` parser: UTF-8 file → passage chunks → heuristic claim proposals.
Heuristic claims are **draft labor for humans**, not truth.

## Apply gate (production CT)

1. Lamassu A complete (reachable host, empty DB, roles)
2. This repo migrations green in CI
3. Wil explicit OK for first migrate
4. Apply with migrator role; record SHA + timestamp in ops notes (no passwords)

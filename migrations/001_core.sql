-- Mnemosyne schema v001 — core tables + review gates
-- Target DB name: mnemosyne
-- Applied only via `mnemosyne migrate` (never ad-hoc chat psql for production).
-- Do not store passwords here. First apply to CT 103 requires Lamassu A + Wil OK.
-- Transaction is owned by `mnemosyne migrate` (no nested BEGIN/COMMIT here).
-- Requires PostgreSQL 13+ (gen_random_uuid is core; no pgcrypto extension).
-- vector extension intentionally omitted in v1


-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

CREATE TYPE media_kind AS ENUM (
  'book',
  'audiobook',
  'podcast_episode',
  'podcast_series',
  'article',
  'lecture',
  'other'
);

CREATE TYPE review_status AS ENUM (
  'draft',
  'pending_review',
  'approved',
  'rejected',
  'superseded'
);

CREATE TYPE ingest_status AS ENUM (
  'received',
  'parsing',
  'parsed',
  'failed',
  'cancelled'
);

-- ---------------------------------------------------------------------------
-- Sources (one logical work / episode)
-- ---------------------------------------------------------------------------

CREATE TABLE sources (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  media_kind      media_kind NOT NULL,
  title           text NOT NULL CHECK (length(btrim(title)) > 0),
  authors         text[] NOT NULL DEFAULT '{}',
  series_title    text,
  isbn_or_sku     text,
  external_url    text,
  language        text NOT NULL DEFAULT 'en',
  finished_on     date,
  notes           text,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sources_media_kind_idx ON sources (media_kind);
CREATE INDEX sources_title_idx ON sources (title);
CREATE UNIQUE INDEX sources_isbn_unique ON sources (isbn_or_sku)
  WHERE isbn_or_sku IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Ingest batches (file drop / parser run)
-- ---------------------------------------------------------------------------

CREATE TABLE ingest_batches (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid REFERENCES sources (id) ON DELETE SET NULL,
  status          ingest_status NOT NULL DEFAULT 'received',
  parser_name     text NOT NULL CHECK (length(btrim(parser_name)) > 0),
  parser_version  text NOT NULL CHECK (length(btrim(parser_version)) > 0),
  input_uri       text,              -- path or object key; no credentials
  input_sha256    text,
  error_summary   text,
  stats           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);

CREATE INDEX ingest_batches_status_idx ON ingest_batches (status);
CREATE INDEX ingest_batches_source_idx ON ingest_batches (source_id);

-- ---------------------------------------------------------------------------
-- Passages (stable locator + text)
-- ---------------------------------------------------------------------------

CREATE TABLE passages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
  batch_id        uuid REFERENCES ingest_batches (id) ON DELETE SET NULL,
  locator         text NOT NULL CHECK (length(btrim(locator)) > 0),
  sequence_no     integer NOT NULL DEFAULT 0,
  body            text NOT NULL CHECK (length(body) > 0),
  body_sha256     text NOT NULL CHECK (length(body_sha256) = 64),
  token_estimate  integer CHECK (token_estimate IS NULL OR token_estimate >= 0),
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, locator, body_sha256)
);

CREATE INDEX passages_source_seq_idx ON passages (source_id, sequence_no);
CREATE INDEX passages_batch_idx ON passages (batch_id);

-- ---------------------------------------------------------------------------
-- Topics & entities
-- ---------------------------------------------------------------------------

CREATE TABLE topics (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            text NOT NULL UNIQUE CHECK (length(btrim(slug)) > 0),
  title           text NOT NULL CHECK (length(btrim(title)) > 0),
  description     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entities (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            text NOT NULL UNIQUE CHECK (length(btrim(slug)) > 0),
  name            text NOT NULL CHECK (length(btrim(name)) > 0),
  kind            text NOT NULL DEFAULT 'concept'
                    CHECK (kind IN ('person', 'org', 'concept', 'work', 'place', 'other')),
  description     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE passage_topics (
  passage_id      uuid NOT NULL REFERENCES passages (id) ON DELETE CASCADE,
  topic_id        uuid NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
  PRIMARY KEY (passage_id, topic_id)
);

CREATE TABLE passage_entities (
  passage_id      uuid NOT NULL REFERENCES passages (id) ON DELETE CASCADE,
  entity_id       uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  PRIMARY KEY (passage_id, entity_id)
);

-- ---------------------------------------------------------------------------
-- Claims (distilled knowledge) + human review gate
-- ---------------------------------------------------------------------------

CREATE TABLE claims (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id          uuid NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
  primary_passage_id uuid REFERENCES passages (id) ON DELETE SET NULL,
  statement          text NOT NULL CHECK (length(btrim(statement)) > 0),
  confidence         real CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  status             review_status NOT NULL DEFAULT 'draft',
  reviewer           text,
  reviewed_at        timestamptz,
  review_notes       text,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT claims_reviewed_fields_ck CHECK (
    (status IN ('draft', 'pending_review') AND reviewed_at IS NULL)
    OR (status IN ('approved', 'rejected', 'superseded'))
  )
);

CREATE INDEX claims_status_idx ON claims (status);
CREATE INDEX claims_source_idx ON claims (source_id);
CREATE INDEX claims_pending_idx ON claims (created_at)
  WHERE status = 'pending_review';

CREATE TABLE claim_passages (
  claim_id        uuid NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  passage_id      uuid NOT NULL REFERENCES passages (id) ON DELETE CASCADE,
  PRIMARY KEY (claim_id, passage_id)
);

CREATE TABLE claim_topics (
  claim_id        uuid NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  topic_id        uuid NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
  PRIMARY KEY (claim_id, topic_id)
);

CREATE TABLE claim_entities (
  claim_id        uuid NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  entity_id       uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
  PRIMARY KEY (claim_id, entity_id)
);

-- ---------------------------------------------------------------------------
-- Review queue (explicit work items for human gate)
-- ---------------------------------------------------------------------------

CREATE TABLE review_queue (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id        uuid NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  priority        integer NOT NULL DEFAULT 100,
  opened_at       timestamptz NOT NULL DEFAULT now(),
  closed_at       timestamptz,
  outcome         review_status
                    CHECK (
                      outcome IS NULL
                      OR outcome IN ('approved', 'rejected', 'superseded')
                    ),
  UNIQUE (claim_id),
  CONSTRAINT review_queue_closed_outcome_ck CHECK (
    (closed_at IS NULL AND outcome IS NULL)
    OR (closed_at IS NOT NULL AND outcome IS NOT NULL)
  )
);

CREATE INDEX review_queue_open_idx ON review_queue (priority, opened_at)
  WHERE closed_at IS NULL;

-- ---------------------------------------------------------------------------
-- Optional embeddings stub (v2) — table present, unused until extension exists
-- ---------------------------------------------------------------------------

CREATE TABLE passage_embeddings (
  passage_id      uuid PRIMARY KEY REFERENCES passages (id) ON DELETE CASCADE,
  model_name      text NOT NULL,
  dims            integer NOT NULL CHECK (dims > 0),
  embedding_bytea bytea,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Grants (role names only — passwords out of band)
-- Expected roles from Lamassu A:
--   mnemosyne_migrator, mnemosyne_ingest, mnemosyne_app, mnemosyne_ro
-- Grants are applied in a separate ops step after roles exist; not hard-required
-- for empty-DB migrate with a superuser/dev URL.
-- ---------------------------------------------------------------------------

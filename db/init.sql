-- Schema for the legal knowledge base: firm-approved Q&A pairs, searchable by embedding.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_base (
    id         BIGSERIAL PRIMARY KEY,
    category   TEXT NOT NULL,
    question   TEXT NOT NULL UNIQUE,
    answer     TEXT NOT NULL,
    -- Statutes, rules, or cases the answer relies on, e.g. 'Fla. Stat. § 83.49'.
    sources    TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Each entry is searchable by its question plus any alternate phrasings (aliases),
-- so "I got an eviction notice" can match "How does eviction for unpaid rent work?".
CREATE TABLE IF NOT EXISTS knowledge_base_phrasings (
    id        BIGSERIAL PRIMARY KEY,
    entry_id  BIGINT NOT NULL REFERENCES knowledge_base (id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    -- 384 dimensions matches BAAI/bge-small-en-v1.5 (see src/app/embeddings.py).
    embedding VECTOR(384) NOT NULL,
    UNIQUE (entry_id, text)
);

CREATE INDEX IF NOT EXISTS knowledge_base_phrasings_embedding_idx
    ON knowledge_base_phrasings USING hnsw (embedding vector_cosine_ops);

-- One row per /query request, including refusals and errors, for review and monitoring.
CREATE TABLE IF NOT EXISTS query_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query           TEXT NOT NULL,
    response        TEXT,
    status          TEXT NOT NULL CHECK (status IN ('answered', 'refused', 'error')),
    -- Knowledge base entries sent to Claude: [{"question": ..., "distance": ...}].
    -- Questions are stored as text so the log survives knowledge base edits.
    references_used JSONB NOT NULL DEFAULT '[]',
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    retrieval_ms    INTEGER,
    llm_ms          INTEGER,
    latency_ms      INTEGER NOT NULL,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS query_log_created_at_idx ON query_log (created_at);

-- API keys for programmatic access. Only a SHA-256 hash of each key is stored; the key
-- itself is shown once when created (see src/scripts/api_keys.py).
CREATE TABLE IF NOT EXISTS api_keys (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('client', 'attorney')),
    key_hash     TEXT NOT NULL UNIQUE,
    -- Overrides the role's default /query limit (requests per hour) when set.
    hourly_limit INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ
);

-- Fixed one-hour windows of request counts per caller and endpoint. Stored in Postgres so
-- the limit holds across multiple app instances.
CREATE TABLE IF NOT EXISTS rate_limits (
    identity     TEXT NOT NULL,
    bucket       TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    count        INTEGER NOT NULL,
    PRIMARY KEY (identity, bucket, window_start)
);

-- Thumbs up/down on a logged answer. `source` records who rated it: anyone using the public
-- chat page, a client API key, or an attorney key. Only attorney ratings are promoted into
-- knowledge base candidates.
CREATE TABLE IF NOT EXISTS feedback (
    id         BIGSERIAL PRIMARY KEY,
    query_id   UUID NOT NULL REFERENCES query_log (id) ON DELETE CASCADE,
    rating     TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment    TEXT,
    source     TEXT NOT NULL DEFAULT 'public' CHECK (source IN ('public', 'client', 'attorney')),
    api_key_id BIGINT REFERENCES api_keys (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Columns added after the first release, for databases created before them.
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS
    source TEXT NOT NULL DEFAULT 'public' CHECK (source IN ('public', 'client', 'attorney'));
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS api_key_id BIGINT REFERENCES api_keys (id);

CREATE INDEX IF NOT EXISTS feedback_query_id_idx ON feedback (query_id);

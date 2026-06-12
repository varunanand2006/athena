-- Athena Phase 3 schema migration

CREATE TABLE IF NOT EXISTS applications (
    id          SERIAL PRIMARY KEY,
    company     TEXT        NOT NULL,
    role        TEXT        NOT NULL,
    stage       TEXT        NOT NULL DEFAULT 'applied',
    applied_date DATE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
    title       TEXT        NOT NULL,
    deadline    TIMESTAMPTZ,
    source      TEXT,
    status      TEXT        NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id          SERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    company     TEXT,
    email       TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leetcode_problems (
    id          SERIAL PRIMARY KEY,
    title       TEXT        NOT NULL,
    slug        TEXT        NOT NULL UNIQUE,
    difficulty  TEXT        NOT NULL,
    solved_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS leetcode_submissions (
    id          BIGINT      PRIMARY KEY,
    problem_slug TEXT       NOT NULL,
    difficulty  TEXT        NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS leetcode_queue (
    problem_slug TEXT        PRIMARY KEY,
    submitted_at TIMESTAMPTZ NOT NULL,
    queued_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS leetcode_analysis (
    id          SERIAL PRIMARY KEY,
    problem_slug TEXT       NOT NULL,
    analysis_text TEXT      NOT NULL,
    analyzed_at TIMESTAMPTZ NOT NULL
);

-- Phase 8: Multi-chat
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title      TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);

-- Phase 5: Internship Hunter
CREATE TABLE IF NOT EXISTS internship_postings (
    id                    SERIAL PRIMARY KEY,
    company               TEXT        NOT NULL,
    role                  TEXT        NOT NULL,
    location              TEXT        NOT NULL,
    apply_link            TEXT,
    priority_score        INTEGER,
    resume_recommendation TEXT,
    company_summary       TEXT,
    status                TEXT        NOT NULL DEFAULT 'new',
    found_date            DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company, role, location)
);

-- Phase 9: Document Storage & Catalog
-- Source-of-truth catalog for uploaded documents. The Qdrant `documents`
-- collection holds vector chunks; this Postgres table holds one row per
-- ingested file, with `id` stamped into each chunk's Qdrant payload as
-- `document_id` so chunks can be deleted by document on re-ingest.
CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    filename    TEXT        NOT NULL UNIQUE,
    title       TEXT        NOT NULL,
    doc_type    TEXT        NOT NULL,
    file_path   TEXT        NOT NULL,
    summary     TEXT,
    chunk_count INTEGER     NOT NULL DEFAULT 0,
    size_bytes  INTEGER     NOT NULL DEFAULT 0,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_added_at ON documents (added_at DESC);


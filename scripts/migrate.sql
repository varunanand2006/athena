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

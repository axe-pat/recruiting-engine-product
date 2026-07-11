from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    display_label TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    target_roles_json TEXT NOT NULL DEFAULT '[]',
    skills_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(user_id, sha256, kind)
);

CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    website TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'discovered',
    strategic INTEGER NOT NULL DEFAULT 0 CHECK (strategic IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_user ON companies(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source_label TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'intake',
    fit_score REAL CHECK (fit_score IS NULL OR (fit_score >= 0 AND fit_score <= 10)),
    role_family TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    relationship TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'discovered',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'planned',
    next_action TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS outreach (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    contact_id TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    company_id TEXT REFERENCES companies(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft',
    draft_text TEXT NOT NULL DEFAULT '',
    reviewed_text TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',
    approved_by TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    approved_at TEXT,
    sent_at TEXT,
    delivery_reference TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_user ON outreach(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS outreach_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    outreach_id TEXT NOT NULL REFERENCES outreach(id) ON DELETE CASCADE,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_events ON outreach_events(outreach_id, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    input_counts_json TEXT NOT NULL DEFAULT '{}',
    output_counts_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS intakes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    selected_text TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intakes_user ON intakes(user_id, created_at DESC);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            factory=ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

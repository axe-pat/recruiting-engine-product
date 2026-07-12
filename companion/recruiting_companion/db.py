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

CREATE TABLE IF NOT EXISTS operator_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    requested_scope TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    confirmation_valid INTEGER NOT NULL DEFAULT 0 CHECK (confirmation_valid IN (0, 1)),
    argv_sha256 TEXT NOT NULL DEFAULT '',
    lock_snapshot_json TEXT NOT NULL DEFAULT '{}',
    returncode INTEGER,
    stdout_sha256 TEXT NOT NULL DEFAULT '',
    stderr_sha256 TEXT NOT NULL DEFAULT '',
    stdout_lines INTEGER NOT NULL DEFAULT 0,
    stderr_lines INTEGER NOT NULL DEFAULT 0,
    preflight_returncode INTEGER,
    preflight_stdout_sha256 TEXT NOT NULL DEFAULT '',
    preflight_stderr_sha256 TEXT NOT NULL DEFAULT '',
    result_code TEXT NOT NULL DEFAULT '',
    result_run_id TEXT NOT NULL DEFAULT '',
    result_health TEXT NOT NULL DEFAULT '',
    result_report_sha256 TEXT NOT NULL DEFAULT '',
    result_delivery_mode TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_jobs_user
    ON operator_jobs(user_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS operator_reviews (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_label TEXT NOT NULL,
    target_snapshot_json TEXT NOT NULL DEFAULT '{}',
    source_artifact_sha256 TEXT NOT NULL DEFAULT '',
    artifact_sha256 TEXT NOT NULL,
    reviewed_subject TEXT NOT NULL DEFAULT '',
    reviewed_text TEXT NOT NULL DEFAULT '',
    reviewed_subject_sha256 TEXT NOT NULL DEFAULT '',
    reviewed_text_sha256 TEXT NOT NULL DEFAULT '',
    execution_artifact_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reviewed_at TEXT,
    approved_at TEXT,
    revoked_at TEXT,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_reviews_user
    ON operator_reviews(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_reviews_target
    ON operator_reviews(user_id, command_id, target_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_reviews_one_active
    ON operator_reviews(user_id, command_id, target_id)
    WHERE state IN ('pending', 'reviewed', 'approved');

CREATE TABLE IF NOT EXISTS operator_review_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    review_id TEXT NOT NULL REFERENCES operator_reviews(id) ON DELETE CASCADE,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor_scope TEXT NOT NULL,
    confirmation_valid INTEGER NOT NULL DEFAULT 0
        CHECK (confirmation_valid IN (0, 1)),
    target_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_review_events_review
    ON operator_review_events(review_id, created_at);
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
            operator_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(operator_jobs)"
                ).fetchall()
            }
            if "parameters_json" not in operator_columns:
                connection.execute(
                    "ALTER TABLE operator_jobs ADD COLUMN "
                    "parameters_json TEXT NOT NULL DEFAULT '{}'"
                )
            for column, definition in (
                ("preflight_returncode", "INTEGER"),
                ("preflight_stdout_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("preflight_stderr_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("result_run_id", "TEXT NOT NULL DEFAULT ''"),
                ("result_health", "TEXT NOT NULL DEFAULT ''"),
                ("result_report_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("result_delivery_mode", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in operator_columns:
                    connection.execute(
                        f"ALTER TABLE operator_jobs ADD COLUMN {column} {definition}"
                    )
            review_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(operator_reviews)"
                ).fetchall()
            }
            for column, definition in (
                ("source_artifact_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("reviewed_subject", "TEXT NOT NULL DEFAULT ''"),
                ("reviewed_text", "TEXT NOT NULL DEFAULT ''"),
                ("reviewed_subject_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("reviewed_text_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("execution_artifact_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in review_columns:
                    connection.execute(
                        f"ALTER TABLE operator_reviews ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                UPDATE operator_reviews
                SET source_artifact_sha256 = artifact_sha256
                WHERE source_artifact_sha256 = ''
                """
            )
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

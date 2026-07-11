from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from .config import Settings
from .db import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ServiceError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    status = 404
    code = "not_found"


class ConflictError(ServiceError):
    status = 409
    code = "conflict"


class ValidationError(ServiceError):
    status = 422
    code = "validation_error"


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _text(
    value: Any,
    field: str,
    *,
    required: bool = False,
    maximum: int = 100_000,
) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{field} is required")
    if len(value) > maximum:
        raise ValidationError(f"{field} exceeds the {maximum}-character limit")
    return value


def _string_list(value: Any, field: str, maximum: int = 100) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be an array of strings")
    if len(value) > maximum:
        raise ValidationError(f"{field} cannot contain more than {maximum} items")
    result: list[str] = []
    for item in value:
        cleaned = _text(item, field, maximum=500)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


RESOURCE_FIELDS: dict[str, dict[str, Any]] = {
    "companies": {
        "prefix": "cmp",
        "create": {"name", "website", "stage", "strategic", "notes"},
        "update": {"name", "website", "stage", "strategic", "notes"},
        "required": {"name"},
    },
    "jobs": {
        "prefix": "job",
        "create": {
            "company_id",
            "title",
            "location",
            "description",
            "source_label",
            "source_url",
            "status",
            "fit_score",
            "role_family",
            "discovered_at",
        },
        "update": {
            "company_id",
            "title",
            "location",
            "description",
            "source_label",
            "source_url",
            "status",
            "fit_score",
            "role_family",
            "discovered_at",
        },
        "required": {"title"},
    },
    "contacts": {
        "prefix": "ctc",
        "create": {
            "company_id",
            "name",
            "email",
            "profile_url",
            "relationship",
            "status",
            "notes",
        },
        "update": {
            "company_id",
            "name",
            "email",
            "profile_url",
            "relationship",
            "status",
            "notes",
        },
        "required": set(),
    },
    "applications": {
        "prefix": "app",
        "create": {"job_id", "status", "next_action", "notes", "submitted_at"},
        "update": {"status", "next_action", "notes", "submitted_at"},
        "required": {"job_id"},
    },
}

VALID_STATUSES = {
    "companies": {"discovered", "research", "approved", "watching", "archived"},
    "jobs": {"intake", "active", "review", "selected", "rejected", "archived"},
    "contacts": {"discovered", "review", "approved", "active", "do_not_contact"},
    "applications": {
        "planned",
        "materials_ready",
        "reviewed",
        "submitted",
        "interviewing",
        "closed",
        "withdrawn",
    },
}

OUTREACH_TRANSITIONS = {
    "draft": {"reviewed", "cancelled"},
    "reviewed": {"draft", "approved", "cancelled"},
    "approved": {"reviewed", "sent", "failed", "cancelled"},
    "failed": {"reviewed", "cancelled"},
    "sent": {"replied"},
    "replied": set(),
    "cancelled": set(),
}

MAX_PORTABLE_RUN_ITEMS = 200
DASHBOARD_PRESENTATION_LIMIT = 100
DASHBOARD_RECENT_REPORT_LIMIT = 10


class CompanionService:
    def __init__(self, settings: Settings):
        settings.validate()
        settings.prepare()
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.initialize()

    # Profiles and preferences -------------------------------------------------
    def get_profile(self) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE user_id = ?",
                (self.settings.user_id,),
            ).fetchone()
        result = _row(row)
        if result:
            result["target_roles"] = _loads(result.pop("target_roles_json"), [])
            result["skills"] = _loads(result.pop("skills_json"), [])
        return result

    def put_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("profile must be an object")
        now = utc_now()
        current = self.get_profile() or {}
        values = {
            "display_label": _text(
                payload.get("display_label", current.get("display_label", "")),
                "display_label",
                maximum=200,
            ),
            "headline": _text(
                payload.get("headline", current.get("headline", "")),
                "headline",
                maximum=500,
            ),
            "location": _text(
                payload.get("location", current.get("location", "")),
                "location",
                maximum=300,
            ),
            "summary": _text(
                payload.get("summary", current.get("summary", "")),
                "summary",
                maximum=20_000,
            ),
            "target_roles": _string_list(
                payload.get("target_roles", current.get("target_roles", [])),
                "target_roles",
            ),
            "skills": _string_list(
                payload.get("skills", current.get("skills", [])),
                "skills",
            ),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO profiles (
                    user_id, display_label, headline, location, summary,
                    target_roles_json, skills_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_label = excluded.display_label,
                    headline = excluded.headline,
                    location = excluded.location,
                    summary = excluded.summary,
                    target_roles_json = excluded.target_roles_json,
                    skills_json = excluded.skills_json,
                    updated_at = excluded.updated_at
                """,
                (
                    self.settings.user_id,
                    values["display_label"],
                    values["headline"],
                    values["location"],
                    values["summary"],
                    _json(values["target_roles"]),
                    _json(values["skills"]),
                    current.get("created_at", now),
                    now,
                ),
            )
        return self.get_profile() or {}

    def get_preferences(self) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM preferences WHERE user_id = ?",
                (self.settings.user_id,),
            ).fetchone()
        return _loads(row["data_json"], {}) if row else {}

    def put_preferences(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("preferences must be an object")
        encoded = _json(payload)
        if len(encoded.encode("utf-8")) > 200_000:
            raise ValidationError("preferences exceed the 200 KB limit")
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO preferences (user_id, data_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (self.settings.user_id, encoded, now, now),
            )
        return self.get_preferences()

    # Documents and onboarding -------------------------------------------------
    def add_document(
        self,
        *,
        filename: str,
        content: bytes,
        kind: str = "other",
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        filename = Path(_text(filename, "filename", required=True, maximum=255)).name
        filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip(" .")
        if not filename:
            filename = "document.bin"
        kind = _text(kind, "kind", required=True, maximum=80)
        media_type = _text(media_type, "media_type", required=True, maximum=200)
        if not isinstance(content, bytes):
            raise ValidationError("document content must be bytes")
        if not content:
            raise ValidationError("document content is empty")
        if len(content) > self.settings.max_upload_bytes:
            raise ValidationError(
                f"document exceeds the {self.settings.max_upload_bytes}-byte limit"
            )
        digest = hashlib.sha256(content).hexdigest()
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM documents
                WHERE user_id = ? AND sha256 = ? AND kind = ?
                """,
                (self.settings.user_id, digest, kind),
            ).fetchone()
        if existing:
            return self._public_document(dict(existing), duplicate=True)

        document_id = new_id("doc")
        storage_name = f"{document_id}__{filename}"
        path = self.settings.documents_dir / storage_name
        path.write_bytes(content)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        now = utc_now()
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (
                        id, user_id, kind, filename, media_type, storage_path,
                        sha256, size_bytes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        self.settings.user_id,
                        kind,
                        filename,
                        media_type,
                        storage_name,
                        digest,
                        len(content),
                        now,
                    ),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.get_document(document_id)

    def add_document_base64(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = _text(
            payload.get("content_base64"),
            "content_base64",
            required=True,
            maximum=max(self.settings.max_upload_bytes * 2, 1024),
        )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValidationError("content_base64 is not valid base64") from error
        return self.add_document(
            filename=payload.get("filename", "document.bin"),
            content=content,
            kind=payload.get("kind", "other"),
            media_type=payload.get("media_type", "application/octet-stream"),
        )

    def list_documents(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._page(limit, offset)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM documents WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [self._public_document(dict(row)) for row in rows]

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ? AND user_id = ?",
                (document_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError("document not found")
        return self._public_document(dict(row))

    @staticmethod
    def _public_document(row: dict[str, Any], *, duplicate: bool = False) -> dict[str, Any]:
        row.pop("storage_path", None)
        row["duplicate"] = duplicate
        return row

    def onboard(
        self,
        payload: dict[str, Any],
        uploads: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("onboarding payload must be an object")
        result: dict[str, Any] = {
            "profile_updated": False,
            "preferences_updated": False,
            "documents": [],
            "companies": [],
            "jobs": [],
            "contacts": [],
        }
        if isinstance(payload.get("profile"), dict):
            self.put_profile(payload["profile"])
            result["profile_updated"] = True
        if isinstance(payload.get("preferences"), dict):
            self.put_preferences(payload["preferences"])
            result["preferences_updated"] = True

        for company in self._payload_array(payload, "companies"):
            result["companies"].append(self.create_resource("companies", company))
        for job in self._payload_array(payload, "jobs"):
            job = dict(job)
            company_name = job.pop("company_name", "")
            if company_name and not job.get("company_id"):
                job["company_id"] = self._find_or_create_company(company_name)["id"]
            result["jobs"].append(self.create_resource("jobs", job))
        for contact in self._payload_array(payload, "contacts"):
            contact = dict(contact)
            company_name = contact.pop("company_name", "")
            if company_name and not contact.get("company_id"):
                contact["company_id"] = self._find_or_create_company(company_name)["id"]
            result["contacts"].append(self.create_resource("contacts", contact))

        for document in self._payload_array(payload, "documents"):
            result["documents"].append(self.add_document_base64(document))
        for upload in uploads:
            result["documents"].append(
                self.add_document(
                    filename=upload["filename"],
                    content=upload["content"],
                    kind=upload.get("kind", "other"),
                    media_type=upload.get("media_type", "application/octet-stream"),
                )
            )
        result["dashboard"] = self.dashboard_snapshot()
        return result

    @staticmethod
    def _payload_array(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = payload.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValidationError(f"{key} must be an array of objects")
        if len(value) > 500:
            raise ValidationError(f"{key} cannot contain more than 500 records")
        return value

    # Generic local resources --------------------------------------------------
    def list_resource(
        self,
        resource: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._resource_spec(resource)
        limit, offset = self._page(limit, offset)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {resource} WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [self._normalize_resource(resource, dict(row)) for row in rows]

    def get_resource(self, resource: str, resource_id: str) -> dict[str, Any]:
        self._resource_spec(resource)
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {resource} WHERE id = ? AND user_id = ?",
                (resource_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError(f"{resource.rstrip('s')} not found")
        return self._normalize_resource(resource, dict(row))

    def create_resource(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        spec = self._resource_spec(resource)
        if not isinstance(payload, dict):
            raise ValidationError(f"{resource.rstrip('s')} must be an object")
        unknown = set(payload) - spec["create"]
        if unknown:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(unknown))}")
        for required in spec["required"]:
            if payload.get(required) in (None, ""):
                raise ValidationError(f"{required} is required")
        values = self._clean_resource_values(resource, payload, creating=True)
        resource_id = new_id(spec["prefix"])
        now = utc_now()
        values.setdefault("created_at", now)
        values.setdefault("updated_at", now)
        columns = ["id", "user_id", *values.keys()]
        parameters = [resource_id, self.settings.user_id, *values.values()]
        placeholders = ", ".join("?" for _ in columns)
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    f"INSERT INTO {resource} ({', '.join(columns)}) VALUES ({placeholders})",
                    parameters,
                )
        except sqlite3.IntegrityError as error:
            raise ConflictError(self._integrity_message(error)) from error
        return self.get_resource(resource, resource_id)

    def update_resource(
        self,
        resource: str,
        resource_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        spec = self._resource_spec(resource)
        self.get_resource(resource, resource_id)
        if not isinstance(payload, dict):
            raise ValidationError("update body must be an object")
        unknown = set(payload) - spec["update"]
        if unknown:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(unknown))}")
        if not payload:
            return self.get_resource(resource, resource_id)
        values = self._clean_resource_values(resource, payload, creating=False)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    f"UPDATE {resource} SET {assignments} WHERE id = ? AND user_id = ?",
                    (*values.values(), resource_id, self.settings.user_id),
                )
        except sqlite3.IntegrityError as error:
            raise ConflictError(self._integrity_message(error)) from error
        return self.get_resource(resource, resource_id)

    def _clean_resource_values(
        self,
        resource: str,
        payload: dict[str, Any],
        *,
        creating: bool,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        string_limits = {
            "name": 500,
            "email": 500,
            "profile_url": 2_000,
            "website": 2_000,
            "source_url": 4_000,
            "title": 1_000,
            "location": 500,
            "description": 200_000,
            "notes": 100_000,
            "next_action": 5_000,
            "relationship": 1_000,
            "role_family": 200,
            "source_label": 200,
            "stage": 80,
            "status": 80,
            "submitted_at": 100,
            "discovered_at": 100,
            "company_id": 100,
            "job_id": 100,
        }
        for field, value in payload.items():
            if field == "fit_score":
                if value in (None, ""):
                    values[field] = None
                else:
                    try:
                        score = float(value)
                    except (TypeError, ValueError) as error:
                        raise ValidationError("fit_score must be a number") from error
                    if not 0 <= score <= 10:
                        raise ValidationError("fit_score must be between 0 and 10")
                    values[field] = score
            elif field == "strategic":
                if not isinstance(value, bool):
                    raise ValidationError("strategic must be a boolean")
                values[field] = int(value)
            else:
                values[field] = _text(
                    value,
                    field,
                    required=field in self._resource_spec(resource)["required"],
                    maximum=string_limits.get(field, 10_000),
                )

        if "status" in values and values["status"] not in VALID_STATUSES[resource]:
            raise ValidationError(
                f"status must be one of: {', '.join(sorted(VALID_STATUSES[resource]))}"
            )
        if "stage" in values and values["stage"] not in VALID_STATUSES["companies"]:
            raise ValidationError(
                f"stage must be one of: {', '.join(sorted(VALID_STATUSES['companies']))}"
            )
        if values.get("company_id"):
            self._require_foreign("companies", values["company_id"])
        if values.get("job_id"):
            self._require_foreign("jobs", values["job_id"])
        if resource == "contacts" and creating:
            identity_fields = (
                values.get("name"),
                values.get("email"),
                values.get("profile_url"),
            )
            if not any(identity_fields):
                raise ValidationError("a contact needs a name, email, or profile_url")
        if resource == "jobs" and creating:
            values.setdefault("status", "intake")
            values.setdefault("source_label", "manual")
            values.setdefault("discovered_at", utc_now())
        if resource == "companies" and creating:
            values.setdefault("stage", "discovered")
            values.setdefault("strategic", 0)
        if resource == "contacts" and creating:
            values.setdefault("status", "discovered")
        if resource == "applications" and creating:
            values.setdefault("status", "planned")
        return values

    def _require_foreign(self, resource: str, resource_id: str) -> None:
        try:
            self.get_resource(resource, resource_id)
        except NotFoundError as error:
            raise ValidationError(f"{resource.rstrip('s')}_id is not valid for this user") from error

    @staticmethod
    def _normalize_resource(resource: str, row: dict[str, Any]) -> dict[str, Any]:
        if resource == "companies":
            row["strategic"] = bool(row["strategic"])
        return row

    @staticmethod
    def _resource_spec(resource: str) -> dict[str, Any]:
        if resource not in RESOURCE_FIELDS:
            raise NotFoundError("resource not found")
        return RESOURCE_FIELDS[resource]

    @staticmethod
    def _integrity_message(error: sqlite3.IntegrityError) -> str:
        message = str(error)
        if "applications.user_id, applications.job_id" in message:
            return "an application already exists for this job"
        return "the record conflicts with existing local data"

    @staticmethod
    def _page(limit: int, offset: int) -> tuple[int, int]:
        try:
            limit = int(limit)
            offset = int(offset)
        except (TypeError, ValueError) as error:
            raise ValidationError("limit and offset must be integers") from error
        return min(max(limit, 1), 500), max(offset, 0)

    def _find_or_create_company(self, name: Any) -> dict[str, Any]:
        name = _text(name, "company_name", required=True, maximum=500)
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM companies WHERE user_id = ? AND lower(name) = lower(?) LIMIT 1",
                (self.settings.user_id, name),
            ).fetchone()
        if row:
            return self._normalize_resource("companies", dict(row))
        return self.create_resource("companies", {"name": name})

    # Outreach state machine ---------------------------------------------------
    def list_outreach(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._page(limit, offset)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM outreach WHERE user_id = ?
                ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """,
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_outreach(self, outreach_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM outreach WHERE id = ? AND user_id = ?",
                (outreach_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError("outreach item not found")
        result = dict(row)
        result["events"] = self.list_outreach_events(outreach_id)
        return result

    def list_outreach_events(self, outreach_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, outreach_id, from_state, to_state, actor, note, created_at
                FROM outreach_events
                WHERE user_id = ? AND outreach_id = ?
                ORDER BY created_at ASC
                """,
                (self.settings.user_id, outreach_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_outreach(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("outreach must be an object")
        allowed = {"contact_id", "company_id", "job_id", "channel", "draft_text", "state"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(unknown))}")
        if payload.get("state", "draft") != "draft":
            raise ValidationError("new outreach must begin in draft state")
        targets = {
            key: _text(payload.get(key), key, maximum=100)
            for key in ("contact_id", "company_id", "job_id")
        }
        if not any(targets.values()):
            raise ValidationError("outreach needs a contact_id, company_id, or job_id")
        for field, resource in (
            ("contact_id", "contacts"),
            ("company_id", "companies"),
            ("job_id", "jobs"),
        ):
            if targets[field]:
                self._require_foreign(resource, targets[field])
        channel = _text(payload.get("channel"), "channel", required=True, maximum=80)
        draft_text = _text(payload.get("draft_text"), "draft_text", maximum=20_000)
        now = utc_now()
        outreach_id = new_id("out")
        event_id = new_id("evt")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO outreach (
                    id, user_id, contact_id, company_id, job_id, channel, state,
                    draft_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (
                    outreach_id,
                    self.settings.user_id,
                    targets["contact_id"] or None,
                    targets["company_id"] or None,
                    targets["job_id"] or None,
                    channel,
                    draft_text,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO outreach_events (
                    id, user_id, outreach_id, from_state, to_state, actor, note, created_at
                ) VALUES (?, ?, ?, '', 'draft', 'local-user', 'Draft created', ?)
                """,
                (event_id, self.settings.user_id, outreach_id, now),
            )
        return self.get_outreach(outreach_id)

    def transition_outreach(
        self,
        outreach_id: str,
        *,
        to_state: str,
        actor: str,
        note: str = "",
        reviewed_text: str | None = None,
        delivery_reference: str = "",
        confirmed: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = self.get_outreach(outreach_id)
        from_state = current["state"]
        to_state = _text(to_state, "state", required=True, maximum=40).lower()
        actor = _text(actor, "actor", required=True, maximum=200)
        note = _text(note, "note", maximum=5_000)
        if to_state not in OUTREACH_TRANSITIONS.get(from_state, set()):
            raise ConflictError(f"outreach cannot transition from {from_state} to {to_state}")

        now = utc_now()
        updates: dict[str, Any] = {"state": to_state, "updated_at": now}
        if to_state == "reviewed":
            final_text = _text(
                reviewed_text if reviewed_text is not None else current["draft_text"],
                "reviewed_text",
                required=True,
                maximum=20_000,
            )
            updates.update(
                {
                    "reviewed_text": final_text,
                    "reviewed_by": actor,
                    "reviewed_at": now,
                    "approved_by": "",
                    "approved_at": None,
                }
            )
        elif to_state == "draft":
            updates.update(
                {
                    "reviewed_text": "",
                    "reviewed_by": "",
                    "reviewed_at": None,
                    "approved_by": "",
                    "approved_at": None,
                }
            )
        elif to_state == "approved":
            if not current.get("reviewed_at") or not current.get("reviewed_text"):
                raise ConflictError("outreach must contain an explicit review before approval")
            contact_id = current.get("contact_id")
            if not contact_id:
                raise ConflictError(
                    "outreach needs a reviewed contact before approval"
                )
            contact = self.get_resource("contacts", contact_id)
            if contact.get("status") not in {"approved", "active"}:
                raise ConflictError(
                    "the outreach contact must be approved or active"
                )
            if not any(
                contact.get(field) for field in ("name", "email", "profile_url")
            ):
                raise ConflictError("the outreach contact has no confirmed identity")
            updates.update({"approved_by": actor, "approved_at": now})
        elif to_state == "sent":
            delivery_reference = _text(
                delivery_reference,
                "delivery_reference",
                required=True,
                maximum=1_000,
            )
            if not confirmed:
                raise ValidationError(
                    "confirmed=true is required; this endpoint records an external send and never sends"
                )
            if not current.get("approved_at"):
                raise ConflictError("outreach must be approved before recording a send")
            updates.update(
                {"sent_at": now, "delivery_reference": delivery_reference}
            )
        elif to_state == "replied":
            if not current.get("sent_at"):
                raise ConflictError("a reply cannot be recorded before a confirmed send")
            updates["outcome"] = note or "reply_recorded"

        event_id = new_id("evt")
        assignments = ", ".join(f"{field} = ?" for field in updates)
        with self.db.transaction() as connection:
            updated = connection.execute(
                f"UPDATE outreach SET {assignments} "
                "WHERE id = ? AND user_id = ? AND state = ?",
                (
                    *updates.values(),
                    outreach_id,
                    self.settings.user_id,
                    from_state,
                ),
            )
            if updated.rowcount != 1:
                raise ConflictError("outreach state changed concurrently; reload and review")
            connection.execute(
                """
                INSERT INTO outreach_events (
                    id, user_id, outreach_id, from_state, to_state, actor, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    self.settings.user_id,
                    outreach_id,
                    from_state,
                    to_state,
                    actor,
                    note,
                    now,
                ),
            )
        event = self.list_outreach_events(outreach_id)[-1]
        return self.get_outreach(outreach_id), event

    # Browser intake -----------------------------------------------------------
    def create_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("intake must be an object")
        allowed = {"source_url", "title", "selected_text", "notes", "kind"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(unknown))}")
        kind = _text(payload.get("kind", "note"), "kind", required=True, maximum=40).lower()
        if kind not in {"job", "company", "contact", "note"}:
            raise ValidationError("kind must be job, company, contact, or note")
        source_url = _text(payload.get("source_url"), "source_url", maximum=4_000)
        if source_url:
            parsed = urlsplit(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValidationError("source_url must be an http or https URL")
        title = _text(
            payload.get("title"),
            "title",
            required=kind == "job",
            maximum=1_000,
        )
        selected_text = _text(
            payload.get("selected_text"),
            "selected_text",
            maximum=100_000,
        )
        notes = _text(payload.get("notes"), "notes", maximum=10_000)
        job: dict[str, Any] | None = None
        if kind == "job":
            job = self.create_resource(
                "jobs",
                {
                    "title": title,
                    "description": selected_text,
                    "source_label": "browser_intake",
                    "source_url": source_url,
                    "status": "intake",
                },
            )
        intake_id = new_id("int")
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO intakes (
                    id, user_id, kind, source_url, title, selected_text, notes,
                    job_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake_id,
                    self.settings.user_id,
                    kind,
                    source_url,
                    title,
                    selected_text,
                    notes,
                    job["id"] if job else None,
                    now,
                ),
            )
        return {
            "intake": {
                "id": intake_id,
                "kind": kind,
                "source_url": source_url,
                "title": title,
                "selected_text": selected_text,
                "notes": notes,
                "job_id": job["id"] if job else None,
                "created_at": now,
            },
            "job": job,
        }

    def import_jobs(
        self,
        rows: list[dict[str, Any]],
        *,
        source_label: str,
    ) -> dict[str, Any]:
        source_label = _text(
            source_label,
            "source_label",
            required=True,
            maximum=200,
        )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValidationError("rows must be an array of objects")
        if len(rows) > 5_000:
            raise ValidationError("a job import cannot exceed 5,000 rows")
        result: dict[str, Any] = {
            "source_label": source_label,
            "received": len(rows),
            "imported": 0,
            "skipped": 0,
            "errors": [],
            "job_ids": [],
        }
        aliases = {
            "company": ("company", "company_name", "employer"),
            "title": ("title", "role", "job_title"),
            "location": ("location", "job_location"),
            "source_url": ("url", "source_url", "job_url"),
            "status": ("status",),
            "fit_score": ("fit_score", "score"),
            "role_family": ("role_family", "role_type"),
        }
        for index, raw in enumerate(rows, start=1):
            try:
                normalized: dict[str, Any] = {}
                lowered = {str(key).strip().lower(): value for key, value in raw.items()}
                for target, names in aliases.items():
                    for name in names:
                        if name in lowered and lowered[name] not in (None, ""):
                            normalized[target] = lowered[name]
                            break
                title = _text(
                    normalized.get("title"),
                    "title",
                    required=True,
                    maximum=1_000,
                )
                company_name = _text(
                    normalized.get("company"),
                    "company",
                    maximum=500,
                )
                location = _text(
                    normalized.get("location"),
                    "location",
                    maximum=500,
                )
                source_url = _text(
                    normalized.get("source_url"),
                    "source_url",
                    maximum=4_000,
                )
                if source_url:
                    parsed = urlsplit(source_url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        raise ValidationError("url must be an http or https URL")
                if self._job_import_exists(
                    source_url=source_url,
                    company_name=company_name,
                    title=title,
                    location=location,
                ):
                    result["skipped"] += 1
                    continue
                payload: dict[str, Any] = {
                    "title": title,
                    "location": location,
                    "source_url": source_url,
                    "source_label": source_label,
                    "status": normalized.get("status") or "intake",
                    "role_family": normalized.get("role_family") or "",
                }
                if normalized.get("fit_score") not in (None, ""):
                    payload["fit_score"] = normalized["fit_score"]
                if company_name:
                    payload["company_id"] = self._find_or_create_company(company_name)["id"]
                job = self.create_resource("jobs", payload)
                result["imported"] += 1
                result["job_ids"].append(job["id"])
            except ServiceError as error:
                result["errors"].append(
                    {"row": index, "code": error.code, "message": error.message}
                )
        return result

    def _job_import_exists(
        self,
        *,
        source_url: str,
        company_name: str,
        title: str,
        location: str,
    ) -> bool:
        with self.db.connect() as connection:
            if source_url:
                row = connection.execute(
                    """
                    SELECT 1 FROM jobs
                    WHERE user_id = ? AND lower(source_url) = lower(?) LIMIT 1
                    """,
                    (self.settings.user_id, source_url),
                ).fetchone()
                if row:
                    return True
            row = connection.execute(
                """
                SELECT 1 FROM jobs j
                LEFT JOIN companies c ON c.id = j.company_id AND c.user_id = j.user_id
                WHERE j.user_id = ?
                  AND lower(trim(j.title)) = lower(trim(?))
                  AND lower(trim(COALESCE(c.name, ''))) = lower(trim(?))
                  AND lower(trim(j.location)) = lower(trim(?))
                LIMIT 1
                """,
                (self.settings.user_id, title, company_name, location),
            ).fetchone()
        return bool(row)

    # Runs, reports, and dashboard --------------------------------------------
    def run_portable(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        unknown = set(config) - {"min_fit_score", "limit"}
        if unknown:
            raise ValidationError(f"unsupported run config: {', '.join(sorted(unknown))}")
        try:
            minimum = float(config.get("min_fit_score", 7.0))
        except (TypeError, ValueError) as error:
            raise ValidationError("min_fit_score must be a number") from error
        if not 0 <= minimum <= 10:
            raise ValidationError("min_fit_score must be between 0 and 10")
        try:
            limit = int(config.get("limit", 50))
        except (TypeError, ValueError) as error:
            raise ValidationError("limit must be an integer") from error
        limit = min(max(limit, 1), MAX_PORTABLE_RUN_ITEMS)
        normalized_config = {"min_fit_score": minimum, "limit": limit}
        run_id = new_id("run")
        started = utc_now()
        input_counts = self._table_counts()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, user_id, run_type, status, started_at,
                    input_counts_json, config_json
                ) VALUES (?, ?, 'portable', 'running', ?, ?, ?)
                """,
                (
                    run_id,
                    self.settings.user_id,
                    started,
                    _json(input_counts),
                    _json(normalized_config),
                ),
            )
        try:
            queue = self._derive_portable_queue(minimum=minimum, limit=limit)
            action_counts = dict(Counter(item["action"] for item in queue))
            gate_counts = dict(Counter(item["gate"] for item in queue))
            output_counts = {
                "queue_items": len(queue),
                "actions": action_counts,
                "gates": gate_counts,
            }
            completed = utc_now()
            report_id = new_id("rpt")
            report_summary = {
                "schema_version": "1.0",
                "run_id": run_id,
                "run_type": "portable",
                "source_scope": "current_user_local_database_only",
                "started_at": started,
                "completed_at": completed,
                "input_counts": input_counts,
                "output_counts": output_counts,
                "config": normalized_config,
                "queue": queue,
                "truth_contract": [
                    "No external source was queried.",
                    "No application or outreach action was executed.",
                    "Queue reasons are deterministic and name the local evidence used.",
                    "Missing fit evidence is routed to review rather than inferred.",
                ],
            }
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO reports (
                        id, user_id, run_id, kind, summary_json, created_at
                    ) VALUES (?, ?, ?, 'portable_queue', ?, ?)
                    """,
                    (
                        report_id,
                        self.settings.user_id,
                        run_id,
                        _json(report_summary),
                        completed,
                    ),
                )
                connection.execute(
                    """
                    UPDATE runs SET status = 'completed', completed_at = ?,
                        output_counts_json = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        completed,
                        _json(output_counts),
                        run_id,
                        self.settings.user_id,
                    ),
                )
            return self.get_run(run_id)
        except Exception as error:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    UPDATE runs SET status = 'failed', completed_at = ?, error = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (utc_now(), str(error)[:2_000], run_id, self.settings.user_id),
                )
            raise

    def _derive_portable_queue(self, *, minimum: float, limit: int) -> list[dict[str, Any]]:
        queue: list[dict[str, Any]] = []
        with self.db.connect() as connection:
            jobs = connection.execute(
                """
                SELECT j.*, c.name AS company_name, a.id AS application_id,
                    a.status AS application_status
                FROM jobs j
                LEFT JOIN companies c ON c.id = j.company_id AND c.user_id = j.user_id
                LEFT JOIN applications a ON a.job_id = j.id AND a.user_id = j.user_id
                WHERE j.user_id = ? AND j.status NOT IN ('rejected', 'archived')
                ORDER BY COALESCE(j.fit_score, -1) DESC, j.updated_at DESC
                """,
                (self.settings.user_id,),
            ).fetchall()
            contacts = connection.execute(
                """
                SELECT c.*, co.name AS company_name
                FROM contacts c
                LEFT JOIN companies co ON co.id = c.company_id AND co.user_id = c.user_id
                WHERE c.user_id = ? AND c.status IN ('approved', 'active')
                  AND NOT EXISTS (
                    SELECT 1 FROM outreach o
                    WHERE o.user_id = c.user_id AND o.contact_id = c.id
                      AND o.state IN ('approved', 'sent', 'replied')
                  )
                ORDER BY c.updated_at DESC
                """,
                (self.settings.user_id,),
            ).fetchall()
            outreach_rows = connection.execute(
                """
                SELECT * FROM outreach
                WHERE user_id = ? AND state IN ('draft', 'reviewed', 'approved', 'failed')
                ORDER BY updated_at DESC
                """,
                (self.settings.user_id,),
            ).fetchall()

        for row in jobs:
            job = dict(row)
            if job.get("application_status") in {
                "submitted",
                "interviewing",
                "closed",
                "withdrawn",
            }:
                continue
            score = job.get("fit_score")
            if score is None:
                queue.append(
                    {
                        "id": f"queue_job_{job['id']}",
                        "entity_type": "job",
                        "entity_id": job["id"],
                        "label": job["title"],
                        "company": job.get("company_name") or "",
                        "action": "fit_review",
                        "gate": "human_review_required",
                        "priority": 50,
                        "reason": "The local job has no fit score; the engine did not infer one.",
                        "evidence": ["local_job_record", "fit_score_missing"],
                    }
                )
            elif float(score) >= minimum:
                queue.append(
                    {
                        "id": f"queue_job_{job['id']}",
                        "entity_type": "job",
                        "entity_id": job["id"],
                        "label": job["title"],
                        "company": job.get("company_name") or "",
                        "action": "application_review",
                        "gate": "human_review_required",
                        "priority": round(float(score) * 10, 2),
                        "reason": f"The imported fit score ({float(score):.1f}) meets the configured {minimum:.1f} threshold.",
                        "evidence": ["local_job_record", "user_imported_fit_score"],
                    }
                )

        for row in contacts:
            contact = dict(row)
            queue.append(
                {
                    "id": f"queue_contact_{contact['id']}",
                    "entity_type": "contact",
                    "entity_id": contact["id"],
                    "label": contact.get("name") or "Relationship contact",
                    "company": contact.get("company_name") or "",
                    "action": "relationship_review",
                    "gate": "human_review_required",
                    "priority": 65,
                    "reason": "The local contact is approved/active and has no approved or recorded send.",
                    "evidence": ["local_contact_status", "local_outreach_state"],
                }
            )

        state_priority = {"approved": 90, "reviewed": 80, "failed": 70, "draft": 60}
        for row in outreach_rows:
            outreach = dict(row)
            queue.append(
                {
                    "id": f"queue_outreach_{outreach['id']}",
                    "entity_type": "outreach",
                    "entity_id": outreach["id"],
                    "label": f"{outreach['channel']} outreach",
                    "company": "",
                    "action": f"outreach_{outreach['state']}",
                    "gate": (
                        "ready_for_external_execution"
                        if outreach["state"] == "approved"
                        else "human_review_required"
                    ),
                    "priority": state_priority[outreach["state"]],
                    "reason": f"The local outreach record is explicitly {outreach['state']}.",
                    "evidence": ["local_outreach_state"],
                }
            )

        queue.sort(key=lambda item: (-item["priority"], item["id"]))
        return queue[:limit]

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = self._page(limit, offset)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs WHERE user_id = ?
                ORDER BY started_at DESC LIMIT ? OFFSET ?
                """,
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [self._normalize_run(dict(row)) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND user_id = ?",
                (run_id, self.settings.user_id),
            ).fetchone()
            report_rows = connection.execute(
                """
                SELECT id, run_id, kind, created_at FROM reports
                WHERE run_id = ? AND user_id = ? ORDER BY created_at DESC
                """,
                (run_id, self.settings.user_id),
            ).fetchall()
        if not row:
            raise NotFoundError("run not found")
        return {
            "run": self._normalize_run(dict(row)),
            "reports": [dict(report) for report in report_rows],
        }

    def get_report(self, report_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reports WHERE id = ? AND user_id = ?",
                (report_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError("report not found")
        result = dict(row)
        result["summary"] = _loads(result.pop("summary_json"), {})
        return result

    @staticmethod
    def _normalize_run(row: dict[str, Any]) -> dict[str, Any]:
        row["input_counts"] = _loads(row.pop("input_counts_json"), {})
        row["output_counts"] = _loads(row.pop("output_counts_json"), {})
        row["config"] = _loads(row.pop("config_json"), {})
        return row

    def dashboard_snapshot(self) -> dict[str, Any]:
        counts = self._table_counts()
        with self.db.connect() as connection:
            application_rows = connection.execute(
                """
                SELECT status, count(*) AS count FROM applications
                WHERE user_id = ? GROUP BY status
                """,
                (self.settings.user_id,),
            ).fetchall()
            outreach_rows = connection.execute(
                """
                SELECT state, count(*) AS count FROM outreach
                WHERE user_id = ? GROUP BY state
                """,
                (self.settings.user_id,),
            ).fetchall()
            latest_report = connection.execute(
                """
                SELECT reports.*, runs.status AS run_status
                FROM reports
                JOIN runs ON runs.id = reports.run_id
                    AND runs.user_id = reports.user_id
                WHERE reports.user_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (self.settings.user_id,),
            ).fetchone()
            recent_report_rows = connection.execute(
                """
                SELECT reports.*, runs.status AS run_status
                FROM reports
                JOIN runs ON runs.id = reports.run_id
                    AND runs.user_id = reports.user_id
                WHERE reports.user_id = ?
                ORDER BY reports.created_at DESC LIMIT ?
                """,
                (self.settings.user_id, DASHBOARD_RECENT_REPORT_LIMIT),
            ).fetchall()
            application_items = connection.execute(
                """
                SELECT a.id, COALESCE(c.name, '') AS company,
                    j.title AS role, a.status, a.updated_at
                FROM applications a
                JOIN jobs j ON j.id = a.job_id AND j.user_id = a.user_id
                LEFT JOIN companies c ON c.id = j.company_id
                    AND c.user_id = a.user_id
                WHERE a.user_id = ?
                ORDER BY a.updated_at DESC LIMIT ?
                """,
                (self.settings.user_id, DASHBOARD_PRESENTATION_LIMIT),
            ).fetchall()
            outreach_items = connection.execute(
                """
                SELECT o.id,
                    COALESCE(oc.name, cc.name, jc.name, '') AS company,
                    CASE
                        WHEN trim(COALESCE(ct.name, '')) != '' THEN ct.name
                        WHEN trim(COALESCE(ct.email, '')) != '' THEN ct.email
                        ELSE ''
                    END AS recipient,
                    o.channel, o.state,
                    CASE
                        WHEN trim(o.reviewed_text) != '' THEN o.reviewed_text
                        ELSE o.draft_text
                    END AS text,
                    o.updated_at
                FROM outreach o
                LEFT JOIN contacts ct ON ct.id = o.contact_id
                    AND ct.user_id = o.user_id
                LEFT JOIN companies oc ON oc.id = o.company_id
                    AND oc.user_id = o.user_id
                LEFT JOIN companies cc ON cc.id = ct.company_id
                    AND cc.user_id = o.user_id
                LEFT JOIN jobs j ON j.id = o.job_id AND j.user_id = o.user_id
                LEFT JOIN companies jc ON jc.id = j.company_id
                    AND jc.user_id = o.user_id
                WHERE o.user_id = ?
                ORDER BY o.updated_at DESC LIMIT ?
                """,
                (self.settings.user_id, DASHBOARD_PRESENTATION_LIMIT),
            ).fetchall()
        profile = self.get_profile()
        latest: dict[str, Any] | None = None
        action_queue: list[dict[str, Any]] = []
        if latest_report:
            latest = self._dashboard_report_dto(
                dict(latest_report), include_input_counts=True
            )
            summary = _loads(latest_report["summary_json"], {})
            queue = summary.get("queue", [])
            config = summary.get("config", {})
            try:
                report_limit = int(config.get("limit", MAX_PORTABLE_RUN_ITEMS))
            except (TypeError, ValueError):
                report_limit = MAX_PORTABLE_RUN_ITEMS
            report_limit = min(max(report_limit, 1), MAX_PORTABLE_RUN_ITEMS)
            if isinstance(queue, list):
                action_queue = queue[:report_limit]
        return {
            "generated_at": utc_now(),
            "profile_ready": bool(
                profile and (profile.get("headline") or profile.get("target_roles"))
            ),
            "counts": counts,
            "applications_by_status": {
                row["status"]: row["count"] for row in application_rows
            },
            "outreach_by_state": {row["state"]: row["count"] for row in outreach_rows},
            "recent_runs": self.list_runs(limit=5),
            "latest_report": latest,
            "recent_reports": [
                self._dashboard_report_dto(dict(row)) for row in recent_report_rows
            ],
            "action_queue": action_queue,
            "application_items": [dict(row) for row in application_items],
            "outreach_items": [dict(row) for row in outreach_items],
            "presentation_meta": {
                "applications": {
                    "total": counts["applications"],
                    "returned": len(application_items),
                    "truncated": counts["applications"] > len(application_items),
                },
                "outreach": {
                    "total": counts["outreach"],
                    "returned": len(outreach_items),
                    "truncated": counts["outreach"] > len(outreach_items),
                },
            },
        }

    @staticmethod
    def _dashboard_report_dto(
        row: dict[str, Any],
        *,
        include_input_counts: bool = False,
    ) -> dict[str, Any]:
        summary = _loads(row.get("summary_json"), {})
        input_counts = summary.get("input_counts", {})
        output_counts = summary.get("output_counts", {})
        if not isinstance(input_counts, dict):
            input_counts = {}
        if not isinstance(output_counts, dict):
            output_counts = {}
        queue_items = output_counts.get("queue_items", 0)
        local_records = sum(
            value
            for key, value in input_counts.items()
            if key != "runs" and isinstance(value, int) and not isinstance(value, bool)
        )
        result = {
            "id": row["id"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "created_at": row["created_at"],
            "status": row.get("run_status", "unknown"),
            "summary_text": (
                f"Portable run produced {queue_items} reviewable actions from "
                f"{local_records} local records."
            ),
            "output_counts": output_counts,
        }
        if include_input_counts:
            result["input_counts"] = input_counts
        return result

    def _table_counts(self) -> dict[str, int]:
        tables = (
            "documents",
            "companies",
            "jobs",
            "contacts",
            "applications",
            "outreach",
            "runs",
        )
        result: dict[str, int] = {}
        with self.db.connect() as connection:
            for table in tables:
                result[table] = connection.execute(
                    f"SELECT count(*) FROM {table} WHERE user_id = ?",
                    (self.settings.user_id,),
                ).fetchone()[0]
        return result

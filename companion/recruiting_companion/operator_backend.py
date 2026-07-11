from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .db import Database
from .existing_adapter import ExistingEngineAdapter
from .service import NotFoundError, ValidationError, new_id, utc_now


_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_MAX_XLSX_BYTES = 64 * 1024 * 1024
_MAX_XLSX_EXPANDED_BYTES = 256 * 1024 * 1024
_OPERATOR_JOB_LIMIT = 100
_QUEUE_ITEM_LIMIT = 100
_ACCOUNT_ACTION_LIMIT = 50
_STORY_ITEM_LIMIT = 50
_PREFLIGHT_CONFIRMATION = "RUN_PRODUCTION_PREFLIGHT"
_REQUIRED_LOCKS = {
    "scheduler",
    "pipeline",
    "workbook",
    "queue",
    "adapter_mutation",
}

_JOB_STATUSES = {
    "new",
    "queued",
    "generated",
    "applied",
    "skipped",
    "skip",
    "parked",
    "closed",
    "review",
    "failed",
}
_JOB_SOURCES = {
    "linkedin",
    "indeed",
    "linkedin_live",
    "linkedin_live_jobs_v1",
    "screenshot",
    "manual",
    "builtin_startup_jobs",
    "handshake_jobs_v1",
    "seeded",
    "a16z_startup_jobs",
    "jobspy_filtered_v1",
    "yc_startup_jobs",
}
_ROLE_TYPES = {"PM", "Ops", "Strategy", "TPM", "Other"}
_REVIEW_DECISIONS = {"Proceed", "Reject", "Deprioritize", "Error"}
_REVIEW_CATEGORIES = {"Low Priority", "N/A"}
_ACCOUNT_TIERS = {"A", "B", "C", "L1", "L2", "L3"}
_ACCOUNT_STAGES = {
    "unqualified",
    "people_mapped",
    "priority_target",
    "outreach_active",
    "connected_no_conversation",
    "conversation_started",
}
_ACCOUNT_ACTIONS = {
    "Map contacts on LinkedIn",
    "Reconcile LinkedIn; await accepts",
    "Send LinkedIn invites",
    "Draft and send follow-up",
    "Continue conversation; push for coffee chat",
}
_QUEUE_STATUSES = {"queued", "generated", "review", "ready", "applied", "closed"}
_QUEUE_BUCKETS = {"new", "carry", "manual", "review", "ready"}
_COMMS_REVIEW_DECISIONS = {
    "accepted_for_continued_testing",
    "accepted_as_account_specific",
    "hold_for_more_examples",
    "rejected",
}
_WORKSPACE_COUNT_FIELDS = {
    "organizations",
    "opportunities",
    "contacts",
    "touchpoints",
    "sources",
}
_INVITE_TOTAL_FIELDS = {
    "already_connected",
    "dry_run_ready",
    "navigation_error",
    "send_error",
    "sent",
    "unavailable",
}

_COMMAND_CATALOG = {
    "production.preflight": {
        "kind": "read_only_check",
        "confirmation": _PREFLIGHT_CONFIRMATION,
        "policy": "conditionally_available",
    },
    "accounts.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_ACCOUNT_TRACKER",
        "policy": "conditionally_available",
    },
    "reports.daily.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_EXACT_DAILY_REPORT",
        "policy": "conditionally_available",
    },
    "reports.sources.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_EXACT_ROLE_SURFACE",
        "policy": "conditionally_available",
    },
    "reports.cadence.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_CADENCE_REPORT",
        "policy": "conditionally_available",
    },
    "reports.outcomes.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_OUTCOME_REPORT",
        "policy": "conditionally_available",
    },
    "communications.lab.refresh": {
        "kind": "local_write",
        "confirmation": "REFRESH_COMMUNICATION_LAB",
        "policy": "conditionally_available",
    },
    "outreach.plan.preview": {
        "kind": "review_artifact",
        "confirmation": "BUILD_TRACK_2_REVIEW_PLAN",
        "policy": "conditionally_available",
    },
    "application.resume.generate": {
        "kind": "model_generation",
        "confirmation": "GENERATE_ONE_RESUME_WITH_MODEL_COST",
        "policy": "conditionally_available",
    },
    "application.apply_packet.build": {
        "kind": "review_artifact",
        "confirmation": "BUILD_ONE_APPLY_PACKET",
        "policy": "conditionally_available",
    },
    "open.account_tracker": {
        "kind": "local_open",
        "confirmation": "OPEN_ACCOUNT_TRACKER",
        "policy": "conditionally_available",
    },
    "open.current_apply_queue": {
        "kind": "local_open",
        "confirmation": "OPEN_CURRENT_APPLY_QUEUE",
        "policy": "conditionally_available",
    },
    "open.latest_report": {
        "kind": "local_open",
        "confirmation": "OPEN_LATEST_EXACT_REPORT",
        "policy": "conditionally_available",
    },
    "open.story_workbench": {
        "kind": "local_open",
        "confirmation": "OPEN_STORY_WORKBENCH",
        "policy": "conditionally_available",
    },
    "open.communication_review": {
        "kind": "local_open",
        "confirmation": "OPEN_COMMUNICATION_REVIEW",
        "policy": "conditionally_available",
    },
    "open.application_folder": {
        "kind": "local_open",
        "confirmation": "OPEN_APPLICATION_FOLDER",
        "policy": "conditionally_available",
    },
    "nightly.run": {
        "kind": "pipeline_execution",
        "confirmation": "",
        "policy": "forbidden",
        "reason": "Full nightly execution is outside the operator allowlist.",
    },
    "outreach.send": {
        "kind": "external_delivery",
        "confirmation": "",
        "policy": "forbidden",
        "reason": "External outreach delivery is never available through this operator API.",
    },
    "application.submit": {
        "kind": "external_submission",
        "confirmation": "",
        "policy": "forbidden",
        "reason": "Automatic application submission is never available through this operator API.",
    },
}

_EMPTY_PARAMETERS_SCHEMA = {
    "type": "object",
    "additional_properties": False,
    "required": [],
    "properties": {},
}
_JOB_PARAMETERS_SCHEMA = {
    "type": "object",
    "additional_properties": False,
    "required": ["job_id"],
    "properties": {
        "job_id": {
            "type": "integer",
            "minimum": 1,
            "maximum": 999_999_999_999,
            "description": "Numeric job id present in the current apply queue.",
        }
    },
}
_PARAMETER_SCHEMAS = {
    command_id: (
        _JOB_PARAMETERS_SCHEMA
        if command_id
        in {
            "application.resume.generate",
            "application.apply_packet.build",
            "open.application_folder",
        }
        else _EMPTY_PARAMETERS_SCHEMA
    )
    for command_id in _COMMAND_CATALOG
}
_BACKGROUND_COMMANDS = {
    "accounts.refresh",
    "reports.daily.refresh",
    "reports.sources.refresh",
    "reports.cadence.refresh",
    "reports.outcomes.refresh",
    "communications.lab.refresh",
    "outreach.plan.preview",
    "application.resume.generate",
    "application.apply_packet.build",
}
_COMMAND_TIMEOUTS = {
    "accounts.refresh": 300,
    "reports.daily.refresh": 900,
    "reports.sources.refresh": 300,
    "reports.cadence.refresh": 300,
    "reports.outcomes.refresh": 300,
    "communications.lab.refresh": 600,
    "outreach.plan.preview": 300,
    "application.resume.generate": 3_000,
    "application.apply_packet.build": 120,
}
_SUCCESS_CODES = {
    "accounts.refresh": "account_tracker_refreshed",
    "reports.daily.refresh": "daily_report_refreshed",
    "reports.sources.refresh": "role_surface_refreshed",
    "reports.cadence.refresh": "cadence_report_refreshed",
    "reports.outcomes.refresh": "outcome_report_refreshed",
    "communications.lab.refresh": "communication_lab_refreshed",
    "outreach.plan.preview": "outreach_plan_built",
    "application.resume.generate": "resume_generation_completed",
    "application.apply_packet.build": "apply_packet_built",
}

_COMMAND_PRESENTATION = {
    "production.preflight": {
        "label": "Verify production release",
        "description": "Run the fixed check-only release attestation command.",
        "category": "production_guard",
        "risk": "read",
    },
    "accounts.refresh": {
        "label": "Refresh account tracker",
        "description": "Rebuild the installed account workbook from local Outreach CSVs.",
        "category": "accounts",
        "risk": "local_write",
    },
    "reports.daily.refresh": {
        "label": "Refresh daily report",
        "description": "Rebuild from the newest fully verified exact nightly summary.",
        "category": "reports",
        "risk": "local_write",
    },
    "reports.sources.refresh": {
        "label": "Refresh role-surface report",
        "description": "Build from the exact source metrics bound to the verified run.",
        "category": "reports",
        "risk": "local_write",
    },
    "reports.cadence.refresh": {
        "label": "Refresh cadence report",
        "description": "Rebuild the local tracker-backed cadence review artifact.",
        "category": "reports",
        "risk": "local_write",
    },
    "reports.outcomes.refresh": {
        "label": "Refresh outcome learning",
        "description": "Rebuild advisory outcome-learning and style-sync artifacts.",
        "category": "reports",
        "risk": "local_write",
    },
    "communications.lab.refresh": {
        "label": "Refresh communication lab",
        "description": "Rebuild the corpus-backed local communication brief.",
        "category": "communications",
        "risk": "local_write",
    },
    "outreach.plan.preview": {
        "label": "Build Track 2 review plan",
        "description": "Write one bounded plan artifact; never execute or send it.",
        "category": "communications",
        "risk": "local_write",
    },
    "application.resume.generate": {
        "label": "Generate one resume",
        "description": (
            "Run resume-only budget generation for one current-queue job. "
            "This can incur model cost."
        ),
        "category": "applications",
        "risk": "model_cost",
    },
    "application.apply_packet.build": {
        "label": "Build one apply packet",
        "description": "Write a no-submit apply-assist review packet; never call rtrvr.",
        "category": "applications",
        "risk": "local_write",
    },
    "open.account_tracker": {
        "label": "Open account tracker",
        "description": "Open the exact installed Outreach account workbook.",
        "category": "accounts",
        "risk": "local_open",
    },
    "open.current_apply_queue": {
        "label": "Open current apply queue",
        "description": "Open the exact installed ResumeGenerator queue folder.",
        "category": "applications",
        "risk": "local_open",
    },
    "open.latest_report": {
        "label": "Open latest exact report",
        "description": "Open the HTML report bound to the latest fully verified run.",
        "category": "reports",
        "risk": "local_open",
    },
    "open.story_workbench": {
        "label": "Open story workbench",
        "description": "Open the installed career story workbench folder.",
        "category": "stories",
        "risk": "local_open",
    },
    "open.communication_review": {
        "label": "Open communication review",
        "description": "Open the latest exact outcome-recommendation review artifact.",
        "category": "communications",
        "risk": "local_open",
    },
    "open.application_folder": {
        "label": "Open application folder",
        "description": "Open the exact current-queue folder for one numeric job id.",
        "category": "applications",
        "risk": "local_open",
    },
    "nightly.run": {
        "label": "Run full nightly",
        "description": "Full production pipeline execution is intentionally forbidden.",
        "category": "production",
        "risk": "external",
    },
    "outreach.send": {
        "label": "Send outreach",
        "description": "Message delivery is intentionally forbidden in the cockpit.",
        "category": "communications",
        "risk": "external",
    },
    "application.submit": {
        "label": "Submit application",
        "description": "Final application submission is intentionally forbidden.",
        "category": "applications",
        "risk": "external",
    },
}


class OperatorBackend:
    """Sanitized operator projections plus a fixed-command audit runner."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.validate()
        self.settings.prepare()
        self.db = Database(settings.database_path)
        self.db.initialize()
        self.adapter = ExistingEngineAdapter(settings)

    def capabilities(self) -> dict[str, Any]:
        adapter_status = self.adapter.status()
        commands = []
        for command_id, definition in _COMMAND_CATALOG.items():
            command = {
                "command_id": command_id,
                "kind": definition["kind"],
                "status": definition["policy"],
                "confirmation_required": bool(definition["confirmation"]),
                "confirmation_phrase": definition["confirmation"] or None,
                "parameters_schema": _PARAMETER_SCHEMAS[command_id],
                "asynchronous": command_id in _BACKGROUND_COMMANDS,
                "reason": definition.get("reason", ""),
                **_COMMAND_PRESENTATION[command_id],
            }
            if command_id == "production.preflight":
                available, reasons = self._preflight_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
            elif command_id == "open.application_folder":
                available, reasons = self._application_action_base_availability(
                    command_id, adapter_status
                )
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
            elif command_id.startswith("open."):
                available, reasons = self._open_availability(
                    command_id, adapter_status
                )
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
            elif command_id in _BACKGROUND_COMMANDS:
                available, reasons = self._background_availability(
                    command_id, adapter_status
                )
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
            commands.append(command)
        guarded_writes = any(
            command["status"] == "available"
            and command["kind"]
            in {"local_write", "review_artifact", "model_generation"}
            for command in commands
        )
        return {
            "schema_version": "1.0",
            "mode": "existing" if adapter_status["configured"] else "portable",
            "data_class": "local-private",
            "mutations_enabled": guarded_writes,
            "guarded_local_writes_enabled": guarded_writes,
            "arbitrary_commands_allowed": False,
            "external_sends_allowed": False,
            "automatic_applications_allowed": False,
            "full_nightly_allowed": False,
            "locks": adapter_status["locks"],
            "busy": adapter_status["busy"],
            "production_guard": adapter_status["production_guard"],
            "commands": commands,
        }

    def overview(self) -> dict[str, Any]:
        capability = self.capabilities()
        assets = self.assets()
        return {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "mode": capability["mode"],
            "data_class": "local-private",
            "guard": {
                "locks": capability["locks"],
                "busy": capability["busy"],
                "production_guard": capability["production_guard"],
                "external_actions": "disabled",
            },
            "capabilities": {
                "mutations_enabled": capability["mutations_enabled"],
                "guarded_local_writes_enabled": capability[
                    "guarded_local_writes_enabled"
                ],
                "arbitrary_commands_allowed": capability[
                    "arbitrary_commands_allowed"
                ],
                "external_sends_allowed": capability["external_sends_allowed"],
                "automatic_applications_allowed": capability[
                    "automatic_applications_allowed"
                ],
                "full_nightly_allowed": capability["full_nightly_allowed"],
                "commands": capability["commands"],
            },
            "assets": assets,
            "recent_jobs": self.list_jobs(limit=10),
        }

    def assets(self) -> dict[str, Any]:
        run_projections = self.adapter.verified_run_projections(limit=20)
        lock_states = self.adapter.lock_states()
        locks_free = self._all_locks_free(lock_states)

        workbooks: dict[str, Any]
        current_queue: dict[str, Any]
        if locks_free:
            current_workspace = self.adapter.snapshot()["current_workspace"]
            workbooks = self._workbook_assets()
            current_queue = self._current_apply_queue_assets(current_workspace)
            ending_locks = self.adapter.lock_states()
            if not self._all_locks_free(ending_locks):
                workbooks = {
                    "status": "busy",
                    "scope": "current-snapshot",
                    "reason": (
                        "Engine lock state changed during workbook capture; "
                        "aggregates were discarded."
                    ),
                    "locks": ending_locks,
                }
                current_queue = {
                    "status": "busy",
                    "scope": "current-snapshot",
                    "reason": (
                        "Engine lock state changed during queue capture; rows "
                        "were discarded."
                    ),
                    "locks": ending_locks,
                    "items": [],
                    "items_returned": 0,
                    "truncated": False,
                }
        else:
            blocked_status = (
                "busy" if any(state == "busy" for state in lock_states.values())
                else "unavailable"
            )
            workbooks = {
                "status": blocked_status,
                "scope": "current-snapshot",
                "reason": (
                    "Workbook aggregates require every engine and adapter lock "
                    "to be explicitly free."
                ),
                "locks": lock_states,
            }
            current_queue = {
                "status": blocked_status,
                "scope": "current-snapshot",
                "reason": (
                    "Queue projection requires every engine and adapter lock "
                    "to be explicitly free."
                ),
                "locks": lock_states,
                "items": [],
                "items_returned": 0,
                "truncated": False,
            }

        reports = self._report_assets(run_projections)
        sources = self._source_assets(run_projections)
        return {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "workbooks": workbooks,
            "current_apply_queue": current_queue,
            "story_comms": self._story_comms_assets(),
            "daily_reports": reports,
            "source_metrics": sources,
        }

    def list_jobs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), _OPERATOR_JOB_LIMIT)
        offset = max(int(offset), 0)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operator_jobs WHERE user_id = ?
                ORDER BY requested_at DESC LIMIT ? OFFSET ?
                """,
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [self._job_dto(dict(row)) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operator_jobs WHERE id = ? AND user_id = ?",
                (job_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError("operator job not found")
        return self._job_dto(dict(row))

    def submit_job(
        self,
        *,
        command_id: str,
        confirmation: str,
        requested_scope: str,
        parameters: Any = None,
    ) -> dict[str, Any]:
        if command_id not in _COMMAND_CATALOG:
            raise ValidationError("command_id is not in the operator allowlist")
        definition = _COMMAND_CATALOG[command_id]
        expected_confirmation = str(definition["confirmation"])
        if expected_confirmation and confirmation != expected_confirmation:
            raise ValidationError("confirmation phrase does not match the command")
        if requested_scope not in {"local", "web"}:
            raise ValidationError("requested_scope must be local or web")
        safe_parameters = self._validate_parameters(command_id, parameters)

        capability = next(
            item
            for item in self.capabilities()["commands"]
            if item["command_id"] == command_id
        )
        if capability["status"] == "available" and safe_parameters.get("job_id"):
            queue_row = self._current_queue_job(safe_parameters["job_id"])
            selected_available, selected_reasons = self._selected_action_availability(
                command_id, queue_row
            )
            if not selected_available:
                capability = {
                    **capability,
                    "status": "unavailable",
                    "reason": "; ".join(selected_reasons),
                }
        job_id = new_id("opjob")
        now = utc_now()
        initial_status = "queued" if capability["status"] == "available" else "blocked"
        result_code = "" if initial_status == "queued" else f"capability_{capability['status']}"
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operator_jobs (
                    id, user_id, command_id, parameters_json, status, requested_scope,
                    requested_at, confirmation_valid, result_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    self.settings.user_id,
                    command_id,
                    json.dumps(safe_parameters, sort_keys=True),
                    initial_status,
                    requested_scope,
                    now,
                    int(bool(expected_confirmation)),
                    result_code,
                    now,
                ),
            )
        if initial_status == "blocked":
            return self.get_job(job_id)
        if command_id == "production.preflight":
            self._execute_preflight(job_id)
        elif command_id.startswith("open."):
            self._execute_open(job_id, command_id, safe_parameters)
        elif command_id in _BACKGROUND_COMMANDS:
            worker = threading.Thread(
                target=self._execute_background,
                args=(job_id, command_id, safe_parameters),
                daemon=True,
                name=f"operator-{command_id}-{job_id[-8:]}",
            )
            try:
                worker.start()
            except RuntimeError:
                self._finish_job(
                    job_id,
                    status="failed",
                    result_code="worker_start_failed",
                )
        else:
            self._finish_job(job_id, status="blocked", result_code="not_executable")
        return self.get_job(job_id)

    @staticmethod
    def _validate_parameters(command_id: str, parameters: Any) -> dict[str, int]:
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise ValidationError("operator parameters must be an object")
        expected = set(_PARAMETER_SCHEMAS[command_id]["properties"])
        unknown = set(parameters) - expected
        if unknown:
            raise ValidationError(
                "unsupported operator parameters: " + ", ".join(sorted(unknown))
            )
        if expected == {"job_id"}:
            if set(parameters) != {"job_id"}:
                raise ValidationError("parameters must contain exactly job_id")
            job_id = parameters["job_id"]
            if (
                isinstance(job_id, bool)
                or not isinstance(job_id, int)
                or job_id < 1
                or job_id > 999_999_999_999
            ):
                raise ValidationError("job_id must be a positive integer")
            return {"job_id": job_id}
        if parameters:
            raise ValidationError("this command does not accept parameters")
        return {}

    def _execute_background(
        self,
        job_id: str,
        command_id: str,
        parameters: dict[str, int],
    ) -> None:
        try:
            self._execute_background_guarded(job_id, command_id, parameters)
        except Exception:
            self._finish_job(
                job_id,
                status="failed",
                result_code="worker_internal_error",
            )

    def _execute_background_guarded(
        self,
        job_id: str,
        command_id: str,
        parameters: dict[str, int],
    ) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id,
                    status="blocked",
                    result_code="adapter_lock_busy",
                )
                return
            try:
                locks = self.adapter.lock_states()
                external_locks = {
                    key: value
                    for key, value in locks.items()
                    if key != "adapter_mutation"
                }
                if not all(value == "free" for value in external_locks.values()):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="engine_locks_not_free",
                        lock_snapshot=locks,
                    )
                    return
                try:
                    argv, cwd, timeout, success_code = self._fixed_action_argv(
                        command_id, parameters
                    )
                except (OSError, ValueError, ValidationError):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="fixed_surface_changed",
                        lock_snapshot=locks,
                    )
                    return
                argv_hash = hashlib.sha256(
                    b"\0".join(part.encode("utf-8") for part in argv)
                ).hexdigest()
                self._update_job(
                    job_id,
                    {
                        "status": "running",
                        "started_at": utc_now(),
                        "argv_sha256": argv_hash,
                        "lock_snapshot_json": json.dumps(locks, sort_keys=True),
                    },
                )
                try:
                    completed = subprocess.run(
                        argv,
                        cwd=cwd,
                        env=self._fixed_environment(command_id),
                        capture_output=True,
                        text=False,
                        timeout=timeout,
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="fixed_command_timeout",
                        returncode=124,
                        stdout=error.stdout or b"",
                        stderr=error.stderr or b"",
                    )
                    return
                except OSError:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="fixed_command_spawn_failed",
                    )
                    return
                self._finish_job(
                    job_id,
                    status="completed" if completed.returncode == 0 else "failed",
                    result_code=(
                        success_code
                        if completed.returncode == 0
                        else "fixed_command_failed"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _execute_preflight(self, job_id: str) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id,
                    status="blocked",
                    result_code="adapter_lock_busy",
                )
                return
            try:
                locks = self.adapter.lock_states()
                external_locks = {
                    key: value for key, value in locks.items() if key != "adapter_mutation"
                }
                if not all(value == "free" for value in external_locks.values()):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="engine_locks_not_free",
                        lock_snapshot=locks,
                    )
                    return
                try:
                    argv, cwd = self._preflight_argv()
                except (OSError, ValidationError):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="preflight_surface_changed",
                    )
                    return
                argv_hash = hashlib.sha256(
                    b"\0".join(part.encode("utf-8") for part in argv)
                ).hexdigest()
                started = utc_now()
                self._update_job(
                    job_id,
                    {
                        "status": "running",
                        "started_at": started,
                        "argv_sha256": argv_hash,
                        "lock_snapshot_json": json.dumps(locks, sort_keys=True),
                    },
                )
                environment = {
                    key: value
                    for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
                    if (value := os.environ.get(key))
                }
                try:
                    completed = subprocess.run(
                        argv,
                        cwd=cwd,
                        env=environment,
                        capture_output=True,
                        text=False,
                        timeout=120,
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    stdout = error.stdout or b""
                    stderr = error.stderr or b""
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="timeout",
                        returncode=124,
                        stdout=stdout,
                        stderr=stderr,
                    )
                    return
                except OSError:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="preflight_spawn_failed",
                    )
                    return
                self._finish_job(
                    job_id,
                    status="completed" if completed.returncode == 0 else "failed",
                    result_code=(
                        "preflight_valid"
                        if completed.returncode == 0
                        else "preflight_failed"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _execute_open(
        self,
        job_id: str,
        command_id: str,
        parameters: dict[str, int],
    ) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id,
                    status="blocked",
                    result_code="adapter_lock_busy",
                )
                return
            try:
                locks = self.adapter.lock_states()
                if command_id in {
                    "open.account_tracker",
                    "open.current_apply_queue",
                    "open.application_folder",
                }:
                    external = {
                        key: value
                        for key, value in locks.items()
                        if key != "adapter_mutation"
                    }
                    if not all(value == "free" for value in external.values()):
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="engine_locks_not_free",
                            lock_snapshot=locks,
                        )
                        return
                try:
                    target = self._open_target(command_id, parameters)
                except (OSError, ValueError, ValidationError):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="open_target_changed",
                    )
                    return
                opener = Path("/usr/bin/open")
                if not opener.is_file() or not os.access(opener, os.X_OK):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="local_opener_unavailable",
                    )
                    return
                argv = [str(opener), str(target)]
                argv_hash = hashlib.sha256(
                    b"\0".join(part.encode("utf-8") for part in argv)
                ).hexdigest()
                self._update_job(
                    job_id,
                    {
                        "status": "running",
                        "started_at": utc_now(),
                        "argv_sha256": argv_hash,
                        "lock_snapshot_json": json.dumps(locks, sort_keys=True),
                    },
                )
                try:
                    completed = subprocess.run(
                        argv,
                        cwd=self.settings.user_dir,
                        env={
                            key: value
                            for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
                            if (value := os.environ.get(key))
                        },
                        capture_output=True,
                        text=False,
                        timeout=30,
                        check=False,
                        shell=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="local_open_failed",
                    )
                    return
                self._finish_job(
                    job_id,
                    status="completed" if completed.returncode == 0 else "failed",
                    result_code=(
                        "local_open_requested"
                        if completed.returncode == 0
                        else "local_open_failed"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _preflight_argv(self) -> tuple[list[str], Path]:
        if not self.settings.resume_python:
            raise ValidationError("resume Python is not configured")
        if not self.settings.resumegen_root:
            raise ValidationError("resume root is not configured")
        if not self.settings.attestation_path:
            raise ValidationError("attestation is not configured")
        python = _preserved_executable_path(
            self.settings.resume_python,
            "resume Python",
        )
        if self.settings.resumegen_root.is_symlink():
            raise ValidationError("resume root cannot be a symlink")
        root = self.settings.resumegen_root.resolve(strict=True)
        script_input = root / "discovery" / "scripts" / "nightly_prompt.py"
        if script_input.is_symlink():
            raise ValidationError("preflight script cannot be a symlink")
        script = script_input.resolve(strict=True)
        if not script.is_relative_to(root):
            raise ValidationError("preflight script escapes the configured root")
        attestation = self.settings.attestation_path.resolve(strict=True)
        if not attestation.is_file():
            raise ValidationError("production attestation is not a regular file")
        return (
            [
                str(python),
                "discovery/scripts/nightly_prompt.py",
                "--production-check-only",
                "--production-attestation",
                str(attestation),
            ],
            root,
        )

    def _preflight_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons = []
        if not adapter_status.get("roots_available"):
            reasons.append("existing engine roots are unavailable")
        try:
            self._preflight_argv()
        except (OSError, ValidationError):
            reasons.append("fixed production preflight surfaces are unavailable or unsafe")
        locks = adapter_status.get("locks", {})
        if not self._all_locks_free(locks):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
        return not reasons, reasons

    def _background_availability(
        self,
        command_id: str,
        adapter_status: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        if command_id in {
            "application.resume.generate",
            "application.apply_packet.build",
        }:
            return self._application_action_base_availability(
                command_id, adapter_status
            )
        reasons: list[str] = []
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            self._fixed_action_argv(command_id, {})
        except ValidationError as error:
            reasons.append(str(error))
        except (OSError, ValueError):
            reasons.append("fixed command surfaces are unavailable or unsafe")
        return not reasons, reasons

    def _application_action_base_availability(
        self,
        command_id: str,
        adapter_status: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            if command_id == "application.resume.generate":
                self._resume_surface("jobs.py")
                if not self._resume_model_key():
                    reasons.append("ResumeGenerator model credential is unavailable")
            elif command_id == "application.apply_packet.build":
                self._resume_surface("apply_assist/build_apply_task.py")
            elif command_id == "open.application_folder":
                opener = Path("/usr/bin/open")
                if not opener.is_file() or not os.access(opener, os.X_OK):
                    reasons.append("fixed local opener is unavailable")
            else:
                reasons.append("application command is not allowlisted")
            queue_root, rows = self._current_queue_rows()
            numeric_rows = [row for row in rows if _numeric_job_id(row) is not None]
            if not numeric_rows:
                reasons.append("current apply queue has no numeric job ids")
            elif command_id == "open.application_folder" and not any(
                self._queue_job_folder_available(row, queue_root=queue_root)
                for row in numeric_rows
            ):
                reasons.append("current queue has no safe application folder")
        except (OSError, ValueError, ValidationError, json.JSONDecodeError):
            reasons.append("current apply queue or fixed script is unavailable or unsafe")
        return not reasons, reasons

    def _selected_action_availability(
        self,
        command_id: str,
        queue_row: dict[str, Any],
        queue_root: Path | None = None,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if _numeric_job_id(queue_row) is None:
            reasons.append("selected queue item does not have a numeric job id")
        if command_id == "application.resume.generate":
            try:
                folder = self._queue_job_folder(queue_row, queue_root=queue_root)
            except (OSError, ValueError, ValidationError):
                folder = None
            if folder is None:
                reasons.append("selected queue item has no safe application folder")
            elif not (folder / "jd.txt").is_file() or (folder / "jd.txt").is_symlink():
                reasons.append("selected queue item has no safe job description")
            elif _folder_has_resume(folder):
                reasons.append("selected queue item already has resume material")
        elif command_id == "open.application_folder":
            if not self._queue_job_folder_available(
                queue_row, queue_root=queue_root
            ):
                reasons.append("selected queue item has no safe application folder")
        return not reasons, reasons

    def _fixed_action_argv(
        self,
        command_id: str,
        parameters: dict[str, int],
    ) -> tuple[list[str], Path, int, str]:
        timeout = _COMMAND_TIMEOUTS[command_id]
        success_code = _SUCCESS_CODES[command_id]
        if command_id in {
            "accounts.refresh",
            "reports.daily.refresh",
            "reports.sources.refresh",
            "reports.cadence.refresh",
            "reports.outcomes.refresh",
            "communications.lab.refresh",
            "outreach.plan.preview",
        }:
            python, root = self._outreach_surface()
            base = [str(python), "main.py"]
            if command_id == "accounts.refresh":
                argv = [
                    *base,
                    "account-tracker",
                    "--workspace",
                    "workspace",
                    "--output",
                    "workspace/account_tracker.xlsx",
                ]
            elif command_id == "reports.daily.refresh":
                latest, summary_path, _ = self._latest_verified_context()
                summary_payload = _read_json_object(summary_path)
                exact_since = summary_payload.get("created_at")
                if not isinstance(exact_since, str) or not exact_since:
                    raise ValidationError("verified summary created_at is unavailable")
                argv = [
                    *base,
                    "write-daily-run-report",
                    "--workspace",
                    "workspace",
                    "--since",
                    exact_since,
                    "--nightly-summary",
                    str(summary_path),
                    "--run-id",
                    str(latest["run_id"]),
                ]
            elif command_id == "reports.sources.refresh":
                latest, _, source_path = self._latest_verified_context()
                argv = [
                    *base,
                    "build-role-surface-report",
                    "--source-metrics",
                    str(source_path),
                    "--run-id",
                    str(latest["run_id"]),
                    "--workspace",
                    "workspace",
                ]
            elif command_id == "reports.cadence.refresh":
                argv = [
                    *base,
                    "build-outreach-cadence-report",
                    "--workspace",
                    "workspace",
                ]
            elif command_id == "reports.outcomes.refresh":
                argv = [
                    *base,
                    "build-outcome-learning-report",
                    "--workspace",
                    "workspace",
                ]
            elif command_id == "communications.lab.refresh":
                if not self.settings.resumegen_root:
                    raise ValidationError("resume root is not configured")
                resume_root = _strict_allowlisted_path(
                    self.settings.resumegen_root,
                    self.settings.resumegen_root,
                    expect="directory",
                )
                argv = [
                    *base,
                    "build-communication-lab",
                    "--workspace",
                    "workspace",
                    "--resume-root",
                    str(resume_root),
                ]
            else:
                argv = [
                    *base,
                    "build-track-2-daily-plan",
                    "--workspace",
                    "workspace",
                    "--max-total-actions",
                    "24",
                    "--max-companies",
                    "18",
                    "--max-linkedin-invites",
                    "12",
                    "--max-linkedin-followups",
                    "8",
                    "--max-company-mapping",
                    "5",
                    "--max-email-research",
                    "5",
                    "--max-context-enrichment",
                    "8",
                    "--max-email-drafts",
                    "0",
                ]
            return argv, root, timeout, success_code

        job_id = parameters.get("job_id")
        if not isinstance(job_id, int):
            raise ValidationError("numeric queue job id is required")
        queue_row = self._current_queue_job(job_id)
        selected_available, _ = self._selected_action_availability(
            command_id, queue_row
        )
        if not selected_available:
            raise ValidationError("selected queue action is no longer available")
        if command_id == "application.resume.generate":
            python, root = self._resume_surface("jobs.py")
            return (
                [
                    str(python),
                    "jobs.py",
                    "--no-color",
                    "generate",
                    "--id",
                    str(job_id),
                    "--resume-only",
                    "--budget-mode",
                    "--parallel",
                    "1",
                    "--timeout",
                    "2400",
                    "--model",
                    "claude-sonnet-4-6",
                ],
                root,
                timeout,
                success_code,
            )
        if command_id == "application.apply_packet.build":
            python, root = self._resume_surface(
                "apply_assist/build_apply_task.py"
            )
            return (
                [
                    str(python),
                    "apply_assist/build_apply_task.py",
                    "--job-id",
                    str(job_id),
                    "--queue-json",
                    "apps/Apply queues/current_apply_queue/priority_order.json",
                    "--out-dir",
                    "apply_assist/tasks",
                ],
                root,
                timeout,
                success_code,
            )
        raise ValidationError("background command is not allowlisted")

    def _outreach_surface(self) -> tuple[Path, Path]:
        if not self.settings.outreach_python or not self.settings.outreach_root:
            raise ValidationError("Outreach Python and root are required")
        python = _preserved_executable_path(
            self.settings.outreach_python,
            "Outreach Python",
        )
        root = _strict_allowlisted_path(
            self.settings.outreach_root,
            self.settings.outreach_root,
            expect="directory",
        )
        _strict_allowlisted_path(root, root / "main.py", expect="file")
        return python, root

    def _resume_surface(self, script_relative: str) -> tuple[Path, Path]:
        if not self.settings.resume_python or not self.settings.resumegen_root:
            raise ValidationError("ResumeGenerator Python and root are required")
        python = _preserved_executable_path(
            self.settings.resume_python,
            "ResumeGenerator Python",
        )
        root = _strict_allowlisted_path(
            self.settings.resumegen_root,
            self.settings.resumegen_root,
            expect="directory",
        )
        _strict_allowlisted_path(root, root / script_relative, expect="file")
        return python, root

    def _latest_verified_context(
        self,
    ) -> tuple[dict[str, Any], Path, Path]:
        if not self.settings.resumegen_root:
            raise ValidationError("resume root is not configured")
        projections = self.adapter.verified_run_projections(limit=1)
        if not projections:
            raise ValidationError("no fully verified exact run is available")
        latest = projections[-1]
        evidence = latest.get("evidence", {})
        summary = evidence.get("summary")
        source = evidence.get("source_metrics")
        if not isinstance(summary, dict) or not isinstance(source, dict):
            raise ValidationError("verified run evidence pointers are incomplete")
        summary_path = _strict_allowlisted_path(
            self.settings.resumegen_root,
            self.settings.resumegen_root / str(summary.get("path") or ""),
            expect="file",
        )
        source_path = _strict_allowlisted_path(
            self.settings.resumegen_root,
            self.settings.resumegen_root / str(source.get("path") or ""),
            expect="file",
        )
        if not re.fullmatch(r"\d{8}-\d{6}", str(latest.get("run_id") or "")):
            raise ValidationError("verified run id is invalid")
        if not isinstance(latest.get("started_at"), str) or not latest["started_at"]:
            raise ValidationError("verified run start time is unavailable")
        return latest, summary_path, source_path

    def _current_queue_rows(self) -> tuple[Path, list[dict[str, Any]]]:
        if not self.settings.resumegen_root:
            raise ValidationError("resume root is not configured")
        queue_root = _strict_allowlisted_path(
            self.settings.resumegen_root,
            self.settings.resumegen_root
            / "apps"
            / "Apply queues"
            / "current_apply_queue",
            expect="directory",
        )
        manifest_path = _strict_allowlisted_path(
            queue_root, queue_root / "manifest.json", expect="file"
        )
        priority_path = _strict_allowlisted_path(
            queue_root, queue_root / "priority_order.json", expect="file"
        )
        first_manifest = _read_bounded_bytes(manifest_path)
        priority_content = _read_bounded_bytes(priority_path)
        second_manifest = _read_bounded_bytes(manifest_path)
        if hashlib.sha256(first_manifest).digest() != hashlib.sha256(
            second_manifest
        ).digest():
            raise ValidationError("current queue changed during capture")
        manifest = json.loads(second_manifest.decode("utf-8"))
        priority = json.loads(priority_content.decode("utf-8"))
        if not isinstance(manifest, dict) or not isinstance(priority, list):
            raise ValidationError("current queue artifacts have invalid types")
        if manifest.get("queue_type") not in {None, "current_apply_queue"}:
            raise ValidationError("current queue manifest type is invalid")
        rows = [row for row in priority if isinstance(row, dict)]
        return queue_root, rows

    def _current_queue_job(self, job_id: int) -> dict[str, Any]:
        _, rows = self._current_queue_rows()
        for row in rows:
            if _numeric_job_id(row) == job_id:
                return row
        raise ValidationError("job_id is not present in the current apply queue")

    def _queue_job_folder(
        self,
        row: dict[str, Any],
        *,
        queue_root: Path | None = None,
    ) -> Path:
        if queue_root is None:
            queue_root, _ = self._current_queue_rows()
        raw_folder = row.get("folder_path")
        if not isinstance(raw_folder, str) or not raw_folder:
            raise ValidationError("queue item has no application folder")
        candidate = Path(raw_folder).expanduser()
        if not candidate.is_absolute():
            candidate = queue_root / candidate
        return _strict_allowlisted_path(queue_root, candidate, expect="directory")

    def _queue_job_folder_available(
        self,
        row: dict[str, Any],
        *,
        queue_root: Path | None = None,
    ) -> bool:
        try:
            self._queue_job_folder(row, queue_root=queue_root)
        except (OSError, ValueError, ValidationError, json.JSONDecodeError):
            return False
        return True

    def _fixed_environment(self, command_id: str) -> dict[str, str]:
        environment = {
            key: value
            for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
            if (value := os.environ.get(key))
        }
        if command_id == "application.resume.generate":
            key = self._resume_model_key()
            if key:
                environment["ANTHROPIC_API_KEY"] = key
        return environment

    def _resume_model_key(self) -> str:
        existing = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if existing:
            return existing
        if not self.settings.resumegen_root:
            return ""
        try:
            path = _strict_allowlisted_path(
                self.settings.resumegen_root,
                self.settings.resumegen_root / ".env",
                expect="file",
            )
            return _dotenv_value(path, "ANTHROPIC_API_KEY")
        except (OSError, ValueError, UnicodeError):
            return ""

    def _open_availability(
        self,
        command_id: str,
        adapter_status: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        reasons = []
        opener = Path("/usr/bin/open")
        if not opener.is_file() or not os.access(opener, os.X_OK):
            reasons.append("fixed local opener is unavailable")
        if command_id in {
            "open.account_tracker",
            "open.current_apply_queue",
        } and not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append("all engine, queue, and adapter locks must be free")
        try:
            self._open_target(command_id)
        except (OSError, ValueError):
            reasons.append("allowlisted local target is unavailable or unsafe")
        return not reasons, reasons

    def _open_target(
        self,
        command_id: str,
        parameters: dict[str, int] | None = None,
    ) -> Path:
        if command_id == "open.account_tracker":
            if not self.settings.outreach_root:
                raise ValueError("outreach root unavailable")
            return _strict_allowlisted_path(
                self.settings.outreach_root,
                self.settings.outreach_root / "workspace" / "account_tracker.xlsx",
                expect="file",
            )
        if command_id == "open.current_apply_queue":
            if not self.settings.resumegen_root:
                raise ValueError("resume root unavailable")
            return _strict_allowlisted_path(
                self.settings.resumegen_root,
                self.settings.resumegen_root
                / "apps"
                / "Apply queues"
                / "current_apply_queue",
                expect="directory",
            )
        if command_id == "open.latest_report":
            if not self.settings.outreach_root:
                raise ValueError("outreach root unavailable")
            status = self.adapter.status()
            latest = status.get("latest_verified_run")
            if not isinstance(latest, dict):
                raise ValueError("verified report unavailable")
            html = latest.get("evidence", {}).get("outreach_html")
            if not isinstance(html, dict) or not html.get("path"):
                raise ValueError("verified HTML report unavailable")
            return _strict_allowlisted_path(
                self.settings.outreach_root,
                self.settings.outreach_root / str(html["path"]),
                expect="file",
            )
        if command_id == "open.story_workbench":
            if not self.settings.resumegen_root:
                raise ValueError("resume root unavailable")
            return _strict_allowlisted_path(
                self.settings.resumegen_root,
                self.settings.resumegen_root / "docs" / "career_workbench",
                expect="directory",
            )
        if command_id == "open.communication_review":
            if not self.settings.outreach_root:
                raise ValueError("outreach root unavailable")
            directory = _strict_allowlisted_path(
                self.settings.outreach_root,
                self.settings.outreach_root / "workspace" / "comms_learning",
                expect="directory",
            )
            candidates = [
                path
                for path in directory.iterdir()
                if re.fullmatch(
                    r"outcome_recommendation_review_\d{4}-\d{2}-\d{2}\.json",
                    path.name,
                )
            ]
            if not candidates:
                raise ValueError("review artifact unavailable")
            return _strict_allowlisted_path(
                self.settings.outreach_root,
                sorted(candidates, key=lambda path: path.name)[-1],
                expect="file",
            )
        if command_id == "open.application_folder":
            job_id = int((parameters or {}).get("job_id") or 0)
            if job_id < 1:
                raise ValueError("numeric queue job id is required")
            row = self._current_queue_job(job_id)
            return self._queue_job_folder(row)
        raise ValueError("open command is not allowlisted")

    @staticmethod
    def _all_locks_free(locks: dict[str, Any]) -> bool:
        return all(locks.get(name) == "free" for name in _REQUIRED_LOCKS)

    def _finish_job(
        self,
        job_id: str,
        *,
        status: str,
        result_code: str,
        returncode: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        lock_snapshot: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "completed_at": utc_now(),
            "result_code": result_code,
            "returncode": returncode,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest() if stdout else "",
            "stderr_sha256": hashlib.sha256(stderr).hexdigest() if stderr else "",
            "stdout_lines": len(stdout.splitlines()),
            "stderr_lines": len(stderr.splitlines()),
        }
        if lock_snapshot is not None:
            values["lock_snapshot_json"] = json.dumps(lock_snapshot, sort_keys=True)
        self._update_job(job_id, values)

    def _update_job(self, job_id: str, values: dict[str, Any]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE operator_jobs SET {assignments} WHERE id = ? AND user_id = ?",
                (*values.values(), job_id, self.settings.user_id),
            )

    @staticmethod
    def _job_dto(row: dict[str, Any]) -> dict[str, Any]:
        presentation = _COMMAND_PRESENTATION.get(row["command_id"], {})
        return {
            "id": row["id"],
            "command_id": row["command_id"],
            "label": presentation.get("label", row["command_id"]),
            "parameters": _safe_operator_parameters(row["parameters_json"]),
            "status": row["status"],
            "requested_scope": row["requested_scope"],
            "requested_at": row["requested_at"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "confirmation_valid": bool(row["confirmation_valid"]),
            "argv_sha256": row["argv_sha256"],
            "lock_snapshot": _safe_lock_snapshot(row["lock_snapshot_json"]),
            "returncode": row["returncode"],
            "stdout_sha256": row["stdout_sha256"],
            "stderr_sha256": row["stderr_sha256"],
            "stdout_lines": row["stdout_lines"],
            "stderr_lines": row["stderr_lines"],
            "result_code": row["result_code"],
            "summary": _operator_job_summary(
                row["status"], row["result_code"]
            ),
        }

    def _current_apply_queue_assets(
        self,
        current_workspace: dict[str, Any],
    ) -> dict[str, Any]:
        summary = current_workspace.get("application_queue")
        result: dict[str, Any] = {
            "status": current_workspace.get("status", "unavailable"),
            "scope": "current-snapshot",
            "summary": summary if isinstance(summary, dict) else None,
            "reasons": list(current_workspace.get("reasons", [])),
            "evidence": {
                key: value
                for key, value in current_workspace.get("evidence", {}).items()
                if key.startswith("application_")
            },
            "items": [],
            "items_returned": 0,
            "items_total": 0,
            "truncated": False,
            "limit": _QUEUE_ITEM_LIMIT,
        }
        if not self.settings.resumegen_root or not isinstance(summary, dict):
            return result
        queue_root_input = (
            self.settings.resumegen_root
            / "apps"
            / "Apply queues"
            / "current_apply_queue"
        )
        try:
            queue_root = _strict_allowlisted_path(
                self.settings.resumegen_root,
                queue_root_input,
                expect="directory",
            )
            manifest_path = _strict_allowlisted_path(
                queue_root,
                queue_root / "manifest.json",
                expect="file",
            )
            priority_path = _strict_allowlisted_path(
                queue_root,
                queue_root / "priority_order.json",
                expect="file",
            )
            first_manifest = _read_bounded_bytes(manifest_path)
            priority_content = _read_bounded_bytes(priority_path)
            second_manifest = _read_bounded_bytes(manifest_path)
            if hashlib.sha256(first_manifest).digest() != hashlib.sha256(
                second_manifest
            ).digest():
                raise ValueError("queue manifest changed during capture")
            priority = json.loads(priority_content.decode("utf-8"))
            if not isinstance(priority, list):
                raise ValueError("queue priority artifact is not an array")
            expected_count = summary.get("priority_item_count")
            if isinstance(expected_count, int) and expected_count != len(priority):
                raise ValueError("queue summary and row count do not match")
            application_commands = {
                item["command_id"]: item
                for item in self.capabilities()["commands"]
                if item["command_id"]
                in {
                    "application.resume.generate",
                    "application.apply_packet.build",
                    "open.application_folder",
                }
            }
            items = []
            for raw in priority[:_QUEUE_ITEM_LIMIT]:
                if not isinstance(raw, dict):
                    continue
                projected = _queue_item_projection(raw, queue_root)
                if projected is None:
                    continue
                projected["actions"] = self._queue_item_actions(
                    raw, application_commands, queue_root
                )
                items.append(projected)
            result.update(
                {
                    "items": items,
                    "items_returned": len(items),
                    "items_total": len(priority),
                    "truncated": len(priority) > _QUEUE_ITEM_LIMIT,
                }
            )
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            result["status"] = "partial" if summary else "unavailable"
            result["reasons"].append(
                f"Minimized queue rows failed closed: {type(error).__name__}"
            )
        return result

    def _queue_item_actions(
        self,
        row: dict[str, Any],
        capabilities: dict[str, dict[str, Any]],
        queue_root: Path,
    ) -> list[dict[str, Any]]:
        job_id = _numeric_job_id(row)
        actions = []
        for command_id in (
            "application.resume.generate",
            "application.apply_packet.build",
            "open.application_folder",
        ):
            capability = capabilities.get(command_id, {})
            status = str(capability.get("status") or "unavailable")
            reason = str(capability.get("reason") or "")
            if status == "available" and job_id is not None:
                available, selected_reasons = self._selected_action_availability(
                    command_id, row, queue_root
                )
                if not available:
                    status = "unavailable"
                    reason = "; ".join(selected_reasons)
            elif job_id is None:
                status = "unavailable"
                reason = "Queue item does not have a numeric job id."
            actions.append(
                {
                    "command_id": command_id,
                    "status": status,
                    "reason": reason,
                    "confirmation_phrase": _COMMAND_CATALOG[command_id][
                        "confirmation"
                    ],
                    "parameters": {"job_id": job_id} if job_id else None,
                    "asynchronous": command_id in _BACKGROUND_COMMANDS,
                }
            )
        return actions

    def _workbook_assets(self) -> dict[str, Any]:
        if not self.settings.resumegen_root or not self.settings.outreach_root:
            return {
                "status": "unavailable",
                "scope": "current-snapshot",
                "reason": "Existing engine roots are not configured.",
            }
        try:
            resume_path = self.settings.resumegen_root / "discovery" / "jobs.xlsx"
            account_path = self.settings.outreach_root / "workspace" / "account_tracker.xlsx"
            resume_sheets, resume_evidence = _read_xlsx(
                resume_path, self.settings.resumegen_root
            )
            account_sheets, account_evidence = _read_xlsx(
                account_path, self.settings.outreach_root
            )
            return {
                "status": "available",
                "scope": "current-snapshot",
                "resume_workbook": _resume_workbook_projection(
                    resume_sheets, resume_evidence
                ),
                "account_tracker": _account_tracker_projection(
                    account_sheets, account_evidence
                ),
            }
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
            return {
                "status": "unavailable",
                "scope": "current-snapshot",
                "reason": f"Workbook aggregate failed closed: {type(error).__name__}",
            }

    def _story_comms_assets(self) -> dict[str, Any]:
        categories: dict[str, Any] = {}
        story_items: list[dict[str, Any]] = []
        story_item_total = 0
        if self.settings.resumegen_root:
            root = self.settings.resumegen_root
            for label, relative in (
                ("story_engine", "docs/career_workbench/story_engine"),
                ("story_sources", "docs/career_workbench/story_sources"),
                ("interview_prep", "docs/career_workbench/interview_prep"),
                ("story_bank", "cover_letters/story_bank"),
            ):
                categories[label] = _directory_inventory(root / relative, root)
            story_items, story_item_total = _curated_story_items(root)
        else:
            categories.update(
                {
                    "story_engine": {"status": "not_configured"},
                    "story_sources": {"status": "not_configured"},
                    "interview_prep": {"status": "not_configured"},
                    "story_bank": {"status": "not_configured"},
                }
            )

        comms_totals: dict[str, Any] = {}
        recommendation_review: dict[str, Any] = {}
        recommendation_count = 0
        review_decision_count = 0
        review_decision_counts: dict[str, int] = {}
        reasons: list[str] = []
        if self.settings.outreach_root:
            root = self.settings.outreach_root
            comms_dir = root / "workspace" / "comms_learning"
            categories["comms_learning"] = _directory_inventory(comms_dir, root)
            learning_path = comms_dir / "outcome_learning.json"
            if learning_path.is_file():
                try:
                    payload = _read_json_object(
                        _strict_allowlisted_path(root, learning_path, expect="file")
                    )
                    totals = payload.get("totals", {})
                    comms_totals = {
                        key: value
                        for key in (
                            "sends",
                            "accepts",
                            "replies",
                            "rejections",
                            "gold",
                            "silver",
                            "negative",
                            "accept_rate",
                            "reply_rate",
                            "rejection_rate",
                        )
                        if isinstance((value := totals.get(key)), (int, float))
                        and not isinstance(value, bool)
                    }
                    recommendations = payload.get("recommendations")
                    if isinstance(recommendations, list):
                        recommendation_count = len(recommendations)
                except (OSError, ValueError, json.JSONDecodeError):
                    reasons.append("Outcome-learning aggregate is unavailable.")
            review_candidates = sorted(
                comms_dir.glob("outcome_recommendation_review_*.json")
            )
            if review_candidates:
                try:
                    review = _read_json_object(
                        _strict_allowlisted_path(
                            root, review_candidates[-1], expect="file"
                        )
                    )
                    recommendation_review = {
                        "automatic_prompt_changes_applied": bool(
                            review.get("automatic_prompt_changes_applied", False)
                        ),
                        "policy_changes_applied": bool(
                            review.get("policy_changes_applied", False)
                        ),
                    }
                    decisions = review.get("decisions")
                    if isinstance(decisions, list):
                        decision_counter: Counter[str] = Counter()
                        for decision in decisions:
                            if not isinstance(decision, dict):
                                continue
                            value = str(decision.get("decision") or "")
                            decision_counter[
                                value
                                if value in _COMMS_REVIEW_DECISIONS
                                else "other"
                            ] += 1
                        review_decision_count = sum(decision_counter.values())
                        review_decision_counts = dict(sorted(decision_counter.items()))
                except (OSError, ValueError, json.JSONDecodeError):
                    reasons.append("Communication-review aggregate is unavailable.")
        else:
            categories["comms_learning"] = {"status": "not_configured"}
        status = (
            "available"
            if any(item.get("status") == "available" for item in categories.values())
            else "unavailable"
        )
        story_inventory = [
            {
                "label": label.replace("_", " ").title(),
                "category": label,
                "status": inventory.get("status", "unavailable"),
                "count": inventory.get("file_count", 0),
            }
            for label, inventory in categories.items()
            if label != "comms_learning"
        ]
        story_total = sum(
            int(item.get("count", 0))
            for item in story_inventory
            if isinstance(item.get("count"), int)
        )
        canonical_count = sum(
            1 for item in story_items if item["category"] == "canonical_story"
        )
        return {
            "status": status,
            "scope": "current-snapshot",
            "inventories": categories,
            "outcome_totals": comms_totals,
            "recommendation_review": recommendation_review,
            "stories": {
                "status": "available" if story_items or story_total else "unavailable",
                "file_count": story_total,
                "canonical_count": canonical_count,
                "private_status": "protected",
                "inventory": story_inventory,
                "items": story_items,
                "items_returned": len(story_items),
                "items_total": story_item_total,
                "truncated": story_item_total > _STORY_ITEM_LIMIT,
                "limit": _STORY_ITEM_LIMIT,
            },
            "communications": {
                "status": categories.get("comms_learning", {}).get(
                    "status", "unavailable"
                ),
                "totals": comms_totals,
                "recommendation_count": recommendation_count,
                "review_decision_count": review_decision_count,
                "review_decision_counts": review_decision_counts,
                "pending_review_count": max(
                    recommendation_count - review_decision_count, 0
                ),
                "review": recommendation_review,
            },
            "reasons": reasons,
        }

    @staticmethod
    def _report_assets(run_projections: list[dict[str, Any]]) -> dict[str, Any]:
        reports = []
        for run in run_projections:
            report = run.get("report", {})
            reports.append(
                {
                    "run_id": run.get("run_id"),
                    "status": run.get("status"),
                    "started_at": run.get("started_at"),
                    "completed_at": run.get("completed_at"),
                    "failure_count": run.get("failure_count", 0),
                    "source_count": len(run.get("sources", [])),
                    "workspace_counts": _allowlisted_numeric_mapping(
                        report.get("workspace_counts"), _WORKSPACE_COUNT_FIELDS
                    ),
                    "invite_totals": _allowlisted_numeric_mapping(
                        report.get("invite_totals"), _INVITE_TOTAL_FIELDS
                    ),
                    "pending_review_count": report.get("pending_review_count", 0),
                    "evidence": run.get("evidence", {}).get("outreach_report"),
                }
            )
        return {
            "status": "available" if reports else "unavailable",
            "scope": "run-scoped",
            "count": len(reports),
            "total": len(reports),
            "items_returned": len(reports),
            "truncated": False,
            "latest_run_id": reports[-1]["run_id"] if reports else None,
            "failure_count": reports[-1]["failure_count"] if reports else 0,
            "items": reports,
            "reason": "" if reports else "No report passed the complete run evidence chain.",
        }

    @staticmethod
    def _source_assets(run_projections: list[dict[str, Any]]) -> dict[str, Any]:
        if not run_projections:
            return {
                "status": "unavailable",
                "scope": "run-scoped",
                "reason": "No source metrics passed the complete run evidence chain.",
                "run_id": None,
                "failure_count": 0,
                "total": 0,
                "items": [],
                "latest": None,
            }
        latest = run_projections[-1]
        sources = []
        for source in latest.get("sources", []):
            if not isinstance(source, dict):
                continue
            status = str(source.get("status") or "not_reported")
            errors = (
                [f"Exact run manifest reported source status {status}."]
                if status
                in {
                    "failed",
                    "timed_out",
                    "failed_missing_artifact",
                    "not_reported",
                }
                else []
            )
            sources.append(
                {
                    "source": source.get("source"),
                    "status": status,
                    "raw_count": source.get("raw_count", 0),
                    "kept_count": source.get("kept_count", 0),
                    "errors": errors,
                }
            )
        return {
            "status": "available",
            "scope": "run-scoped",
            "run_id": latest.get("run_id"),
            "failure_count": latest.get("failure_count", 0),
            "total": len(sources),
            "items": sources,
            "latest": {
                "run_id": latest.get("run_id"),
                "status": latest.get("status"),
                "failure_count": latest.get("failure_count", 0),
                "sources": sources,
                "evidence": latest.get("evidence", {}).get("source_metrics"),
            },
        }


def _safe_lock_snapshot(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    allowed = {"free", "busy", "unavailable", "not_configured"}
    return {
        str(key): str(state)
        for key, state in parsed.items()
        if str(state) in allowed
    }


def _safe_operator_parameters(value: str) -> dict[str, int]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict) or set(parsed) not in (set(), {"job_id"}):
        return {}
    job_id = parsed.get("job_id")
    if job_id is None:
        return {}
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        return {}
    return {"job_id": job_id}


def _operator_job_summary(status: str, result_code: str) -> str:
    summaries = {
        "preflight_valid": "Production release preflight passed.",
        "preflight_failed": "Production release preflight failed.",
        "local_open_requested": "The fixed local target was opened.",
        "local_open_failed": "The fixed local target could not be opened.",
        "capability_forbidden": "This capability is forbidden by policy.",
        "capability_unavailable": "This capability is unavailable in the current guard state.",
        "adapter_lock_busy": "The companion guard lock is busy.",
        "engine_locks_not_free": "An upstream engine lock is not free.",
        "preflight_surface_changed": "The fixed preflight surface changed and was rejected.",
        "open_target_changed": "The fixed open target changed and was rejected.",
        "local_opener_unavailable": "The fixed local opener is unavailable.",
        "preflight_spawn_failed": "The fixed preflight process could not start.",
        "timeout": "The fixed preflight timed out.",
        "not_executable": "The capability is not executable in this release.",
        "account_tracker_refreshed": "The account tracker workbook was refreshed.",
        "daily_report_refreshed": "The exact-run daily report was refreshed.",
        "role_surface_refreshed": "The exact-run role-surface report was refreshed.",
        "cadence_report_refreshed": "The cadence report was refreshed.",
        "outcome_report_refreshed": "The outcome-learning report was refreshed.",
        "communication_lab_refreshed": "The communication lab was refreshed.",
        "outreach_plan_built": "A review-only Track 2 plan was built.",
        "resume_generation_completed": "One resume-only budget generation completed.",
        "apply_packet_built": "One no-submit apply packet was built.",
        "fixed_surface_changed": "A fixed command surface changed and was rejected.",
        "fixed_command_timeout": "The fixed command reached its bounded timeout.",
        "fixed_command_spawn_failed": "The fixed command could not start.",
        "fixed_command_failed": "The fixed command returned a failure status.",
        "worker_start_failed": "The background worker could not start.",
        "worker_internal_error": "The background worker failed closed.",
    }
    return summaries.get(result_code, f"Operator job {status}.")


def _numeric_job_id(row: dict[str, Any]) -> int | None:
    raw = row.get("id") if row.get("id") is not None else row.get("job_id")
    candidate = str(raw or "").strip()
    if not re.fullmatch(r"[1-9]\d{0,11}", candidate):
        return None
    return int(candidate)


def _folder_has_resume(folder: Path) -> bool:
    try:
        return any(
            item.is_file()
            and not item.is_symlink()
            and item.name.casefold().startswith("resume_")
            and item.suffix.casefold() in {".docx", ".pdf", ".txt"}
            for item in folder.iterdir()
        )
    except OSError:
        return False


def _dotenv_value(path: Path, key: str) -> str:
    content = _read_bounded_bytes(path, limit=256 * 1024).decode("utf-8")
    prefix = f"{key}="
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or len(value) > 4096 or any(
            character in value for character in ("\x00", "\r", "\n")
        ):
            return ""
        return value
    return ""


def _read_bounded_bytes(path: Path, *, limit: int = 20 * 1024 * 1024) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError("artifact exceeds the operator limit")
    return path.read_bytes()


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(_read_bounded_bytes(path).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON artifact is not an object")
    return value


def _queue_item_projection(
    row: dict[str, Any],
    queue_root: Path,
) -> dict[str, Any] | None:
    company = _safe_display_text(row.get("company"), maximum=160)
    role = _safe_display_text(
        row.get("role_title") or row.get("role"), maximum=180
    )
    if not company and not role:
        return None
    raw_identifier = str(row.get("id") or row.get("job_id") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", raw_identifier):
        job_id = raw_identifier
    elif raw_identifier:
        job_id = "job_" + hashlib.sha256(raw_identifier.encode("utf-8")).hexdigest()[:16]
    else:
        stable = f"{company}\0{role}".encode("utf-8")
        job_id = "job_" + hashlib.sha256(stable).hexdigest()[:16]
    status = str(row.get("status") or "").strip()
    queue_bucket = str(row.get("queue_bucket") or "").strip()
    material = _queue_item_material(row, queue_root)
    ready_count = sum(1 for value in material.values() if value)
    return {
        "id": job_id,
        "job_id": job_id,
        "company": company or "Unknown company",
        "role": role or "Role not labeled",
        "role_title": role or "Role not labeled",
        "fit_score": _as_number(row.get("fit_score")),
        "priority_score": _as_number(row.get("priority_score")),
        "priority_rank": _safe_integer(row.get("priority_rank")),
        "status": status if status in _QUEUE_STATUSES else "other",
        "queue_bucket": (
            queue_bucket if queue_bucket in _QUEUE_BUCKETS else "other"
        ),
        "in_latest_run": row.get("in_latest_run") is True,
        "has_resume": material["resume"],
        "has_cover_letter": material["cover_letter"],
        "has_job_description": material["job_description"],
        "has_strategy": material["strategy"],
        "has_intel": material["intel"],
        "material_flags": material,
        "material_state": (
            "complete"
            if ready_count == len(material)
            else "partial"
            if ready_count
            else "missing"
        ),
    }


def _queue_item_material(row: dict[str, Any], queue_root: Path) -> dict[str, bool]:
    flags = {
        "resume": False,
        "cover_letter": False,
        "job_description": False,
        "strategy": False,
        "intel": False,
    }
    raw_folder = row.get("folder_path")
    if not isinstance(raw_folder, str) or not raw_folder:
        return flags
    candidate = Path(raw_folder).expanduser()
    if not candidate.is_absolute():
        candidate = queue_root / candidate
    try:
        folder = _strict_allowlisted_path(queue_root, candidate, expect="directory")
        names = {
            item.name.casefold()
            for item in folder.iterdir()
            if item.is_file() and not item.is_symlink()
        }
    except (OSError, ValueError):
        return flags
    flags["resume"] = any(
        name.startswith("resume_") and name.endswith((".docx", ".pdf", ".txt"))
        for name in names
    )
    flags["cover_letter"] = any(
        (
            name.startswith("cl_")
            or name.startswith("cover_letter")
            or name.startswith("cover-letter")
        )
        and name.endswith((".docx", ".pdf", ".txt"))
        for name in names
    )
    flags["job_description"] = "jd.txt" in names
    flags["strategy"] = "strategy.json" in names
    flags["intel"] = "intel.txt" in names
    return flags


def _curated_story_items(root: Path) -> tuple[list[dict[str, Any]], int]:
    candidates: list[dict[str, Any]] = []
    for category, relative in (
        ("canonical_story", "docs/career_workbench/story_engine/stories"),
        ("story_source", "docs/career_workbench/story_sources"),
        ("story_bank", "cover_letters/story_bank"),
    ):
        directory = root / relative
        try:
            safe_directory = _strict_allowlisted_path(
                root, directory, expect="directory"
            )
        except (OSError, ValueError):
            continue
        for path in safe_directory.rglob("*.md"):
            if path.is_symlink() or path.name.startswith("."):
                continue
            normalized_stem = path.stem.strip().casefold().replace("-", "_")
            if normalized_stem in {"readme", "template"} or any(
                marker in normalized_stem for marker in ("audit", "private_prep")
            ):
                continue
            if category == "story_bank" and not any(
                marker in normalized_stem
                for marker in ("story", "behaviour", "behavior", "case", "project")
            ):
                continue
            try:
                safe_path = _strict_allowlisted_path(
                    safe_directory, path, expect="file"
                )
            except (OSError, ValueError):
                continue
            filename = _safe_display_text(safe_path.name, maximum=160)
            title = _safe_display_text(
                re.sub(r"[_-]+", " ", safe_path.stem), maximum=140
            )
            if not filename or not title:
                continue
            candidates.append(
                {
                    "filename": filename,
                    "title": title,
                    "label": title,
                    "category": category,
                    "status": "available",
                }
            )
    candidates.sort(key=lambda item: (item["category"], item["title"].casefold()))
    return candidates[:_STORY_ITEM_LIMIT], len(candidates)


def _safe_display_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    normalized = "".join(
        character for character in normalized if character.isprintable()
    )
    return normalized[:maximum]


def _safe_date(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate) else ""


def _safe_integer(value: Any) -> int | None:
    number = _as_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _directory_inventory(path: Path, root: Path) -> dict[str, Any]:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError:
        return {"status": "unavailable", "file_count": 0, "total_bytes": 0}
    if not resolved.is_relative_to(resolved_root) or not resolved.is_dir():
        return {"status": "unavailable", "file_count": 0, "total_bytes": 0}
    extensions: Counter[str] = Counter()
    file_count = 0
    total_bytes = 0
    for item in resolved.rglob("*"):
        if not item.is_file() or item.is_symlink() or item.name.startswith("."):
            continue
        try:
            item_resolved = item.resolve(strict=True)
            if not item_resolved.is_relative_to(resolved_root):
                continue
            size = item.stat().st_size
        except OSError:
            continue
        suffix = item.suffix.lower()
        extension = (
            suffix
            if suffix in {".md", ".txt", ".json", ".jsonl", ".docx", ".html"}
            else "other"
        )
        extensions[extension] += 1
        file_count += 1
        total_bytes += size
    return {
        "status": "available",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "extension_counts": dict(sorted(extensions.items())),
    }


def _read_xlsx(path: Path, root: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise ValueError("workbook escapes its configured root")
    content = resolved.read_bytes()
    if len(content) > _MAX_XLSX_BYTES:
        raise ValueError("workbook exceeds the operator limit")
    sheets: dict[str, list[dict[str, str]]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > _MAX_XLSX_EXPANDED_BYTES:
            raise ValueError("expanded workbook exceeds the operator limit")
        shared = _xlsx_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"] for item in relationships
        }
        for sheet in workbook.iter(f"{{{_XLSX_MAIN_NS}}}sheet"):
            name = sheet.attrib.get("name", "")
            relationship_id = sheet.attrib.get(f"{{{_XLSX_REL_NS}}}id")
            if not name or not relationship_id or relationship_id not in targets:
                continue
            target = targets[relationship_id].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            rows = _xlsx_sheet_rows(archive.read(target), shared)
            sheets[name] = rows
    evidence = {
        "state": "valid",
        "path": resolved.relative_to(resolved_root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
    return sheets, evidence


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.iter(f"{{{_XLSX_MAIN_NS}}}t"))
        for item in root.iter(f"{{{_XLSX_MAIN_NS}}}si")
    ]


def _xlsx_sheet_rows(content: bytes, shared: list[str]) -> list[dict[str, str]]:
    root = ET.fromstring(content)
    raw_rows: list[dict[int, str]] = []
    for row in root.iter(f"{{{_XLSX_MAIN_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{_XLSX_MAIN_NS}}}c"):
            index = _column_index(cell.attrib.get("r", "A1"))
            cell_type = cell.attrib.get("t", "")
            value_node = cell.find(f"{{{_XLSX_MAIN_NS}}}v")
            inline_node = cell.find(f"{{{_XLSX_MAIN_NS}}}is")
            value = ""
            if cell_type == "s" and value_node is not None:
                shared_index = int(value_node.text or "0")
                if 0 <= shared_index < len(shared):
                    value = shared[shared_index]
            elif cell_type == "inlineStr" and inline_node is not None:
                value = "".join(
                    text.text or ""
                    for text in inline_node.iter(f"{{{_XLSX_MAIN_NS}}}t")
                )
            elif value_node is not None:
                value = value_node.text or ""
            values[index] = value
        raw_rows.append(values)
    if not raw_rows:
        return []
    headers = {
        index: value.strip()
        for index, value in raw_rows[0].items()
        if value.strip()
    }
    return [
        {header: row.get(index, "") for index, header in headers.items()}
        for row in raw_rows[1:]
    ]


def _column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    result = 0
    for character in letters.upper():
        result = result * 26 + ord(character) - 64
    return max(result - 1, 0)


def _resume_workbook_projection(
    sheets: dict[str, list[dict[str, str]]], evidence: dict[str, Any]
) -> dict[str, Any]:
    jobs = [row for row in sheets.get("Jobs", []) if row.get("id")]
    archive = [row for row in sheets.get("Archive", []) if row.get("id")]
    review = [row for row in sheets.get("ReviewCache", []) if row.get("cache_key")]
    return {
        "evidence": evidence,
        "jobs": {
            "row_count": len(jobs),
            "status_counts": _allowlisted_counts(jobs, "status", _JOB_STATUSES),
            "source_counts": _allowlisted_counts(jobs, "source", _JOB_SOURCES),
            "role_type_counts": _allowlisted_counts(jobs, "role_type", _ROLE_TYPES),
            "fit_score": _numeric_summary(jobs, "fit_score"),
        },
        "archive": {
            "row_count": len(archive),
            "status_counts": _allowlisted_counts(archive, "status", _JOB_STATUSES),
            "source_counts": _allowlisted_counts(archive, "source", _JOB_SOURCES),
            "role_type_counts": _allowlisted_counts(
                archive, "role_type", _ROLE_TYPES
            ),
        },
        "review_cache": {
            "row_count": len(review),
            "decision_counts": _allowlisted_counts(
                review, "decision", _REVIEW_DECISIONS
            ),
            "category_counts": _allowlisted_counts(
                review, "category", _REVIEW_CATEGORIES
            ),
        },
    }


def _account_tracker_projection(
    sheets: dict[str, list[dict[str, str]]], evidence: dict[str, Any]
) -> dict[str, Any]:
    master = [
        row for row in sheets.get("Account Tracker", []) if row.get("Company")
    ]
    master_by_company = {
        row["Company"].strip().casefold(): row
        for row in master
        if row.get("Company", "").strip()
    }
    action_rows = [
        row for row in sheets.get("Action Queue", []) if row.get("Company")
    ]
    action_items = []
    for row in action_rows[:_ACCOUNT_ACTION_LIMIT]:
        company = _safe_display_text(row.get("Company"), maximum=160)
        if not company:
            continue
        master_row = master_by_company.get(company.casefold(), {})
        tier = str(row.get("Tier") or master_row.get("Tier") or "").strip()
        stage = str(master_row.get("Account Stage") or "").strip()
        next_action = str(row.get("Next Action") or "").strip()
        action_items.append(
            {
                "company": company,
                "tier": tier if tier in _ACCOUNT_TIERS else "other",
                "stage": stage if stage in _ACCOUNT_STAGES else "other",
                "next_action": (
                    next_action
                    if next_action in _ACCOUNT_ACTIONS
                    else "Review account action"
                ),
                "next_due": _safe_date(row.get("Next Due")),
                "account_score": _as_number(
                    row.get("Account Score") or master_row.get("Account Score")
                ),
                "fit_score": _as_number(
                    row.get("Fit Score") or master_row.get("Fit Score")
                ),
            }
        )
    numeric_columns = (
        "People Mapped",
        "Email Contacts",
        "Invites Sent",
        "Accepted",
        "Replies",
        "Coffee Chats",
        "Advocates",
    )
    expected_sheets = (
        "Account Tracker",
        "Tier A — Active Campaign",
        "Action Queue",
        "Campaign Plan",
        "Startup Founder-Led",
        "Growth Mid-Market",
        "Large Company",
        "Large Company Priority",
        "Strategic Wishlist",
        "Needs Enrichment",
    )
    return {
        "evidence": evidence,
        "account_count": len(master),
        "action_count": len(action_rows),
        "action_items": action_items,
        "action_items_returned": len(action_items),
        "action_items_total": len(action_rows),
        "action_items_truncated": len(action_rows) > _ACCOUNT_ACTION_LIMIT,
        "action_item_limit": _ACCOUNT_ACTION_LIMIT,
        "tier_counts": _allowlisted_counts(master, "Tier", _ACCOUNT_TIERS),
        "stage_counts": _allowlisted_counts(
            master, "Account Stage", _ACCOUNT_STAGES
        ),
        "activity_totals": {
            column: int(sum(_as_number(row.get(column)) or 0 for row in master))
            for column in numeric_columns
        },
        "people_mapped": int(
            sum(_as_number(row.get("People Mapped")) or 0 for row in master)
        ),
        "score_summary": {
            "account_score": _numeric_summary(master, "Account Score"),
            "fit_score": _numeric_summary(master, "Fit Score"),
        },
        "sheet_row_counts": {
            name: sum(1 for row in sheets.get(name, []) if row.get("Company"))
            for name in expected_sheets
            if name in sheets
        },
        "sheet_count": sum(1 for name in expected_sheets if name in sheets),
    }


def _allowlisted_counts(
    rows: Iterable[dict[str, str]], field: str, allowed: set[str]
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = (row.get(field) or "").strip()
        counts[value if value in allowed else "other"] += 1
    return dict(sorted(counts.items()))


def _allowlisted_numeric_mapping(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number
        for key in sorted(allowed)
        if isinstance((number := value.get(key)), (int, float))
        and not isinstance(number, bool)
    }


def _numeric_summary(rows: Iterable[dict[str, str]], field: str) -> dict[str, Any]:
    values = [value for row in rows if (value := _as_number(row.get(field))) is not None]
    return {
        "count": len(values),
        "average": round(sum(values) / len(values), 3) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _preserved_executable_path(candidate: Path, label: str) -> Path:
    """Validate an executable without resolving away a virtualenv launcher.

    Python virtualenv entrypoints are commonly symlinks to a base interpreter.
    Executing the resolved target loses the adjacent ``pyvenv.cfg`` and its
    installed packages, so the fixed argv must retain the configured path.
    """
    path = candidate.expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValidationError(f"configured {label} is not executable")
    return path


def _strict_allowlisted_path(root: Path, candidate: Path, *, expect: str) -> Path:
    root_input = root.expanduser().absolute()
    candidate_input = (
        candidate.expanduser().absolute()
        if candidate.is_absolute()
        else (root_input / candidate).absolute()
    )
    if root_input.is_symlink():
        raise ValueError("configured root cannot be a symlink")
    try:
        relative = candidate_input.relative_to(root_input)
    except ValueError as error:
        raise ValueError("allowlisted path escapes its configured root") from error
    cursor = root_input
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("allowlisted path traverses a symlink")
    resolved_root = root_input.resolve(strict=True)
    resolved = candidate_input.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("allowlisted path escapes its configured root")
    if expect == "file" and not resolved.is_file():
        raise ValueError("allowlisted file is unavailable")
    if expect == "directory" and not resolved.is_dir():
        raise ValueError("allowlisted directory is unavailable")
    return resolved

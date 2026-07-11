from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .config import Settings
from .db import Database
from .existing_adapter import ExistingEngineAdapter
from .service import ConflictError, NotFoundError, ValidationError, new_id, utc_now


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
_REVIEW_ITEM_LIMIT = 100
_REVIEW_TARGET_LIMIT = 50
_REVIEW_TTL_HOURS = 24
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

_SAFE_REVIEW_ID = re.compile(r"^review_[a-f0-9]{32}$")
_SAFE_TARGET_ID = re.compile(r"^target_[a-f0-9]{24}$")
_REVIEW_CONFIRMATION = "REVIEW_EXACT_TARGET"
_APPROVAL_CONFIRMATION = "APPROVE_EXACT_TARGET"
_REVOCATION_CONFIRMATION = "REVOKE_EXACT_TARGET"
_CONTENT_UPDATE_CONFIRMATION = "UPDATE_EXACT_REVIEW_CONTENT"
_APPLY_ASSIST_BLOCKED_REASON = (
    "Live browser fill is disabled because the installed rtrvr runner has no "
    "tool-enforced final-submit interceptor; a prompt-only stop rule is not an "
    "acceptable human-submit boundary."
)
_SAFE_NIGHTLY_PIPELINE_ARGS = (
    "--cycle-config",
    "offcycle_light",
    "--generate",
    "--prepare-outreach",
    "--target-sends",
    "0",
    "--per-company-send-limit",
    "5",
    "--send-min-score",
    "20",
    "--linkedin-discovery-timeout",
    "1800",
    "--outreach-resolve-limit",
    "20",
    "--outreach-enrich-limit",
    "20",
    "--outreach-campaign-limit",
    "30",
    "--outreach-timeout-seconds",
    "4",
    "--outreach-max-search-results",
    "3",
    "--execute-track-2-daily-plan",
    "--track-2-total-actions",
    "auto",
    "--track-2-companies",
    "auto",
    "--track-2-linkedin-invites",
    "auto",
    "--track-2-linkedin-followups",
    "auto",
    "--track-2-company-mapping",
    "auto",
    "--track-2-email-research",
    "auto",
    "--track-2-context-enrichment",
    "8",
    "--track-2-email-drafts",
    "auto",
)
_REVIEW_GATED_COMMANDS = {
    "nightly.run",
    "outreach.linkedin.send",
    "outreach.email.send",
    "application.assist.fill_to_review",
    "application.status.applied",
    "application.status.closed",
}
_LIFECYCLE_COMMANDS = {
    "application.status.applied",
    "application.status.closed",
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
        "confirmation": "RUN_REVIEWED_NIGHTLY",
        "policy": "contract_required",
        "reason": (
            "Requires one current release review and the reviewed-actions runtime gate."
        ),
    },
    "outreach.send": {
        "kind": "external_delivery",
        "confirmation": "",
        "policy": "forbidden",
        "reason": "Choose a recipient-bound LinkedIn or email review lane instead.",
    },
    "outreach.linkedin.send": {
        "kind": "external_delivery",
        "confirmation": "SEND_ONE_REVIEWED_LINKEDIN_MESSAGE",
        "policy": "contract_required",
        "reason": (
            "Requires one current recipient-bound review, the replay-protected "
            "LinkedIn executor, and the reviewed-actions runtime gate."
        ),
    },
    "outreach.email.send": {
        "kind": "external_delivery",
        "confirmation": "SEND_ONE_REVIEWED_EMAIL",
        "policy": "contract_required",
        "reason": (
            "Requires one current recipient-bound review, configured SMTP, and "
            "the reviewed-actions runtime gate."
        ),
    },
    "application.assist.fill_to_review": {
        "kind": "browser_fill_to_review",
        "confirmation": "FILL_ONE_REVIEWED_APPLICATION_TO_REVIEW",
        "policy": "contract_required",
        "reason": (
            "Requires one fingerprinted application review, an attested assist "
            "runner, configured rtrvr access, and the reviewed-actions runtime gate."
        ),
    },
    "application.status.applied": {
        "kind": "status_archive",
        "confirmation": "ARCHIVE_ONE_REVIEWED_APPLICATION_AS_APPLIED",
        "policy": "contract_required",
        "reason": (
            "Requires one approved current-queue target and the installed "
            "artifact-preserving lifecycle contract."
        ),
    },
    "application.status.closed": {
        "kind": "status_archive",
        "confirmation": "ARCHIVE_ONE_REVIEWED_APPLICATION_AS_CLOSED",
        "policy": "contract_required",
        "reason": (
            "Requires one approved current-queue target and the installed "
            "artifact-preserving lifecycle contract."
        ),
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
_REVIEW_PARAMETERS_SCHEMA = {
    "type": "object",
    "additional_properties": False,
    "required": ["review_id", "target_id"],
    "properties": {
        "review_id": {
            "type": "string",
            "pattern": "^review_[a-f0-9]{32}$",
            "description": "Durable approved review created by this companion.",
        },
        "target_id": {
            "type": "string",
            "pattern": "^target_[a-f0-9]{24}$",
            "description": "Opaque exact target identifier projected by this companion.",
        },
    },
}
_PARAMETER_SCHEMAS = {
    command_id: (
        _REVIEW_PARAMETERS_SCHEMA
        if command_id in _REVIEW_GATED_COMMANDS
        else _JOB_PARAMETERS_SCHEMA
        if command_id in {
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
    "nightly.run",
    "outreach.email.send",
    "application.assist.fill_to_review",
    *_LIFECYCLE_COMMANDS,
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
    "application.status.applied": 180,
    "application.status.closed": 180,
    "nightly.run": 21_600,
    "outreach.email.send": 300,
    "outreach.linkedin.send": 300,
    "application.assist.fill_to_review": 900,
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
    "application.status.applied": "application_archived_applied",
    "application.status.closed": "application_archived_closed",
    "nightly.run": "reviewed_nightly_completed",
    "outreach.email.send": "reviewed_email_completed",
    "outreach.linkedin.send": "reviewed_linkedin_completed",
    "application.assist.fill_to_review": "apply_assist_run_completed",
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
        "description": (
            "Run one reviewed prepare/generate cycle with all direct delivery flags omitted."
        ),
        "category": "production",
        "risk": "external",
    },
    "outreach.send": {
        "label": "Legacy generic outreach send",
        "description": "Disabled: reviewed delivery must name one channel and recipient.",
        "category": "communications",
        "risk": "external",
    },
    "outreach.linkedin.send": {
        "label": "Send one reviewed LinkedIn message",
        "description": (
            "Requires one exact recipient, one immutable reviewed draft, approval, "
            "typed confirmation, and the shared production locks."
        ),
        "category": "communications",
        "risk": "external",
    },
    "outreach.email.send": {
        "label": "Send one reviewed email",
        "description": (
            "Requires one exact recipient, one immutable reviewed draft, approval, "
            "typed confirmation, and the shared production locks."
        ),
        "category": "communications",
        "risk": "external",
    },
    "application.assist.fill_to_review": {
        "label": "Application fill safety gate",
        "description": (
            "Live browser fill stays blocked until the runner has a tool-enforced "
            "final-submit interceptor and authoritative terminal receipt."
        ),
        "category": "applications",
        "risk": "external",
    },
    "application.status.applied": {
        "label": "Archive reviewed job as applied",
        "description": "One-job status transition with artifact-preserving archive proof.",
        "category": "applications",
        "risk": "external",
    },
    "application.status.closed": {
        "label": "Archive reviewed job as closed",
        "description": "One-job terminal transition with artifact-preserving archive proof.",
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
                "requires_approved_review": command_id in _REVIEW_GATED_COMMANDS,
                "maximum_items": 1 if command_id in _REVIEW_GATED_COMMANDS else None,
                "execution_contract": (
                    "unproven" if command_id in _REVIEW_GATED_COMMANDS else "fixed"
                ),
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
            elif command_id == "nightly.run":
                available, reasons = self._nightly_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
                command["execution_contract"] = "proven"
            elif command_id == "outreach.email.send":
                available, reasons = self._email_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
                command["execution_contract"] = "proven"
            elif command_id == "outreach.linkedin.send":
                available, reasons = self._linkedin_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
                command["execution_contract"] = "proven"
            elif command_id == "application.assist.fill_to_review":
                available, reasons = self._apply_assist_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
                command["execution_contract"] = "blocked_final_submit_guard"
            elif command_id in _LIFECYCLE_COMMANDS:
                available, reasons = self._lifecycle_availability(adapter_status)
                command["status"] = "available" if available else "unavailable"
                command["reason"] = "" if available else "; ".join(reasons)
                command["execution_contract"] = "proven"
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
        command_states = {
            command["command_id"]: command["status"] for command in commands
        }
        return {
            "schema_version": "1.1",
            "mode": "existing" if adapter_status["configured"] else "portable",
            "data_class": "local-private",
            "mutations_enabled": guarded_writes,
            "guarded_local_writes_enabled": guarded_writes,
            "arbitrary_commands_allowed": False,
            "external_sends_allowed": any(
                command_states.get(command_id) == "available"
                for command_id in {
                    "outreach.linkedin.send",
                    "outreach.email.send",
                }
            ),
            "external_send_policy": "reviewed_single_target_only",
            "automatic_applications_allowed": False,
            "full_nightly_allowed": command_states.get("nightly.run") == "available",
            "review_workflows_enabled": True,
            "reviewed_actions_enabled": self.settings.allow_reviewed_actions,
            "approved_external_actions_enabled": any(
                command_states.get(command_id) == "available"
                for command_id in _REVIEW_GATED_COMMANDS
            ),
            "locks": adapter_status["locks"],
            "busy": adapter_status["busy"],
            "production_guard": adapter_status["production_guard"],
            "commands": commands,
        }

    def overview(self) -> dict[str, Any]:
        capability = self.capabilities()
        assets = self.assets()
        return {
            "schema_version": "1.1",
            "generated_at": utc_now(),
            "mode": capability["mode"],
            "data_class": "local-private",
            "guard": {
                "locks": capability["locks"],
                "busy": capability["busy"],
                "production_guard": capability["production_guard"],
                "external_actions": (
                    "reviewed-single-target-only"
                    if capability["approved_external_actions_enabled"]
                    else "disabled"
                ),
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
                "external_send_policy": capability["external_send_policy"],
                "automatic_applications_allowed": capability[
                    "automatic_applications_allowed"
                ],
                "full_nightly_allowed": capability["full_nightly_allowed"],
                "review_workflows_enabled": capability[
                    "review_workflows_enabled"
                ],
                "approved_external_actions_enabled": capability[
                    "approved_external_actions_enabled"
                ],
                "reviewed_actions_enabled": capability[
                    "reviewed_actions_enabled"
                ],
                "commands": capability["commands"],
            },
            "assets": assets,
            "recent_jobs": self.list_jobs(limit=10),
            "review_queue": self.review_queue(),
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

    def review_queue(self) -> dict[str, Any]:
        targets = self.review_targets()
        reviews = self.list_reviews(limit=25)
        counts = Counter(str(review.get("state") or "unknown") for review in reviews)
        return {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "data_class": "local-private-minimized",
            "review_confirmation_phrase": _REVIEW_CONFIRMATION,
            "approval_confirmation_phrase": _APPROVAL_CONFIRMATION,
            "revocation_confirmation_phrase": _REVOCATION_CONFIRMATION,
            "expires_after_hours": _REVIEW_TTL_HOURS,
            "maximum_items_per_action": 1,
            "lanes": targets["lanes"],
            "recent_reviews": reviews,
            "review_counts": dict(sorted(counts.items())),
            "execution_boundary": (
                "Review and approval are durable but never execute by themselves. "
                "A separate typed confirmation can run only an installed fixed "
                "single-target contract whose readiness checks currently pass."
            ),
        }

    def review_targets(self) -> dict[str, Any]:
        records, collection_reasons = self._review_target_records()
        by_command: dict[str, list[dict[str, Any]]] = {
            command_id: [] for command_id in _REVIEW_GATED_COMMANDS
        }
        for record in records:
            by_command[record["command_id"]].append(
                self._public_review_target(record)
            )
        lanes = []
        adapter_status = self.adapter.status()
        for command_id in sorted(_REVIEW_GATED_COMMANDS):
            definition = _COMMAND_CATALOG[command_id]
            presentation = _COMMAND_PRESENTATION[command_id]
            targets = by_command[command_id][:_REVIEW_TARGET_LIMIT]
            reason = str(definition.get("reason") or "")
            if collection_reasons.get(command_id):
                reason = "; ".join(collection_reasons[command_id])
            execution_state = "contract_required"
            if command_id == "nightly.run":
                executable, execution_reasons = self._nightly_availability(
                    adapter_status
                )
                execution_state = "available" if executable else "unavailable"
                if execution_reasons:
                    reason = "; ".join(execution_reasons)
            elif command_id == "outreach.linkedin.send":
                executable, execution_reasons = self._linkedin_availability(
                    adapter_status
                )
                execution_state = "available" if executable else "unavailable"
                if execution_reasons:
                    reason = "; ".join(execution_reasons)
            elif command_id == "outreach.email.send":
                executable, execution_reasons = self._email_availability(
                    adapter_status
                )
                execution_state = "available" if executable else "unavailable"
                if execution_reasons:
                    reason = "; ".join(execution_reasons)
            elif command_id == "application.assist.fill_to_review":
                executable, execution_reasons = self._apply_assist_availability(
                    adapter_status
                )
                execution_state = "available" if executable else "unavailable"
                if execution_reasons:
                    reason = "; ".join(execution_reasons)
            elif command_id in _LIFECYCLE_COMMANDS:
                executable, execution_reasons = self._lifecycle_availability(adapter_status)
                execution_state = "available" if executable else "unavailable"
                if execution_reasons:
                    reason = "; ".join(execution_reasons)
            lanes.append(
                {
                    "command_id": command_id,
                    "label": presentation["label"],
                    "description": presentation["description"],
                    "category": presentation["category"],
                    "state": "review_stage_available" if targets else "waiting_for_contract",
                    "execution_state": execution_state,
                    "reason": reason,
                    "requirements": {
                        "exact_target": True,
                        "immutable_artifact": True,
                        "exact_recipient": command_id.startswith("outreach."),
                        "prior_review": True,
                        "prior_approval": True,
                        "typed_confirmation": str(definition["confirmation"]),
                        "maximum_items": 1,
                        "shared_locks": sorted(_REQUIRED_LOCKS),
                        "caller_controlled_paths": False,
                        "caller_controlled_flags": False,
                        "caller_controlled_content": False,
                    },
                    "targets": targets,
                    "targets_returned": len(targets),
                    "targets_total": len(by_command[command_id]),
                    "truncated": len(by_command[command_id]) > _REVIEW_TARGET_LIMIT,
                }
            )
        return {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "lanes": lanes,
            "maximum_targets_per_lane": _REVIEW_TARGET_LIMIT,
        }

    def get_review_target_detail(self, target_id: str) -> dict[str, Any]:
        """Return selected private review detail; never included in overview."""
        if not _SAFE_TARGET_ID.fullmatch(target_id):
            raise NotFoundError("review target not found")
        records, _ = self._review_target_records()
        for target in records:
            if target["target_id"] != target_id:
                continue
            snapshot = target["_snapshot"]
            detail = {
                "target_id": target["target_id"],
                "command_id": target["command_id"],
                "target_type": target["target_type"],
                "label": target["label"],
                "detail": target["detail"],
                "artifact_sha256": target["artifact_sha256"],
                "job_id": snapshot.get("job_id"),
                "channel": snapshot.get("channel"),
                "recipient": None,
                "draft_text": None,
                "content_binding": (
                    "The artifact hash is recomputed before review, approval, and execution."
                ),
                "maximum_items": 1,
                "review_confirmation_phrase": _REVIEW_CONFIRMATION,
            }
            sensitive = target.get("_sensitive_detail")
            if isinstance(sensitive, dict):
                detail["recipient"] = sensitive.get("recipient")
                detail["subject"] = sensitive.get("subject")
                detail["draft_text"] = sensitive.get("draft_text")
                detail["context"] = sensitive.get("context")
            return detail
        raise NotFoundError("review target not found")

    def list_reviews(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        self._expire_reviews()
        limit = min(max(int(limit), 1), _REVIEW_ITEM_LIMIT)
        offset = max(int(offset), 0)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operator_reviews WHERE user_id = ?
                ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """,
                (self.settings.user_id, limit, offset),
            ).fetchall()
        return [self._review_dto(dict(row)) for row in rows]

    def get_review(self, review_id: str) -> dict[str, Any]:
        if not _SAFE_REVIEW_ID.fullmatch(review_id):
            raise NotFoundError("operator review not found")
        self._expire_reviews()
        row = self._review_row(review_id)
        review = self._review_dto(row)
        with self.db.connect() as connection:
            events = connection.execute(
                """
                SELECT from_state, to_state, actor_scope, confirmation_valid,
                       target_sha256, created_at
                FROM operator_review_events
                WHERE review_id = ? AND user_id = ?
                ORDER BY created_at ASC
                """,
                (review_id, self.settings.user_id),
            ).fetchall()
        review["events"] = [
            {
                "from_state": event["from_state"],
                "to_state": event["to_state"],
                "actor_scope": event["actor_scope"],
                "confirmation_valid": bool(event["confirmation_valid"]),
                "target_sha256": event["target_sha256"],
                "created_at": event["created_at"],
            }
            for event in events
        ]
        return review

    def get_review_detail(
        self, review_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        review = self.get_review(review_id)
        row = self._review_row(review_id)
        review["reviewed_subject"] = row["reviewed_subject"]
        review["reviewed_text"] = row["reviewed_text"]
        target = self.get_review_target_detail(review["target_id"])
        if target["command_id"] != review["command_id"]:
            raise ConflictError("review target command binding changed")
        return review, target

    def update_review_content(
        self,
        review_id: str,
        *,
        reviewed_subject: Any,
        reviewed_text: Any,
        confirmation: str,
        requested_scope: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if requested_scope not in {"local", "web"}:
            raise ValidationError("requested_scope must be local or web")
        if confirmation != _CONTENT_UPDATE_CONFIRMATION:
            raise ValidationError("confirmation phrase does not match content update")
        self._expire_reviews()
        row = self._review_row(review_id)
        if row["state"] not in {"pending", "reviewed", "approved"}:
            raise ConflictError("review content cannot be changed in its current state")
        try:
            target = self._resolve_current_review_target(
                row["command_id"], row["target_id"]
            )
        except (NotFoundError, ValidationError):
            self._mark_review_stale(row, requested_scope=requested_scope)
            raise ConflictError("the selected target is no longer current")
        if target["artifact_sha256"] != row["source_artifact_sha256"]:
            self._mark_review_stale(row, requested_scope=requested_scope)
            raise ConflictError("the immutable source changed before content update")
        subject, text_value = self._review_content_for_target(
            target,
            reviewed_subject=reviewed_subject,
            reviewed_text=reviewed_text,
        )
        if (
            subject == row["reviewed_subject"]
            and text_value == row["reviewed_text"]
        ):
            return self.get_review_detail(review_id)
        artifact_sha = _canonical_binding_sha(
            {
                "source_artifact_sha256": row["source_artifact_sha256"],
                "reviewed_subject": subject,
                "reviewed_text": text_value,
            }
        )
        now = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(hours=_REVIEW_TTL_HOURS)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE operator_reviews
                SET state = 'pending', reviewed_subject = ?, reviewed_text = ?,
                    reviewed_subject_sha256 = ?, reviewed_text_sha256 = ?,
                    artifact_sha256 = ?, reviewed_at = NULL, approved_at = NULL,
                    execution_artifact_json = '{}', expires_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND state = ?
                """,
                (
                    subject,
                    text_value,
                    hashlib.sha256(subject.encode("utf-8")).hexdigest()
                    if subject
                    else "",
                    hashlib.sha256(text_value.encode("utf-8")).hexdigest()
                    if text_value
                    else "",
                    artifact_sha,
                    expires_at,
                    now,
                    review_id,
                    self.settings.user_id,
                    row["state"],
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("review state changed concurrently")
            self._insert_review_event(
                connection,
                review_id=review_id,
                from_state=row["state"],
                to_state="pending",
                actor_scope=requested_scope,
                confirmation_valid=True,
                target_sha256=artifact_sha,
                created_at=now,
            )
        return self.get_review_detail(review_id)

    def create_review(
        self,
        *,
        command_id: str,
        target_id: str,
        requested_scope: str,
        reviewed_subject: Any = None,
        reviewed_text: Any = None,
    ) -> dict[str, Any]:
        if command_id not in _REVIEW_GATED_COMMANDS:
            raise ValidationError("command_id is not a review-gated capability")
        if requested_scope not in {"local", "web"}:
            raise ValidationError("requested_scope must be local or web")
        if not _SAFE_TARGET_ID.fullmatch(target_id):
            raise ValidationError("target_id is not a projected operator target")
        self._expire_reviews()
        target = self._resolve_current_review_target(command_id, target_id)
        subject, text_value = self._review_content_for_target(
            target,
            reviewed_subject=reviewed_subject,
            reviewed_text=reviewed_text,
        )
        review_artifact_sha = _canonical_binding_sha(
            {
                "source_artifact_sha256": target["artifact_sha256"],
                "reviewed_subject": subject,
                "reviewed_text": text_value,
            }
        )
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM operator_reviews
                WHERE user_id = ? AND command_id = ? AND target_id = ?
                  AND state IN ('pending', 'reviewed', 'approved')
                ORDER BY created_at DESC LIMIT 1
                """,
                (self.settings.user_id, command_id, target_id),
            ).fetchone()
        if existing:
            if (
                existing["reviewed_subject"] != subject
                or existing["reviewed_text"] != text_value
            ):
                raise ConflictError(
                    "an active review exists; update its exact content explicitly"
                )
            return self._review_dto(dict(existing))

        review_id = new_id("review")
        now = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(hours=_REVIEW_TTL_HOURS)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        snapshot = target["_snapshot"]
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO operator_reviews (
                        id, user_id, command_id, target_id, target_type,
                        target_label, target_snapshot_json, source_artifact_sha256,
                        artifact_sha256, reviewed_subject, reviewed_text,
                        reviewed_subject_sha256, reviewed_text_sha256,
                        state, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        review_id,
                        self.settings.user_id,
                        command_id,
                        target_id,
                        target["target_type"],
                        target["label"],
                        json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                        target["artifact_sha256"],
                        review_artifact_sha,
                        subject,
                        text_value,
                        hashlib.sha256(subject.encode("utf-8")).hexdigest()
                        if subject
                        else "",
                        hashlib.sha256(text_value.encode("utf-8")).hexdigest()
                        if text_value
                        else "",
                        expires_at,
                        now,
                        now,
                    ),
                )
                self._insert_review_event(
                    connection,
                    review_id=review_id,
                    from_state="none",
                    to_state="pending",
                    actor_scope=requested_scope,
                    confirmation_valid=False,
                    target_sha256=review_artifact_sha,
                    created_at=now,
                )
        except sqlite3.IntegrityError as error:
            # A concurrent request may have staged the same target after the
            # optimistic lookup above. The database is the arbiter: return the
            # identical winner, or require an explicit content update.
            with self.db.connect() as connection:
                winner = connection.execute(
                    """
                    SELECT * FROM operator_reviews
                    WHERE user_id = ? AND command_id = ? AND target_id = ?
                      AND state IN ('pending', 'reviewed', 'approved')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (self.settings.user_id, command_id, target_id),
                ).fetchone()
            if winner:
                current = dict(winner)
                if (
                    current["reviewed_subject"] == subject
                    and current["reviewed_text"] == text_value
                ):
                    return self._review_dto(current)
                raise ConflictError(
                    "an active review exists; update its exact content explicitly"
                ) from error
            raise ConflictError("review staging conflicted with another request") from error
        return self.get_review(review_id)

    def transition_review(
        self,
        review_id: str,
        *,
        transition: str,
        confirmation: str,
        requested_scope: str,
    ) -> dict[str, Any]:
        if requested_scope not in {"local", "web"}:
            raise ValidationError("requested_scope must be local or web")
        transitions = {
            "review": ({"pending"}, "reviewed", _REVIEW_CONFIRMATION, "reviewed_at"),
            "approve": ({"reviewed"}, "approved", _APPROVAL_CONFIRMATION, "approved_at"),
            "revoke": (
                {"pending", "reviewed", "approved"},
                "revoked",
                _REVOCATION_CONFIRMATION,
                "revoked_at",
            ),
        }
        if transition not in transitions:
            raise ValidationError("review transition is not allowlisted")
        allowed_states, next_state, phrase, timestamp_field = transitions[transition]
        if confirmation != phrase:
            raise ValidationError("confirmation phrase does not match the review transition")
        self._expire_reviews()
        row = self._review_row(review_id)
        if row["state"] not in allowed_states:
            raise ConflictError(
                f"review cannot transition from {row['state']} to {next_state}"
            )
        if transition != "revoke":
            try:
                current = self._resolve_current_review_target(
                    row["command_id"], row["target_id"]
                )
            except (NotFoundError, ValidationError):
                self._mark_review_stale(row, requested_scope=requested_scope)
                raise ConflictError("the selected target is no longer current")
            if current["artifact_sha256"] != row["source_artifact_sha256"]:
                self._mark_review_stale(row, requested_scope=requested_scope)
                raise ConflictError("the selected artifact changed after review staging")
        execution_artifact: dict[str, Any] | None = None
        if transition == "approve" and row["command_id"] == "outreach.linkedin.send":
            execution_artifact = self._materialize_linkedin_approval(row, current)
        now = utc_now()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE operator_reviews
                SET state = ?, {timestamp_field} = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND state = ?
                """,
                (
                    next_state,
                    now,
                    now,
                    review_id,
                    self.settings.user_id,
                    row["state"],
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("review state changed concurrently")
            if execution_artifact is not None:
                connection.execute(
                    """
                    UPDATE operator_reviews SET execution_artifact_json = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        json.dumps(
                            execution_artifact,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        review_id,
                        self.settings.user_id,
                    ),
                )
            self._insert_review_event(
                connection,
                review_id=review_id,
                from_state=row["state"],
                to_state=next_state,
                actor_scope=requested_scope,
                confirmation_valid=True,
                target_sha256=row["artifact_sha256"],
                created_at=now,
            )
        return self.get_review(review_id)

    def _review_target_records(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        records: list[dict[str, Any]] = []
        reasons: dict[str, list[str]] = {
            command_id: [] for command_id in _REVIEW_GATED_COMMANDS
        }
        reasons["application.assist.fill_to_review"].append(
            _APPLY_ASSIST_BLOCKED_REASON
        )
        locks = self.adapter.lock_states()
        if not self._all_locks_free(locks):
            message = (
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return records, {
                command_id: [message] for command_id in _REVIEW_GATED_COMMANDS
            }

        try:
            records.append(self._nightly_review_target())
        except (OSError, ValueError, ValidationError):
            reasons["nightly.run"].append(
                "production release attestation or safe nightly surface is unavailable"
            )

        try:
            queue_root, rows = self._current_queue_rows()
            for row in rows[:_REVIEW_TARGET_LIMIT]:
                job_id = _numeric_job_id(row)
                if job_id is None:
                    continue
                folder = self._queue_job_folder(row, queue_root=queue_root)
                artifact_sha = _application_artifact_fingerprint(
                    row, folder, maximum_bytes=64 * 1024 * 1024
                )
                company = _safe_display_text(row.get("company"), maximum=80)
                role = _safe_display_text(
                    row.get("role_title") or row.get("role"), maximum=100
                )
                label = f"#{job_id} · {company or 'Company'} · {role or 'Role'}"
                common_snapshot = {
                    "job_id": job_id,
                    "artifact_sha256": artifact_sha,
                    "maximum_items": 1,
                }
                for command_id, terminal_status in (
                    ("application.status.applied", "applied"),
                    ("application.status.closed", "closed"),
                ):
                    records.append(
                        self._make_review_target(
                            command_id=command_id,
                            target_type="application_status_archive",
                            label=label,
                            detail=(
                                f"One job to {terminal_status} through the archive-first, artifact-preserving transition."
                            ),
                            artifact_sha256=artifact_sha,
                            snapshot={
                                **common_snapshot,
                                "terminal_status": terminal_status,
                            },
                        )
                    )
        except (OSError, ValueError, ValidationError, json.JSONDecodeError):
            for command_id in (
                "application.assist.fill_to_review",
                "application.status.applied",
                "application.status.closed",
            ):
                reasons[command_id].append(
                    "current apply queue targets are unavailable or unsafe"
                )
        outreach_records, outreach_reasons = self._outreach_review_target_records()
        records.extend(outreach_records)
        for command_id, values in outreach_reasons.items():
            reasons[command_id].extend(values)
        return records, reasons

    def _nightly_review_target(self) -> dict[str, Any]:
        if not self.settings.attestation_path:
            raise ValidationError("production attestation is not configured")
        if self.settings.attestation_path.is_symlink():
            raise ValueError("attestation cannot be a symlink")
        attestation = self.settings.attestation_path.resolve(strict=True)
        if not attestation.is_file():
            raise ValueError("attestation must be a file")
        attestation_sha = hashlib.sha256(
            _read_bounded_bytes(attestation, limit=2 * 1024 * 1024)
        ).hexdigest()
        _, root = self._resume_surface("discovery/scripts/nightly_prompt.py")
        script = _strict_allowlisted_path(
            root, root / "discovery" / "scripts" / "nightly_prompt.py", expect="file"
        )
        script_sha = hashlib.sha256(
            _read_bounded_bytes(script, limit=2 * 1024 * 1024)
        ).hexdigest()
        pipeline_script = _strict_allowlisted_path(
            root,
            root / "discovery" / "scripts" / "run_nightly_pipeline.py",
            expect="file",
        )
        pipeline_script_sha = hashlib.sha256(
            _read_bounded_bytes(pipeline_script, limit=4 * 1024 * 1024)
        ).hexdigest()
        pipeline_args = " ".join(_SAFE_NIGHTLY_PIPELINE_ARGS)
        wrapper_argv = [
            "--force",
            "--require-production-attestation",
            "--production-attestation",
            "<server-owned-attestation>",
            "--pipeline-args",
            pipeline_args,
        ]
        binding = {
            "release_attestation_sha256": attestation_sha,
            "nightly_prompt_sha256": script_sha,
            "run_nightly_pipeline_sha256": pipeline_script_sha,
            "wrapper_argv": wrapper_argv,
            "pipeline_args": list(_SAFE_NIGHTLY_PIPELINE_ARGS),
            "pipeline_args_string": pipeline_args,
            "delivery_flags": {
                "execute_sends": False,
                "track_2_send_linkedin": False,
                "execute_linkedin_followups": False,
            },
            "maximum_runs": 1,
        }
        artifact_sha = _canonical_binding_sha(binding)
        target = self._make_review_target(
            command_id="nightly.run",
            target_type="safe_nightly_release",
            label=f"Safe nightly · release {attestation_sha[:12]}",
            detail=(
                "One prepare/generate nightly run. Email sends, LinkedIn invites, "
                "and LinkedIn follow-ups are omitted."
            ),
            artifact_sha256=artifact_sha,
            snapshot=binding,
        )
        target["_execution_binding"] = {
            **binding,
            "attestation": str(attestation),
        }
        return target

    def _outreach_review_target_records(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        records: list[dict[str, Any]] = []
        reasons = {
            "outreach.linkedin.send": [],
            "outreach.email.send": [],
        }
        if not self.settings.resumegen_root or not self.settings.outreach_root:
            message = "installed ResumeGenerator and Outreach roots are required"
            return records, {key: [message] for key in reasons}
        verified = self.adapter.verified_run_projections(limit=1)
        if not verified:
            message = "no fully verified exact nightly run is available"
            return records, {key: [message] for key in reasons}
        latest = verified[-1]
        try:
            evidence = latest["evidence"]["daily_manifest"]
            relative = Path(str(evidence["path"]))
            manifest_path = _strict_allowlisted_path(
                self.settings.resumegen_root,
                self.settings.resumegen_root / relative,
                expect="file",
            )
            manifest_content = _read_bounded_bytes(
                manifest_path, limit=20 * 1024 * 1024
            )
            if hashlib.sha256(manifest_content).hexdigest() != evidence.get("sha256"):
                raise ValueError("verified manifest source hash changed")
            manifest = json.loads(manifest_content.decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("run_id") != latest["run_id"]:
                raise ValueError("verified manifest run binding changed")
        except (KeyError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            message = "exact nightly manifest is unavailable, changed, or unsafe"
            return records, {key: [message] for key in reasons}

        run_id = str(latest["run_id"])
        outreach_root = self.settings.outreach_root
        linkedin_records = 0
        email_records = 0

        try:
            allowed_phase_paths = {
                _resolve_exact_artifact(outreach_root, pointer)
                for pointer in manifest.get("track_2_phase_artifacts", [])
                if isinstance(pointer, str)
            }
            artifacts_root = _strict_allowlisted_path(
                outreach_root, outreach_root / "artifacts", expect="directory"
            )
            phase_results = manifest.get("track_2_phase_results", [])
            if not isinstance(phase_results, list):
                raise ValueError("Track 2 phase results are not typed")
            for phase in phase_results:
                if not isinstance(phase, dict) or phase.get("phase") != "5_send_linkedin_invites":
                    continue
                runs = phase.get("runs")
                if not isinstance(runs, list):
                    continue
                for run in runs:
                    if not isinstance(run, dict) or run.get("send_artifact"):
                        continue
                    if str(run.get("status") or "").casefold() not in {
                        "planned",
                        "ready",
                        "review_ready",
                        "dry_run",
                        "prepared",
                        "not_executed",
                    }:
                        continue
                    pipeline_path = _resolve_exact_artifact(
                        outreach_root, run.get("pipeline_artifact")
                    )
                    if not pipeline_path.is_relative_to(artifacts_root):
                        raise ValueError("invite pipeline artifact is outside Outreach/artifacts")
                    if pipeline_path not in allowed_phase_paths:
                        raise ValueError("invite pipeline artifact is not bound by the exact manifest")
                    source_content = _read_bounded_bytes(
                        pipeline_path, limit=20 * 1024 * 1024
                    )
                    source_sha = hashlib.sha256(source_content).hexdigest()
                    source = json.loads(source_content.decode("utf-8"))
                    if not isinstance(source, dict):
                        raise ValueError("invite pipeline artifact is not an object")
                    run_company = _bounded_private_text(run.get("company"), maximum=180)
                    source_company = _bounded_private_text(source.get("company"), maximum=180)
                    if run_company.casefold() != source_company.casefold():
                        raise ValueError("invite company binding changed")
                    minimum = _strict_number(run.get("effective_min_score"), minimum=0, maximum=100)
                    results = source.get("results")
                    if not isinstance(results, list):
                        raise ValueError("invite pipeline results are not typed")
                    for row_index, row in enumerate(results):
                        if not isinstance(row, dict):
                            continue
                        note_qc = row.get("note_qc")
                        if not isinstance(note_qc, dict) or str(
                            note_qc.get("verdict") or ""
                        ).casefold() != "send":
                            continue
                        if row.get("target_company_match") is not True:
                            continue
                        score = _strict_number(row.get("score"), minimum=0, maximum=100)
                        if score < minimum:
                            continue
                        linkedin_url = _canonical_linkedin_url(row.get("linkedin_url"))
                        name = _bounded_private_text(row.get("name"), maximum=180)
                        note = _bounded_private_text(row.get("note"), maximum=2_000)
                        recipient_ref = hashlib.sha256(
                            linkedin_url.encode("utf-8")
                        ).hexdigest()
                        binding = {
                            "run_id": run_id,
                            "source_sha256": source_sha,
                            "company": source_company,
                            "linkedin_url": linkedin_url,
                            "name": name,
                            "note": note,
                            "score": score,
                            "effective_min_score": minimum,
                            "recipient_ref": recipient_ref,
                            "maximum_items": 1,
                            "action": "invite",
                            "source_row_index": row_index,
                        }
                        artifact_sha = _canonical_binding_sha(binding)
                        target = self._make_review_target(
                            command_id="outreach.linkedin.send",
                            target_type="linkedin_invite",
                            label=f"LinkedIn invite · {source_company}",
                            detail="One exact recipient and connection note from the verified run.",
                            artifact_sha256=artifact_sha,
                            snapshot={
                                "run_id": run_id,
                                "source_sha256": source_sha,
                                "recipient_ref": recipient_ref,
                                "channel": "linkedin_invite",
                                "maximum_items": 1,
                            },
                        )
                        target["_sensitive_detail"] = {
                            "recipient": f"{name} · {linkedin_url}",
                            "draft_text": note,
                            "context": f"{source_company} · score {score:g} · minimum {minimum:g}",
                        }
                        target["_execution_binding"] = binding
                        target["_execution_binding"].update(
                            {
                                "action": "invite",
                                "source_artifact": str(pipeline_path),
                                "source_row_index": row_index,
                            }
                        )
                        records.append(target)
                        linkedin_records += 1
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            reasons["outreach.linkedin.send"].append(
                "verified invite artifact pointers are absent, delivered, changed, or unsafe"
            )

        for pointer in manifest.get("linkedin_followup_draft_artifacts", []):
            try:
                source_path = _resolve_exact_artifact(outreach_root, pointer)
                source_content = _read_bounded_bytes(
                    source_path, limit=20 * 1024 * 1024
                )
                source_sha = hashlib.sha256(source_content).hexdigest()
                source = json.loads(source_content.decode("utf-8"))
                if not isinstance(source, dict) or not isinstance(source.get("results"), list):
                    raise ValueError("follow-up artifact is not typed")
                for row_index, row in enumerate(source["results"]):
                    if not isinstance(row, dict):
                        continue
                    source_status = str(row.get("source_status") or "").casefold()
                    if source_status in {"", "failed", "error", "skipped", "sent"}:
                        continue
                    contact_id = _bounded_private_text(row.get("contact_id"), maximum=180)
                    thread_id = _bounded_private_text(row.get("thread_id"), maximum=240)
                    if thread_id.casefold().startswith("synthetic:"):
                        raise ValueError("follow-up thread_id is synthetic")
                    draft_kind = _bounded_private_text(row.get("draft_kind"), maximum=80)
                    latest_message = _bounded_private_text(
                        row.get("latest_message"), maximum=8_000
                    )
                    message_window_text = _bounded_private_json(
                        row.get("message_window"), maximum=8_000
                    )
                    linkedin_url = _canonical_linkedin_url(row.get("linkedin_url"))
                    name = _bounded_private_text(row.get("name"), maximum=180)
                    company = _bounded_private_text(row.get("company"), maximum=180)
                    draft = _bounded_private_text(
                        row.get("draft_message"), maximum=8_000
                    )
                    identity = "|".join(
                        (contact_id, thread_id, draft_kind, latest_message)
                    )
                    recipient_ref = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    binding = {
                        "run_id": run_id,
                        "source_sha256": source_sha,
                        "contact_id": contact_id,
                        "thread_id": thread_id,
                        "linkedin_url": linkedin_url,
                        "name": name,
                        "company": company,
                        "draft_message": draft,
                        "send_recommendation": row.get("send_recommendation"),
                        "communication_recommendation": row.get(
                            "communication_recommendation"
                        ),
                        "latest_message": latest_message,
                        "message_window": row.get("message_window"),
                        "draft_kind": draft_kind,
                        "source_status": row.get("source_status"),
                        "recipient_ref": recipient_ref,
                        "maximum_items": 1,
                        "action": "followup",
                        "source_row_index": row_index,
                    }
                    artifact_sha = _canonical_binding_sha(binding)
                    target = self._make_review_target(
                        command_id="outreach.linkedin.send",
                        target_type="linkedin_followup",
                        label=f"LinkedIn follow-up · {company}",
                        detail="One exact recipient, thread context, and follow-up draft.",
                        artifact_sha256=artifact_sha,
                        snapshot={
                            "run_id": run_id,
                            "source_sha256": source_sha,
                            "recipient_ref": recipient_ref,
                            "channel": "linkedin_followup",
                            "maximum_items": 1,
                        },
                    )
                    target["_sensitive_detail"] = {
                        "recipient": f"{name} · {linkedin_url}",
                        "draft_text": draft,
                        "context": (
                            f"Latest inbound:\n{latest_message}\n\n"
                            f"Message window:\n{message_window_text}"
                        ),
                    }
                    target["_execution_binding"] = binding
                    target["_execution_binding"].update(
                        {
                            "action": "followup",
                            "source_artifact": str(source_path),
                            "source_row_index": row_index,
                        }
                    )
                    records.append(target)
                    linkedin_records += 1
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                reasons["outreach.linkedin.send"].append(
                    "one exact follow-up artifact was rejected as changed or unsafe"
                )

        for pointer in manifest.get("track_2_email_draft_artifacts", []):
            try:
                source_path = _resolve_exact_artifact(outreach_root, pointer)
                source_content = _read_bounded_bytes(
                    source_path, limit=20 * 1024 * 1024
                )
                source_sha = hashlib.sha256(source_content).hexdigest()
                source = json.loads(source_content.decode("utf-8"))
                if not isinstance(source, dict) or not isinstance(source.get("results"), list):
                    raise ValueError("email artifact is not typed")
                for row in source["results"]:
                    if not isinstance(row, dict):
                        continue
                    organization_id = _bounded_private_text(
                        row.get("organization_id"), maximum=180
                    )
                    contact_id = _bounded_private_text(
                        row.get("contact_id"), maximum=180
                    )
                    email = _canonical_email(row.get("email"))
                    subject = _bounded_private_text(row.get("subject"), maximum=998)
                    body = _bounded_private_text(row.get("body"), maximum=20_000)
                    company = _bounded_private_text(row.get("company"), maximum=180)
                    name = _bounded_private_text(row.get("name"), maximum=180)
                    recipient_ref = hashlib.sha256(
                        f"{organization_id}|{contact_id}|{email}".encode("utf-8")
                    ).hexdigest()
                    binding = {
                        "run_id": run_id,
                        "source_sha256": source_sha,
                        "organization_id": organization_id,
                        "contact_id": contact_id,
                        "email": email,
                        "subject": subject,
                        "body": body,
                        "company": company,
                        "name": name,
                        "cadence_action": row.get("cadence_action"),
                        "communication_review": row.get("communication_review"),
                        "craft_review": row.get("craft_review"),
                        "recipient_ref": recipient_ref,
                        "maximum_items": 1,
                    }
                    artifact_sha = _canonical_binding_sha(binding)
                    target = self._make_review_target(
                        command_id="outreach.email.send",
                        target_type="email_draft",
                        label=f"Email draft · {company}",
                        detail="One exact recipient, subject, and body from the verified run.",
                        artifact_sha256=artifact_sha,
                        snapshot={
                            "run_id": run_id,
                            "source_sha256": source_sha,
                            "recipient_ref": recipient_ref,
                            "channel": "email",
                            "maximum_items": 1,
                        },
                    )
                    target["_sensitive_detail"] = {
                        "recipient": f"{name} <{email}>",
                        "subject": subject,
                        "draft_text": body,
                        "context": str(row.get("cadence_action") or ""),
                    }
                    target["_execution_binding"] = binding
                    records.append(target)
                    email_records += 1
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                reasons["outreach.email.send"].append(
                    "one exact email draft artifact was rejected as changed or unsafe"
                )

        if linkedin_records == 0 and not reasons["outreach.linkedin.send"]:
            reasons["outreach.linkedin.send"].append(
                "the exact run contains no undelivered invite or follow-up target"
            )
        if email_records == 0 and not reasons["outreach.email.send"]:
            reasons["outreach.email.send"].append(
                "the exact run contains no reviewable email draft target"
            )
        return records, reasons

    @staticmethod
    def _make_review_target(
        *,
        command_id: str,
        target_type: str,
        label: str,
        detail: str,
        artifact_sha256: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "command_id": command_id,
                    "artifact_sha256": artifact_sha256,
                    "snapshot": snapshot,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "target_id": f"target_{digest[:24]}",
            "command_id": command_id,
            "target_type": target_type,
            "label": _safe_display_text(label, maximum=180),
            "detail": _safe_display_text(detail, maximum=220),
            "artifact_sha256": artifact_sha256,
            "bounded_limit": 1,
            "_snapshot": snapshot,
        }

    @staticmethod
    def _public_review_target(target: dict[str, Any]) -> dict[str, Any]:
        snapshot = target["_snapshot"]
        return {
            key: value for key, value in target.items() if not key.startswith("_")
        } | {
            "job_id": snapshot.get("job_id"),
            "channel": snapshot.get("channel"),
            "recipient_ref": snapshot.get("recipient_ref"),
            "review_confirmation_phrase": _REVIEW_CONFIRMATION,
        }

    def _resolve_current_review_target(
        self, command_id: str, target_id: str
    ) -> dict[str, Any]:
        records, _ = self._review_target_records()
        for target in records:
            if target["command_id"] == command_id and target["target_id"] == target_id:
                return target
        raise NotFoundError("review target is not present in the current exact projection")

    @staticmethod
    def _review_content_for_target(
        target: dict[str, Any], *, reviewed_subject: Any, reviewed_text: Any
    ) -> tuple[str, str]:
        command_id = target["command_id"]
        sensitive = target.get("_sensitive_detail")
        if command_id not in {
            "outreach.linkedin.send",
            "outreach.email.send",
        }:
            if (
                reviewed_subject is not None
                and reviewed_subject != ""
            ) or (reviewed_text is not None and reviewed_text != ""):
                raise ValidationError(
                    "this review target does not accept editable message content"
                )
            return "", ""
        if not isinstance(sensitive, dict):
            raise ValidationError("exact private draft detail is unavailable")
        text_value = (
            sensitive.get("draft_text")
            if reviewed_text is None
            else reviewed_text
        )
        try:
            text_value = _bounded_private_text(text_value, maximum=20_000)
        except ValueError as error:
            raise ValidationError("reviewed message text is empty or too large") from error
        if command_id == "outreach.email.send":
            subject = (
                sensitive.get("subject")
                if reviewed_subject is None
                else reviewed_subject
            )
            try:
                subject = _bounded_private_text(subject, maximum=998)
            except ValueError as error:
                raise ValidationError("reviewed email subject is empty or too large") from error
            return subject, text_value
        if reviewed_subject is not None and reviewed_subject != "":
            raise ValidationError("LinkedIn review content does not accept a subject")
        return "", text_value

    def _review_row(self, review_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operator_reviews WHERE id = ? AND user_id = ?",
                (review_id, self.settings.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError("operator review not found")
        return dict(row)

    def _expire_reviews(self) -> None:
        now = utc_now()
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operator_reviews
                WHERE user_id = ? AND state IN ('pending', 'reviewed', 'approved')
                  AND expires_at <= ?
                """,
                (self.settings.user_id, now),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                connection.execute(
                    """
                    UPDATE operator_reviews SET state = 'expired', updated_at = ?
                    WHERE id = ? AND user_id = ? AND state = ?
                    """,
                    (now, row["id"], self.settings.user_id, row["state"]),
                )
                self._insert_review_event(
                    connection,
                    review_id=row["id"],
                    from_state=row["state"],
                    to_state="expired",
                    actor_scope="system",
                    confirmation_valid=False,
                    target_sha256=row["artifact_sha256"],
                    created_at=now,
                )

    def _mark_review_stale(
        self, row: dict[str, Any], *, requested_scope: str
    ) -> None:
        now = utc_now()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE operator_reviews SET state = 'stale', updated_at = ?
                WHERE id = ? AND user_id = ? AND state = ?
                """,
                (now, row["id"], self.settings.user_id, row["state"]),
            )
            if cursor.rowcount:
                self._insert_review_event(
                    connection,
                    review_id=row["id"],
                    from_state=row["state"],
                    to_state="stale",
                    actor_scope=requested_scope,
                    confirmation_valid=False,
                    target_sha256=row["artifact_sha256"],
                    created_at=now,
                )

    def _approved_review(
        self, command_id: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        # Execution must not depend on a prior list/detail call having noticed
        # expiration. Expire under the database write transaction, then re-read
        # the selected row before accepting its state.
        self._expire_reviews()
        review = self._review_row(str(parameters.get("review_id") or ""))
        if review["command_id"] != command_id:
            raise ConflictError("approved review does not match the selected command")
        if review["target_id"] != parameters.get("target_id"):
            raise ConflictError("approved review does not match the selected target")
        if review["state"] != "approved":
            raise ConflictError("selected target requires a current approved review")
        current = self._resolve_current_review_target(command_id, review["target_id"])
        if current["artifact_sha256"] != review["source_artifact_sha256"]:
            self._mark_review_stale(review, requested_scope="system")
            raise ConflictError("approved artifact changed and must be reviewed again")
        expected_binding = _canonical_binding_sha(
            {
                "source_artifact_sha256": review["source_artifact_sha256"],
                "reviewed_subject": review["reviewed_subject"],
                "reviewed_text": review["reviewed_text"],
            }
        )
        if expected_binding != review["artifact_sha256"]:
            self._mark_review_stale(review, requested_scope="system")
            raise ConflictError("approved review content binding is invalid")
        return review

    def _consume_review_before_execution(
        self,
        command_id: str,
        parameters: dict[str, Any],
        *,
        actor_scope: str,
    ) -> dict[str, Any]:
        """Atomically consume approval before a future external process spawn.

        Delivery uncertainty must never reopen this review. Reconciliation or a
        newly staged target is required after any spawn attempt whose outcome is
        not authoritatively recorded.
        """
        review = self._approved_review(command_id, parameters)
        return self._consume_review_row(review, actor_scope=actor_scope)

    def _consume_review_row(
        self, review: dict[str, Any], *, actor_scope: str
    ) -> dict[str, Any]:
        expected_binding = _canonical_binding_sha(
            {
                "source_artifact_sha256": review["source_artifact_sha256"],
                "reviewed_subject": review["reviewed_subject"],
                "reviewed_text": review["reviewed_text"],
            }
        )
        if expected_binding != review["artifact_sha256"]:
            raise ConflictError("review content binding changed before consumption")
        now = utc_now()
        consumed = False
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE operator_reviews
                SET state = 'consumed', consumed_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND state = 'approved'
                  AND expires_at > ?
                """,
                (now, now, review["id"], self.settings.user_id, now),
            )
            consumed = cursor.rowcount == 1
            if consumed:
                self._insert_review_event(
                    connection,
                    review_id=review["id"],
                    from_state="approved",
                    to_state="consumed",
                    actor_scope=actor_scope,
                    confirmation_valid=True,
                    target_sha256=review["artifact_sha256"],
                    created_at=now,
                )
        if not consumed:
            # Materialize a due expiration event after the failed
            # compare-and-set. The approval is never consumed or executed.
            self._expire_reviews()
            raise ConflictError("approved review is expired or already consumed")
        return self._review_row(review["id"])

    @staticmethod
    def _insert_review_event(
        connection: Any,
        *,
        review_id: str,
        from_state: str,
        to_state: str,
        actor_scope: str,
        confirmation_valid: bool,
        target_sha256: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operator_review_events (
                id, user_id, review_id, from_state, to_state, actor_scope,
                confirmation_valid, target_sha256, created_at
            ) SELECT ?, user_id, ?, ?, ?, ?, ?, ?, ?
              FROM operator_reviews WHERE id = ?
            """,
            (
                new_id("reviewevent"),
                review_id,
                from_state,
                to_state,
                actor_scope,
                int(confirmation_valid),
                target_sha256,
                created_at,
                review_id,
            ),
        )

    @staticmethod
    def _review_dto(row: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = json.loads(row.get("target_snapshot_json") or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        try:
            execution_artifact = json.loads(
                row.get("execution_artifact_json") or "{}"
            )
        except json.JSONDecodeError:
            execution_artifact = {}
        return {
            "id": row["id"],
            "command_id": row["command_id"],
            "label": _COMMAND_PRESENTATION.get(row["command_id"], {}).get(
                "label", row["command_id"]
            ),
            "target_id": row["target_id"],
            "target_type": row["target_type"],
            "target_label": row["target_label"],
            "source_artifact_sha256": row.get("source_artifact_sha256", ""),
            "artifact_sha256": row["artifact_sha256"],
            "reviewed_subject_sha256": row.get("reviewed_subject_sha256", ""),
            "reviewed_text_sha256": row.get("reviewed_text_sha256", ""),
            "state": row["state"],
            "job_id": snapshot.get("job_id"),
            "channel": snapshot.get("channel"),
            "recipient_ref": snapshot.get("recipient_ref"),
            "bounded_limit": 1,
            "execution_prepared": bool(
                isinstance(execution_artifact, dict) and execution_artifact
            ),
            "review_confirmation_phrase": _REVIEW_CONFIRMATION,
            "approval_confirmation_phrase": _APPROVAL_CONFIRMATION,
            "revocation_confirmation_phrase": _REVOCATION_CONFIRMATION,
            "action_confirmation_phrase": _COMMAND_CATALOG[row["command_id"]][
                "confirmation"
            ],
            "expires_at": row["expires_at"],
            "reviewed_at": row["reviewed_at"],
            "approved_at": row["approved_at"],
            "revoked_at": row["revoked_at"],
            "consumed_at": row["consumed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
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
        if command_id in _REVIEW_GATED_COMMANDS:
            self._approved_review(command_id, safe_parameters)

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
        elif command_id == "nightly.run":
            worker = threading.Thread(
                target=self._execute_reviewed_nightly,
                args=(job_id, safe_parameters),
                daemon=True,
                name=f"operator-nightly-{job_id[-8:]}",
            )
            try:
                worker.start()
            except RuntimeError:
                self._finish_job(
                    job_id, status="failed", result_code="worker_start_failed"
                )
        elif command_id in {
            "application.assist.fill_to_review",
            "outreach.email.send",
            "outreach.linkedin.send",
        }:
            worker = threading.Thread(
                target=(
                    self._execute_reviewed_apply_assist
                    if command_id == "application.assist.fill_to_review"
                    else self._execute_reviewed_email
                    if command_id == "outreach.email.send"
                    else self._execute_reviewed_linkedin
                ),
                args=(job_id, safe_parameters),
                daemon=True,
                name=f"operator-reviewed-{command_id}-{job_id[-8:]}",
            )
            try:
                worker.start()
            except RuntimeError:
                self._finish_job(
                    job_id, status="failed", result_code="worker_start_failed"
                )
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
    def _validate_parameters(command_id: str, parameters: Any) -> dict[str, Any]:
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
        if expected == {"review_id", "target_id"}:
            if set(parameters) != expected:
                raise ValidationError(
                    "parameters must contain exactly review_id and target_id"
                )
            review_id = parameters["review_id"]
            target_id = parameters["target_id"]
            if not isinstance(review_id, str) or not _SAFE_REVIEW_ID.fullmatch(
                review_id
            ):
                raise ValidationError("review_id is not a valid operator review id")
            if not isinstance(target_id, str) or not _SAFE_TARGET_ID.fullmatch(
                target_id
            ):
                raise ValidationError("target_id is not a projected operator target")
            return {"review_id": review_id, "target_id": target_id}
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
        parameters: dict[str, Any],
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
        parameters: dict[str, Any],
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
                if command_id in _LIFECYCLE_COMMANDS:
                    if not self._run_reviewed_production_preflight(
                        job_id,
                        locks=locks,
                    ):
                        return
                    try:
                        review = self._review_row(str(parameters["review_id"]))
                        self._consume_review_row(
                            review, actor_scope="operator-executor"
                        )
                    except (KeyError, ConflictError, NotFoundError):
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="approved_review_unavailable",
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
                        else "lifecycle_busy"
                        if command_id in _LIFECYCLE_COMMANDS
                        and completed.returncode == 75
                        else "lifecycle_validation_failed"
                        if command_id in _LIFECYCLE_COMMANDS
                        and completed.returncode == 2
                        else "lifecycle_rolled_back"
                        if command_id in _LIFECYCLE_COMMANDS
                        and completed.returncode == 1
                        else "fixed_command_failed"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _production_preflight_completed(
        self,
    ) -> subprocess.CompletedProcess[bytes]:
        argv, cwd = self._preflight_argv()
        return subprocess.run(
            argv,
            cwd=cwd,
            env={
                key: value
                for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
                if (value := os.environ.get(key))
            },
            capture_output=True,
            text=False,
            timeout=120,
            check=False,
            shell=False,
        )

    def _run_reviewed_production_preflight(
        self,
        job_id: str,
        *,
        locks: dict[str, str],
    ) -> bool:
        """Revalidate the attested upstream release before approval consumption."""
        try:
            completed = self._production_preflight_completed()
        except subprocess.TimeoutExpired as error:
            self._finish_job(
                job_id,
                status="blocked",
                result_code="reviewed_action_preflight_failed",
                returncode=124,
                stdout=error.stdout or b"",
                stderr=error.stderr or b"",
                lock_snapshot=locks,
            )
            return False
        except (OSError, ValidationError):
            self._finish_job(
                job_id,
                status="blocked",
                result_code="reviewed_action_preflight_failed",
                lock_snapshot=locks,
            )
            return False
        self._update_job(
            job_id,
            {
                "preflight_returncode": completed.returncode,
                "preflight_stdout_sha256": (
                    hashlib.sha256(completed.stdout).hexdigest()
                    if completed.stdout
                    else ""
                ),
                "preflight_stderr_sha256": (
                    hashlib.sha256(completed.stderr).hexdigest()
                    if completed.stderr
                    else ""
                ),
            },
        )
        if completed.returncode != 0:
            self._finish_job(
                job_id,
                status="blocked",
                result_code="reviewed_action_preflight_failed",
                returncode=completed.returncode,
                lock_snapshot=locks,
            )
            return False
        return True

    def _execute_reviewed_nightly(
        self, job_id: str, parameters: dict[str, Any]
    ) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        try:
            with lock_path.open("r+b") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self._finish_job(
                        job_id, status="blocked", result_code="adapter_lock_busy"
                    )
                    return
                try:
                    locks = self.adapter.lock_states()
                    external_locks = {
                        key: value
                        for key, value in locks.items()
                        if key != "adapter_mutation"
                    }
                    if not all(
                        value == "free" for value in external_locks.values()
                    ):
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="engine_locks_not_free",
                            lock_snapshot=locks,
                        )
                        return
                    review = self._review_row(str(parameters.get("review_id") or ""))
                    target = self._nightly_review_target()
                    if (
                        review["state"] != "approved"
                        or review["command_id"] != "nightly.run"
                        or review["target_id"] != parameters.get("target_id")
                        or review["target_id"] != target["target_id"]
                        or review["source_artifact_sha256"] != target["artifact_sha256"]
                    ):
                        raise ConflictError("safe nightly review changed")
                    argv, cwd, timeout = self._fixed_nightly_argv()
                    preflight_argv, preflight_cwd = self._preflight_argv()
                    try:
                        preflight = subprocess.run(
                            preflight_argv,
                            cwd=preflight_cwd,
                            env=self._fixed_environment("nightly.run"),
                            capture_output=True,
                            text=False,
                            timeout=120,
                            check=False,
                            shell=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="reviewed_nightly_preflight_failed",
                            lock_snapshot=locks,
                        )
                        return
                    self._update_job(
                        job_id,
                        {
                            "preflight_returncode": preflight.returncode,
                            "preflight_stdout_sha256": (
                                hashlib.sha256(preflight.stdout).hexdigest()
                                if preflight.stdout
                                else ""
                            ),
                            "preflight_stderr_sha256": (
                                hashlib.sha256(preflight.stderr).hexdigest()
                                if preflight.stderr
                                else ""
                            ),
                        },
                    )
                    if preflight.returncode != 0:
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="reviewed_nightly_preflight_failed",
                            returncode=preflight.returncode,
                            lock_snapshot=locks,
                        )
                        return
                    self._consume_review_row(
                        review, actor_scope="operator-nightly-executor"
                    )
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
                finally:
                    # nightly_prompt owns scheduler/pipeline/adapter lock order.
                    # Holding this lock across spawn would deadlock its child.
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError, ValidationError, ConflictError, NotFoundError):
            self._finish_job(
                job_id,
                status="blocked",
                result_code="approved_review_unavailable",
            )
            return

        environment = self._fixed_environment("nightly.run")
        if self.settings.runtime_dir:
            environment["RECRUITING_ENGINE_RUNTIME_DIR"] = str(
                self.settings.runtime_dir
            )
        if self.settings.resumegen_root:
            environment["RECRUITING_ENGINE_RESUME_ROOT"] = str(
                self.settings.resumegen_root
            )
        if self.settings.outreach_root:
            environment["RECRUITING_ENGINE_OUTREACH_ROOT"] = str(
                self.settings.outreach_root
            )
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=environment,
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
                result_code="reviewed_nightly_timeout",
                returncode=124,
                stdout=error.stdout or b"",
                stderr=error.stderr or b"",
            )
            return
        except OSError:
            self._finish_job(
                job_id,
                status="failed",
                result_code="reviewed_nightly_spawn_failed",
            )
            return
        self._finish_job(
            job_id,
            status="completed" if completed.returncode == 0 else "failed",
            result_code=(
                "reviewed_nightly_completed"
                if completed.returncode == 0
                else "reviewed_nightly_failed"
            ),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _fixed_nightly_argv(self) -> tuple[list[str], Path, int]:
        if not self.settings.allow_reviewed_actions:
            raise ValidationError("reviewed actions are disabled")
        if not self.settings.attestation_path:
            raise ValidationError("production attestation is not configured")
        python, root = self._resume_surface("discovery/scripts/nightly_prompt.py")
        if self.settings.attestation_path.is_symlink():
            raise ValidationError("production attestation cannot be a symlink")
        attestation = self.settings.attestation_path.resolve(strict=True)
        if not attestation.is_file():
            raise ValidationError("production attestation is unavailable")
        return (
            [
                str(python),
                "discovery/scripts/nightly_prompt.py",
                "--force",
                "--require-production-attestation",
                "--production-attestation",
                str(attestation),
                "--pipeline-args",
                " ".join(_SAFE_NIGHTLY_PIPELINE_ARGS),
            ],
            root,
            _COMMAND_TIMEOUTS["nightly.run"],
        )

    def _execute_reviewed_apply_assist(
        self, job_id: str, parameters: dict[str, Any]
    ) -> None:
        # The installed remote runner has no tool-level interception point for
        # the final Submit action. Keep this executor fail-closed even if a
        # future capability projection is accidentally loosened; re-enabling
        # requires replacing this guard with an enforceable browser policy and
        # an authoritative, hash-bound terminal receipt.
        self._finish_job(
            job_id,
            status="blocked",
            result_code="application_assist_submit_guard_unavailable",
        )
        return
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id, status="blocked", result_code="adapter_lock_busy"
                )
                return
            try:
                locks = self.adapter.lock_states()
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
                    review = self._review_row(str(parameters.get("review_id") or ""))
                    if (
                        review["state"] != "approved"
                        or review["command_id"] != "application.assist.fill_to_review"
                        or review["target_id"] != parameters.get("target_id")
                    ):
                        raise ConflictError("apply-assist review is not approved")
                    snapshot = json.loads(review["target_snapshot_json"])
                    job_number = snapshot.get("job_id")
                    if isinstance(job_number, bool) or not isinstance(job_number, int):
                        raise ValidationError("apply-assist review job is invalid")
                    queue_root, _ = self._current_queue_rows()
                    queue_row = self._current_queue_job(job_number)
                    folder = self._queue_job_folder(queue_row, queue_root=queue_root)
                    current_sha = _application_artifact_fingerprint(
                        queue_row, folder, maximum_bytes=64 * 1024 * 1024
                    )
                    if current_sha != review["source_artifact_sha256"]:
                        raise ValidationError("approved application material changed")
                    if not self._apply_assist_is_attested():
                        raise ValidationError("apply_assist is not attested")
                    for key in ("RTRVR_API_KEY", "RTRVR_DEVICE_ID"):
                        if not self._configured_value(self.settings.resumegen_root, key):
                            raise ValidationError(f"{key} is unavailable")
                    python, root = self._resume_surface(
                        "apply_assist/build_apply_task.py"
                    )
                    self._resume_surface("apply_assist/rtrvr_apply_runner.py")
                    profile = _strict_allowlisted_path(
                        root,
                        root / "apply_assist" / "profile_answers.local.json",
                        expect="file",
                    )
                    action_dir = self._prepare_review_action_dir(
                        review["id"], job_id
                    )
                    task_dir = action_dir / "task"
                    result_dir = action_dir / "results"
                    task_dir.mkdir(mode=0o700)
                    result_dir.mkdir(mode=0o700)
                    build_argv = [
                        str(python),
                        "apply_assist/build_apply_task.py",
                        "--job-id",
                        str(job_number),
                        "--queue-json",
                        "apps/Apply queues/current_apply_queue/priority_order.json",
                        "--answers-profile",
                        str(profile),
                        "--out-dir",
                        str(task_dir),
                    ]
                    build = subprocess.run(
                        build_argv,
                        cwd=root,
                        env=self._fixed_environment(
                            "application.assist.fill_to_review"
                        ),
                        capture_output=True,
                        text=False,
                        timeout=120,
                        check=False,
                        shell=False,
                    )
                    self._update_job(
                        job_id,
                        {
                            "preflight_returncode": build.returncode,
                            "preflight_stdout_sha256": (
                                hashlib.sha256(build.stdout).hexdigest()
                                if build.stdout
                                else ""
                            ),
                            "preflight_stderr_sha256": (
                                hashlib.sha256(build.stderr).hexdigest()
                                if build.stderr
                                else ""
                            ),
                        },
                    )
                    if build.returncode != 0:
                        self._finish_job(
                            job_id,
                            status="blocked",
                            result_code="apply_assist_task_build_failed",
                            returncode=build.returncode,
                            lock_snapshot=locks,
                        )
                        return
                    task_files = [
                        path
                        for path in task_dir.iterdir()
                        if path.is_file()
                        and not path.is_symlink()
                        and path.suffix == ".json"
                    ]
                    if len(task_files) != 1:
                        raise ValidationError(
                            "apply-assist build did not produce one exact task"
                        )
                    task_path = task_files[0].resolve(strict=True)
                    task = _read_json_object(task_path)
                    metadata = task.get("metadata")
                    guardrails = task.get("guardrails")
                    if (
                        not isinstance(metadata, dict)
                        or not isinstance(guardrails, dict)
                        or str(metadata.get("queue_job_id"))
                        != str(job_number)
                        or guardrails.get("stop_before_submit") is not True
                    ):
                        raise ValidationError("apply-assist task guard changed")
                    live_argv = [
                        str(python),
                        "apply_assist/rtrvr_apply_runner.py",
                        str(task_path),
                        "--live",
                        "--mode",
                        "mcp",
                        "--max-steps",
                        "20",
                        "--timeout-seconds",
                        "180",
                        "--results-dir",
                        str(result_dir),
                    ]
                    if not self._run_reviewed_production_preflight(
                        job_id,
                        locks=locks,
                    ):
                        return
                    self._consume_review_row(
                        review, actor_scope="operator-apply-assist-executor"
                    )
                    argv_hash = hashlib.sha256(
                        b"\0".join(
                            part.encode("utf-8")
                            for part in [*build_argv, "--then--", *live_argv]
                        )
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
                    live = subprocess.run(
                        live_argv,
                        cwd=root,
                        env=self._fixed_environment(
                            "application.assist.fill_to_review"
                        ),
                        capture_output=True,
                        text=False,
                        timeout=_COMMAND_TIMEOUTS[
                            "application.assist.fill_to_review"
                        ],
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="apply_assist_timeout",
                        returncode=124,
                        stdout=error.stdout or b"",
                        stderr=error.stderr or b"",
                    )
                    return
                except (OSError, ValueError, ValidationError, ConflictError, NotFoundError):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="approved_review_unavailable",
                        lock_snapshot=locks,
                    )
                    return
                self._finish_job(
                    job_id,
                    status="completed" if live.returncode == 0 else "failed",
                    result_code=(
                        "apply_assist_run_completed"
                        if live.returncode == 0
                        else "apply_assist_failed"
                    ),
                    returncode=live.returncode,
                    stdout=live.stdout,
                    stderr=live.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _materialize_linkedin_approval(
        self, review: dict[str, Any], target: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.settings.allow_reviewed_actions:
            raise ConflictError("reviewed actions are disabled by local runtime policy")
        binding = target.get("_execution_binding")
        if not isinstance(binding, dict):
            raise ConflictError("LinkedIn execution binding is unavailable")
        action = str(binding.get("action") or "")
        source_index = binding.get("source_row_index")
        if action not in {"invite", "followup"} or isinstance(source_index, bool) or not isinstance(source_index, int):
            raise ConflictError("LinkedIn action binding is invalid")
        if action == "followup":
            thread_id = str(binding.get("thread_id") or "").strip()
            if not thread_id or thread_id.casefold().startswith("synthetic:"):
                raise ConflictError("LinkedIn follow-up requires an exact thread_id")
        if not self.settings.outreach_root:
            raise ConflictError("Outreach root is unavailable")
        source_path = _resolve_exact_artifact(
            self.settings.outreach_root, binding.get("source_artifact")
        )
        source_sha = hashlib.sha256(
            _read_bounded_bytes(source_path, limit=20 * 1024 * 1024)
        ).hexdigest()
        if source_sha != binding.get("source_sha256"):
            raise ConflictError("LinkedIn source changed before approval")

        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ConflictError("companion mutation lock is busy") from error
            try:
                locks = self.adapter.lock_states()
                external = {
                    key: value
                    for key, value in locks.items()
                    if key != "adapter_mutation"
                }
                if not all(value == "free" for value in external.values()):
                    raise ConflictError("an upstream engine lock is busy")
                python, root = self._outreach_surface()
                _strict_allowlisted_path(
                    root,
                    root / "src" / "outreach" / "reviewed_linkedin.py",
                    expect="file",
                )
                try:
                    preflight = self._production_preflight_completed()
                except (OSError, ValidationError, subprocess.TimeoutExpired) as error:
                    raise ConflictError(
                        "production release could not be revalidated before "
                        "LinkedIn approval materialization"
                    ) from error
                if preflight.returncode != 0:
                    raise ConflictError(
                        "production release changed before LinkedIn approval "
                        "materialization"
                    )
                approval_root = self.settings.user_dir / "reviewed-actions"
                approval_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                review_root = approval_root / review["id"]
                review_root.mkdir(exist_ok=True, mode=0o700)
                leaf = review_root / f"linkedin-{review['artifact_sha256'][:20]}"
                leaf.mkdir(mode=0o700)
                leaf = _strict_allowlisted_path(
                    approval_root, leaf, expect="directory"
                )
                message_path = leaf / "outgoing-message.txt"
                proposal_path = leaf / "proposal.json"
                approval_path = leaf / "approval.json"
                _write_private_text(message_path, review["reviewed_text"])
                base = [
                    str(python),
                    "-m",
                    "outreach.reviewed_linkedin",
                ]
                proposal_argv = [
                    *base,
                    "preview",
                    "--action",
                    action,
                    "--source-artifact",
                    str(source_path),
                    "--row-index",
                    str(source_index),
                    "--outgoing-message-file",
                    str(message_path),
                    "--output",
                    str(proposal_path),
                ]
                environment = self._fixed_environment("outreach.linkedin.send")
                preview = subprocess.run(
                    proposal_argv,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=False,
                    timeout=120,
                    check=False,
                    shell=False,
                )
                if preview.returncode != 0:
                    raise ConflictError("LinkedIn proposal preview failed")
                proposal = _read_json_object(
                    _strict_allowlisted_path(
                        leaf, proposal_path, expect="file"
                    )
                )
                proposal_sha = str(proposal.get("proposal_sha256") or "")
                if not re.fullmatch(r"[a-f0-9]{64}", proposal_sha):
                    raise ConflictError("LinkedIn proposal hash is invalid")
                approve_argv = [
                    *base,
                    "approve",
                    "--action",
                    action,
                    "--source-artifact",
                    str(source_path),
                    "--row-index",
                    str(source_index),
                    "--outgoing-message-file",
                    str(message_path),
                    "--expect-proposal-sha256",
                    proposal_sha,
                    "--approved-by",
                    "local-owner",
                    "--approval-file",
                    str(approval_path),
                ]
                approved = subprocess.run(
                    approve_argv,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=False,
                    timeout=120,
                    check=False,
                    shell=False,
                )
                if approved.returncode != 0:
                    raise ConflictError("LinkedIn approval materialization failed")
                approval = _read_json_object(
                    _strict_allowlisted_path(
                        leaf, approval_path, expect="file"
                    )
                )
                approval_sha = str(approval.get("approval_sha256") or "")
                if not re.fullmatch(r"[a-f0-9]{64}", approval_sha):
                    raise ConflictError("LinkedIn approval hash is invalid")
                return {
                    "kind": "reviewed_linkedin_approval",
                    "action": action,
                    "approval_path": approval_path.relative_to(
                        self.settings.user_dir.resolve(strict=True)
                    ).as_posix(),
                    "approval_sha256": approval_sha,
                    "proposal_sha256": proposal_sha,
                    "proposal_argv_sha256": hashlib.sha256(
                        b"\0".join(
                            part.encode("utf-8") for part in proposal_argv
                        )
                    ).hexdigest(),
                    "approve_argv_sha256": hashlib.sha256(
                        b"\0".join(part.encode("utf-8") for part in approve_argv)
                    ).hexdigest(),
                    "preview_stdout_sha256": hashlib.sha256(
                        preview.stdout
                    ).hexdigest()
                    if preview.stdout
                    else "",
                    "approve_stdout_sha256": hashlib.sha256(
                        approved.stdout
                    ).hexdigest()
                    if approved.stdout
                    else "",
                }
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _execute_reviewed_linkedin(
        self, job_id: str, parameters: dict[str, Any]
    ) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id, status="blocked", result_code="adapter_lock_busy"
                )
                return
            try:
                locks = self.adapter.lock_states()
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
                    review = self._review_row(str(parameters.get("review_id") or ""))
                    if (
                        review["state"] != "approved"
                        or review["command_id"] != "outreach.linkedin.send"
                        or review["target_id"] != parameters.get("target_id")
                    ):
                        raise ConflictError("LinkedIn review is not approved")
                    target = next(
                        (
                            item
                            for item in self._outreach_review_target_records()[0]
                            if item["command_id"] == "outreach.linkedin.send"
                            and item["target_id"] == review["target_id"]
                        ),
                        None,
                    )
                    if (
                        not target
                        or target["artifact_sha256"]
                        != review["source_artifact_sha256"]
                    ):
                        raise ValidationError("approved LinkedIn source changed")
                    binding = target.get("_execution_binding")
                    if not isinstance(binding, dict):
                        raise ValidationError("LinkedIn execution binding is unavailable")
                    action = str(binding.get("action") or "")
                    if action not in {"invite", "followup"}:
                        raise ValidationError("LinkedIn action is invalid")
                    if action == "followup":
                        thread_id = str(binding.get("thread_id") or "").strip()
                        if not thread_id or thread_id.casefold().startswith("synthetic:"):
                            raise ValidationError(
                                "LinkedIn follow-up thread_id is invalid"
                            )
                    try:
                        execution = json.loads(
                            review.get("execution_artifact_json") or "{}"
                        )
                    except json.JSONDecodeError as error:
                        raise ValidationError(
                            "LinkedIn approval metadata is invalid"
                        ) from error
                    if (
                        not isinstance(execution, dict)
                        or execution.get("kind") != "reviewed_linkedin_approval"
                        or execution.get("action") != action
                    ):
                        raise ValidationError(
                            "LinkedIn approval was not materialized"
                        )
                    approval_sha = str(execution.get("approval_sha256") or "")
                    if not re.fullmatch(r"[a-f0-9]{64}", approval_sha):
                        raise ValidationError("LinkedIn approval SHA is invalid")
                    approval_path = _strict_allowlisted_path(
                        self.settings.user_dir,
                        self.settings.user_dir
                        / str(execution.get("approval_path") or ""),
                        expect="file",
                    )
                    python, root = self._outreach_surface()
                    _strict_allowlisted_path(
                        root,
                        root / "src" / "outreach" / "reviewed_linkedin.py",
                        expect="file",
                    )
                    receipt_path = approval_path.parent / f"receipt-{job_id}.json"
                    if receipt_path.exists() or receipt_path.is_symlink():
                        raise ValidationError("LinkedIn receipt path is not fresh")
                    argv = [
                        str(python),
                        "-m",
                        "outreach.reviewed_linkedin",
                        "execute",
                        "--approval-file",
                        str(approval_path),
                        "--expect-approval-sha256",
                        approval_sha,
                        "--receipt-file",
                        str(receipt_path),
                        "--execute",
                    ]
                    if not self._run_reviewed_production_preflight(
                        job_id,
                        locks=locks,
                    ):
                        return
                    self._consume_review_row(
                        review, actor_scope="operator-linkedin-executor"
                    )
                    self._update_job(
                        job_id,
                        {
                            "status": "running",
                            "started_at": utc_now(),
                            "argv_sha256": hashlib.sha256(
                                b"\0".join(part.encode("utf-8") for part in argv)
                            ).hexdigest(),
                            "lock_snapshot_json": json.dumps(locks, sort_keys=True),
                        },
                    )
                    completed = subprocess.run(
                        argv,
                        cwd=root,
                        env=self._fixed_environment("outreach.linkedin.send"),
                        capture_output=True,
                        text=False,
                        timeout=_COMMAND_TIMEOUTS["outreach.linkedin.send"],
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="reviewed_linkedin_reconciliation_required",
                        returncode=124,
                        stdout=error.stdout or b"",
                        stderr=error.stderr or b"",
                    )
                    return
                except (
                    OSError,
                    ValueError,
                    ValidationError,
                    ConflictError,
                    NotFoundError,
                ):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="approved_review_unavailable",
                        lock_snapshot=locks,
                    )
                    return
                receipt: dict[str, Any] = {}
                try:
                    receipt = _read_json_object(
                        _strict_allowlisted_path(
                            approval_path.parent, receipt_path, expect="file"
                        )
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    receipt = {}
                exact_completed = (
                    completed.returncode == 0
                    and receipt.get("status") == "execution_completed"
                    and receipt.get("reconciliation_required") is False
                    and receipt.get("approval_sha256") == approval_sha
                    and receipt.get("proposal_sha256")
                    == execution.get("proposal_sha256")
                )
                reconciliation = (
                    not exact_completed
                    and (
                        receipt.get("reconciliation_required") is True
                        or receipt.get("status")
                        in {"execution_blocked", "execution_unknown"}
                        or completed.returncode == 0
                    )
                )
                self._finish_job(
                    job_id,
                    status="completed" if exact_completed else "failed",
                    result_code=(
                        "reviewed_linkedin_completed"
                        if exact_completed
                        else "reviewed_linkedin_reconciliation_required"
                        if reconciliation
                        else "reviewed_linkedin_failed"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _execute_reviewed_email(
        self, job_id: str, parameters: dict[str, Any]
    ) -> None:
        lock_path = self.settings.adapter_mutation_lock_path
        with lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._finish_job(
                    job_id, status="blocked", result_code="adapter_lock_busy"
                )
                return
            try:
                locks = self.adapter.lock_states()
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
                    review = self._review_row(str(parameters.get("review_id") or ""))
                    if (
                        review["state"] != "approved"
                        or review["command_id"] != "outreach.email.send"
                        or review["target_id"] != parameters.get("target_id")
                    ):
                        raise ConflictError("email review is not approved")
                    target = next(
                        (
                            item
                            for item in self._outreach_review_target_records()[0]
                            if item["command_id"] == "outreach.email.send"
                            and item["target_id"] == review["target_id"]
                        ),
                        None,
                    )
                    if (
                        not target
                        or target["artifact_sha256"]
                        != review["source_artifact_sha256"]
                    ):
                        raise ValidationError("approved email source changed")
                    for key in (
                        "SMTP_HOST",
                        "SMTP_FROM_EMAIL",
                        "SMTP_USERNAME",
                        "SMTP_PASSWORD",
                    ):
                        if not self._configured_value(self.settings.outreach_root, key):
                            raise ValidationError(f"{key} is unavailable")
                    binding = target.get("_execution_binding")
                    if not isinstance(binding, dict):
                        raise ValidationError("email execution binding is unavailable")
                    python, root = self._outreach_surface()
                    action_dir = self._prepare_review_action_dir(
                        review["id"], job_id
                    )
                    draft_path = action_dir / "approved-email.json"
                    approval_path = action_dir / "approved-email.csv"
                    draft = {
                        key: value
                        for key, value in binding.items()
                        if key
                        not in {
                            "run_id",
                            "source_sha256",
                            "recipient_ref",
                            "maximum_items",
                        }
                    }
                    draft["subject"] = review["reviewed_subject"]
                    draft["body"] = review["reviewed_text"]
                    draft["body_length"] = len(review["reviewed_text"])
                    _write_private_json(draft_path, {"results": [draft]})
                    _write_private_csv(
                        approval_path,
                        fieldnames=[
                            "organization_id",
                            "contact_id",
                            "email",
                            "subject",
                            "message",
                            "review_artifact",
                            "user_decision",
                            "user_reason",
                            "user_edit",
                        ],
                        row={
                            "organization_id": draft["organization_id"],
                            "contact_id": draft["contact_id"],
                            "email": draft["email"],
                            "subject": draft["subject"],
                            "message": draft["body"],
                            "review_artifact": str(draft_path),
                            "user_decision": "approved",
                            "user_reason": "exact companion review",
                            "user_edit": draft["body"],
                        },
                    )
                    argv = [
                        str(python),
                        "main.py",
                        "send-track-2-emails",
                        "--draft-artifact",
                        str(draft_path),
                        "--approval-csv",
                        str(approval_path),
                        "--workspace",
                        "workspace",
                        "--limit",
                        "1",
                        "--execute",
                    ]
                    if not self._run_reviewed_production_preflight(
                        job_id,
                        locks=locks,
                    ):
                        return
                    self._consume_review_row(
                        review, actor_scope="operator-email-executor"
                    )
                    self._update_job(
                        job_id,
                        {
                            "status": "running",
                            "started_at": utc_now(),
                            "argv_sha256": hashlib.sha256(
                                b"\0".join(part.encode("utf-8") for part in argv)
                            ).hexdigest(),
                            "lock_snapshot_json": json.dumps(locks, sort_keys=True),
                        },
                    )
                    completed = subprocess.run(
                        argv,
                        cwd=root,
                        env=self._fixed_environment("outreach.email.send"),
                        capture_output=True,
                        text=False,
                        timeout=_COMMAND_TIMEOUTS["outreach.email.send"],
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    self._finish_job(
                        job_id,
                        status="failed",
                        result_code="reviewed_email_reconciliation_required",
                        returncode=124,
                        stdout=error.stdout or b"",
                        stderr=error.stderr or b"",
                    )
                    return
                except (OSError, ValueError, ValidationError, ConflictError, NotFoundError):
                    self._finish_job(
                        job_id,
                        status="blocked",
                        result_code="approved_review_unavailable",
                        lock_snapshot=locks,
                    )
                    return
                evidence = self._reviewed_email_delivery_evidence(
                    completed,
                    outreach_root=root,
                    draft_path=draft_path,
                    expected_draft=draft,
                )
                self._finish_job(
                    job_id,
                    status="completed" if evidence == "sent" else "failed",
                    result_code=(
                        "reviewed_email_completed"
                        if evidence == "sent"
                        else "reviewed_email_not_sent"
                        if evidence == "not_sent"
                        else "reviewed_email_reconciliation_required"
                    ),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _reviewed_email_delivery_evidence(
        completed: subprocess.CompletedProcess[bytes],
        *,
        outreach_root: Path,
        draft_path: Path,
        expected_draft: dict[str, Any],
    ) -> str:
        """Return sent only for one exact, artifact-proven SMTP delivery.

        The upstream CLI may exit zero when a row is held or otherwise not
        delivered. Its bounded result artifact, rather than its return code,
        is therefore the authority for the cockpit completion state.
        """
        stdout = completed.stdout or b""
        if len(stdout) > 64 * 1024:
            return "unknown"
        try:
            lines = stdout.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError:
            return "unknown"
        pointers = [
            line.removeprefix("Artifact: ").strip()
            for line in lines
            if line.startswith("Artifact: ")
        ]
        if len(pointers) != 1 or not pointers[0]:
            return "unknown"
        try:
            artifact_root = _strict_allowlisted_path(
                outreach_root,
                outreach_root / "artifacts",
                expect="directory",
            )
            result_path = _resolve_exact_artifact(outreach_root, pointers[0])
            if not result_path.is_relative_to(artifact_root):
                return "unknown"
            result = _read_json_object(result_path)
            source_pointer = result.get("source_artifact")
            if not isinstance(source_pointer, str) or not source_pointer.strip():
                return "unknown"
            source_candidate = Path(source_pointer).expanduser()
            if not source_candidate.is_absolute():
                source_candidate = outreach_root / source_candidate
            source_path = _strict_allowlisted_path(
                draft_path.parent, source_candidate, expect="file"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return "unknown"
        if source_path != draft_path.resolve(strict=True):
            return "unknown"
        if result.get("execute") is not True:
            return "unknown"
        results = result.get("results")
        if not isinstance(results, list) or len(results) != 1:
            return "unknown"
        delivered = results[0]
        if not isinstance(delivered, dict):
            return "unknown"
        for key in ("eligible", "held", "sent"):
            value = result.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return "unknown"
        immutable_fields = ("organization_id", "contact_id", "email", "subject", "body")
        if any(delivered.get(key) != expected_draft.get(key) for key in immutable_fields):
            return "unknown"
        exact_sent = (
            completed.returncode == 0
            and result["eligible"] == 1
            and result["held"] == 0
            and result["sent"] == 1
            and delivered.get("delivery_status") == "sent"
        )
        if exact_sent:
            return "sent"
        # A schema-valid artifact with no sent row is authoritative evidence
        # that this bounded attempt did not deliver. Any contradictory count or
        # row remains unknown and requires reconciliation.
        if (
            result["sent"] == 0
            and delivered.get("delivery_status") != "sent"
        ):
            return "not_sent"
        return "unknown"

    def _prepare_review_action_dir(self, review_id: str, job_id: str) -> Path:
        if not _SAFE_REVIEW_ID.fullmatch(review_id) or not re.fullmatch(
            r"opjob_[a-f0-9]{32}", job_id
        ):
            raise ValidationError("review action identifiers are invalid")
        root = self.settings.user_dir / "reviewed-actions"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ValidationError("review action root cannot be a symlink")
        review_dir = root / review_id
        review_dir.mkdir(exist_ok=True, mode=0o700)
        if review_dir.is_symlink():
            raise ValidationError("review action directory cannot be a symlink")
        action_dir = review_dir / job_id
        action_dir.mkdir(mode=0o700)
        return _strict_allowlisted_path(root, action_dir, expect="directory")

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

    def _lifecycle_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.allow_reviewed_actions:
            reasons.append("reviewed actions are disabled by local runtime policy")
            return False, reasons
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            self._resume_surface("discovery/scripts/transition_application.py")
        except (OSError, ValueError, ValidationError):
            reasons.append(
                "the attested artifact-preserving lifecycle script is unavailable"
            )
        reasons.extend(self._reviewed_preflight_reasons())
        return not reasons, reasons

    def _nightly_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.allow_reviewed_actions:
            reasons.append("reviewed actions are disabled by local runtime policy")
            return False, reasons
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            self._nightly_review_target()
        except (OSError, ValueError, ValidationError):
            reasons.append(
                "the reviewed safe-nightly script or release attestation changed"
            )
        return not reasons, reasons

    def _email_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.allow_reviewed_actions:
            reasons.append("reviewed actions are disabled by local runtime policy")
            return False, reasons
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            self._outreach_surface()
        except (OSError, ValueError, ValidationError):
            reasons.append("fixed Outreach email command surface is unavailable")
            return False, reasons
        for key in (
            "SMTP_HOST",
            "SMTP_FROM_EMAIL",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
        ):
            if not self._configured_value(self.settings.outreach_root, key):
                reasons.append(f"{key} is not configured")
        reasons.extend(self._reviewed_preflight_reasons())
        return not reasons, reasons

    def _linkedin_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.allow_reviewed_actions:
            reasons.append("reviewed actions are disabled by local runtime policy")
            return False, reasons
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            _, root = self._outreach_surface()
            _strict_allowlisted_path(
                root,
                root / "src" / "outreach" / "reviewed_linkedin.py",
                expect="file",
            )
            _strict_allowlisted_path(
                root, root / "workspace", expect="directory"
            )
        except (OSError, ValueError, ValidationError):
            reasons.append("reviewed LinkedIn executor or tracking workspace is unavailable")
        reasons.extend(self._reviewed_preflight_reasons())
        return not reasons, reasons

    def _apply_assist_availability(
        self, adapter_status: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.allow_reviewed_actions:
            reasons.append("reviewed actions are disabled by local runtime policy")
            return False, reasons
        if not self._all_locks_free(adapter_status.get("locks", {})):
            reasons.append(
                "all scheduler, pipeline, workbook, queue, and adapter locks must be free"
            )
            return False, reasons
        try:
            self._resume_surface("apply_assist/build_apply_task.py")
            self._resume_surface("apply_assist/rtrvr_apply_runner.py")
            if not self.settings.resumegen_root:
                raise ValidationError("resume root is not configured")
            _strict_allowlisted_path(
                self.settings.resumegen_root,
                self.settings.resumegen_root
                / "apply_assist"
                / "profile_answers.local.json",
                expect="file",
            )
        except (OSError, ValueError, ValidationError):
            reasons.append(
                "apply_assist scripts or profile_answers.local.json are unavailable"
            )
        if not self._apply_assist_is_attested():
            reasons.append("apply_assist is not covered by the production attestation")
        for key in ("RTRVR_API_KEY", "RTRVR_DEVICE_ID"):
            if not self._configured_value(self.settings.resumegen_root, key):
                reasons.append(f"{key} is not configured")
        reasons.extend(self._reviewed_preflight_reasons())
        reasons.append(_APPLY_ASSIST_BLOCKED_REASON)
        return not reasons, reasons

    def _reviewed_preflight_reasons(self) -> list[str]:
        try:
            self._preflight_argv()
        except (OSError, ValidationError):
            return [
                "the fixed production-attestation preflight is unavailable or unsafe"
            ]
        return []

    def _apply_assist_is_attested(self) -> bool:
        if not self.settings.attestation_path:
            return False
        try:
            if self.settings.attestation_path.is_symlink():
                return False
            payload = _read_json_object(
                self.settings.attestation_path.resolve(strict=True)
            )
            paths = (
                payload.get("repositories", {})
                .get("resume_generator", {})
                .get("code_paths", [])
            )
            return isinstance(paths, list) and any(
                isinstance(value, str)
                and (value == "apply_assist" or value.startswith("apply_assist/"))
                for value in paths
            )
        except (OSError, ValueError, AttributeError, json.JSONDecodeError):
            return False

    @staticmethod
    def _configured_value(root: Path | None, key: str) -> str:
        current = os.environ.get(key, "").strip()
        if current:
            return current
        if not root:
            return ""
        try:
            env_path = _strict_allowlisted_path(root, root / ".env", expect="file")
            return _dotenv_value(env_path, key)
        except (OSError, ValueError, UnicodeError):
            return ""

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

        if command_id in _LIFECYCLE_COMMANDS:
            if not self.settings.allow_reviewed_actions:
                raise ValidationError("reviewed actions are disabled")
            review_id = parameters.get("review_id")
            target_id = parameters.get("target_id")
            if not isinstance(review_id, str) or not isinstance(target_id, str):
                raise ValidationError("approved review and exact target are required")
            review = self._review_row(review_id)
            if (
                review["state"] != "approved"
                or review["command_id"] != command_id
                or review["target_id"] != target_id
            ):
                raise ValidationError("lifecycle review is not current and approved")
            try:
                snapshot = json.loads(review["target_snapshot_json"])
            except json.JSONDecodeError as error:
                raise ValidationError("lifecycle review snapshot is invalid") from error
            if not isinstance(snapshot, dict):
                raise ValidationError("lifecycle review snapshot is invalid")
            job_id = snapshot.get("job_id")
            expected_terminal = (
                "applied"
                if command_id == "application.status.applied"
                else "closed"
            )
            if (
                isinstance(job_id, bool)
                or not isinstance(job_id, int)
                or snapshot.get("terminal_status") != expected_terminal
            ):
                raise ValidationError("lifecycle review target is invalid")
            queue_root, _ = self._current_queue_rows()
            queue_row = self._current_queue_job(job_id)
            folder = self._queue_job_folder(queue_row, queue_root=queue_root)
            current_sha = _application_artifact_fingerprint(
                queue_row, folder, maximum_bytes=64 * 1024 * 1024
            )
            if current_sha != review["source_artifact_sha256"]:
                raise ValidationError("approved lifecycle artifact changed")
            python, root = self._resume_surface(
                "discovery/scripts/transition_application.py"
            )
            upstream_status = "applied" if expected_terminal == "applied" else "not-applied"
            confirmation = (
                f"APPLY {job_id}" if expected_terminal == "applied" else f"CLOSE {job_id}"
            )
            return (
                [
                    str(python),
                    "discovery/scripts/transition_application.py",
                    "--id",
                    str(job_id),
                    "--status",
                    upstream_status,
                    "--confirm",
                    confirmation,
                    "--external-operator-lock",
                    "--json",
                ],
                root,
                timeout,
                success_code,
            )

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
        if command_id == "application.assist.fill_to_review":
            for key in ("RTRVR_API_KEY", "RTRVR_DEVICE_ID"):
                value = self._configured_value(self.settings.resumegen_root, key)
                if value:
                    environment[key] = value
        if command_id == "outreach.email.send":
            for key in (
                "SMTP_HOST",
                "SMTP_FROM_EMAIL",
                "SMTP_USERNAME",
                "SMTP_PASSWORD",
            ):
                value = self._configured_value(self.settings.outreach_root, key)
                if value:
                    environment[key] = value
        if command_id == "outreach.linkedin.send" and self.settings.outreach_root:
            environment["TRACKING_WORKSPACE_DIR"] = str(
                self.settings.outreach_root / "workspace"
            )
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
            "preflight_returncode": row.get("preflight_returncode"),
            "preflight_stdout_sha256": row.get("preflight_stdout_sha256", ""),
            "preflight_stderr_sha256": row.get("preflight_stderr_sha256", ""),
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


def _safe_operator_parameters(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    if set(parsed) == {"review_id", "target_id"}:
        review_id = parsed.get("review_id")
        target_id = parsed.get("target_id")
        if (
            isinstance(review_id, str)
            and _SAFE_REVIEW_ID.fullmatch(review_id)
            and isinstance(target_id, str)
            and _SAFE_TARGET_ID.fullmatch(target_id)
        ):
            return {"review_id": review_id, "target_id": target_id}
        return {}
    if set(parsed) not in (set(), {"job_id"}):
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
        "application_archived_applied": "The reviewed job was archived as applied with artifacts preserved.",
        "application_archived_closed": "The reviewed job was archived as not applied with artifacts preserved.",
        "approved_review_unavailable": "The approved exact-target review was missing, changed, or already consumed.",
        "lifecycle_busy": "The reviewed lifecycle transition found an upstream lock busy; the approval remains consumed and requires reconciliation.",
        "lifecycle_validation_failed": "The reviewed lifecycle transition failed upstream validation.",
        "lifecycle_rolled_back": "The reviewed lifecycle transition failed and the upstream transaction rolled back.",
        "reviewed_nightly_completed": "The reviewed prepare/generate nightly completed; delivery flags were omitted.",
        "reviewed_nightly_failed": "The reviewed safe nightly returned a failure status.",
        "reviewed_nightly_timeout": "The reviewed safe nightly reached its bounded timeout; approval remains consumed.",
        "reviewed_nightly_spawn_failed": "The reviewed safe nightly could not start; approval remains consumed.",
        "reviewed_nightly_preflight_failed": "Production preflight failed before approval consumption; no nightly process started.",
        "reviewed_action_preflight_failed": "Production attestation changed or could not be revalidated before approval consumption; no consequential action started.",
        "apply_assist_run_completed": "The reviewed apply-assist runner returned successfully. Inspect its browser/result state before the human-owned final Submit.",
        "apply_assist_task_build_failed": "The fixed apply-assist task build failed before approval consumption.",
        "application_assist_submit_guard_unavailable": "Live browser fill is blocked because the installed runner cannot technically intercept final Submit.",
        "apply_assist_timeout": "The reviewed apply-assist run timed out after approval consumption.",
        "apply_assist_failed": "The reviewed apply-assist runner returned a failure status.",
        "reviewed_email_completed": "One exact reviewed email completed the bounded SMTP command.",
        "reviewed_email_not_sent": "The exact reviewed email result artifact proves that no message was sent; stage a fresh review only after resolving the reported hold or failure.",
        "reviewed_email_reconciliation_required": "The reviewed email command did not prove exactly one SMTP delivery; approval remains consumed and reconciliation is required.",
        "reviewed_email_timeout": "The reviewed one-email command timed out after approval consumption.",
        "reviewed_email_failed": "The reviewed one-email command returned a failure status.",
        "reviewed_linkedin_completed": "One immutable reviewed LinkedIn action completed through the replay-protected executor.",
        "reviewed_linkedin_timeout": "The reviewed LinkedIn action timed out after approval consumption and requires reconciliation.",
        "reviewed_linkedin_failed": "The reviewed LinkedIn executor returned a failure status; approval remains consumed.",
        "reviewed_linkedin_reconciliation_required": "The reviewed LinkedIn executor did not prove exactly one send; approval remains consumed and reconciliation is required.",
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


def _application_artifact_fingerprint(
    row: dict[str, Any],
    folder: Path,
    *,
    maximum_bytes: int,
) -> str:
    """Fingerprint one server-resolved job and its bounded review materials."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("application folder is unavailable or unsafe")
    job_id = _numeric_job_id(row)
    if job_id is None:
        raise ValueError("application job id is unavailable")
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "job_id": job_id,
                "status": _safe_display_text(row.get("status"), maximum=40),
                "queue_bucket": _safe_display_text(
                    row.get("queue_bucket"), maximum=40
                ),
                "company": _safe_display_text(row.get("company"), maximum=120),
                "role": _safe_display_text(
                    row.get("role_title") or row.get("role"), maximum=160
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    allowed_suffixes = {".pdf", ".docx", ".txt", ".json", ".md"}
    files: list[Path] = []
    for path in sorted(folder.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError("application material cannot be a symlink")
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(folder):
            raise ValueError("application material escapes its job folder")
        files.append(resolved)
        if len(files) > 64:
            raise ValueError("application material set exceeds the review file limit")
    if not files:
        raise ValueError("application material set is empty")
    consumed = 0
    for path in files:
        content = _read_bounded_bytes(
            path, limit=min(20 * 1024 * 1024, maximum_bytes)
        )
        consumed += len(content)
        if consumed > maximum_bytes:
            raise ValueError("application material set exceeds the review byte limit")
        digest.update(b"\0path\0")
        digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
        digest.update(b"\0content\0")
        digest.update(content)
    return digest.hexdigest()


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


def _resolve_exact_artifact(root: Path, pointer: Any) -> Path:
    if not isinstance(pointer, str) or not pointer.strip():
        raise ValueError("exact artifact pointer is missing")
    candidate = Path(pointer).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = _strict_allowlisted_path(root, candidate, expect="file")
    if any(part.casefold() in {"latest", "current"} for part in resolved.parts):
        raise ValueError("mutable latest/current aliases are not review artifacts")
    if resolved.name.casefold().startswith("latest"):
        raise ValueError("mutable latest alias is not a review artifact")
    return resolved


def _bounded_private_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError("private review field is not text")
    if not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError("private review field is empty or outside its bound")
    return value


def _bounded_private_json(value: Any, *, maximum: int) -> str:
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError("private review context is not canonical JSON") from error
    if not rendered or len(rendered) > maximum or "\x00" in rendered:
        raise ValueError("private review context is outside its display bound")
    return rendered


def _strict_number(value: Any, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("review score is not numeric")
    result = float(value)
    if result < minimum or result > maximum:
        raise ValueError("review score is outside its bound")
    return result


def _canonical_linkedin_url(value: Any) -> str:
    raw = _bounded_private_text(value, maximum=500).strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() != "https" or parsed.hostname not in {
        "linkedin.com",
        "www.linkedin.com",
    }:
        raise ValueError("recipient LinkedIn URL is not canonical")
    if not re.fullmatch(r"/in/[A-Za-z0-9%._~-]+/?", parsed.path):
        raise ValueError("recipient LinkedIn URL is not a profile URL")
    return urlunsplit(
        ("https", "www.linkedin.com", parsed.path.rstrip("/"), "", "")
    )


def _canonical_email(value: Any) -> str:
    raw = _bounded_private_text(value, maximum=320).strip().casefold()
    if not re.fullmatch(
        r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}",
        raw,
        re.IGNORECASE,
    ):
        raise ValueError("recipient email is not valid")
    return raw


def _canonical_binding_sha(binding: dict[str, Any]) -> str:
    try:
        payload = json.dumps(
            binding,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("review binding is not canonical JSON") from error
    return hashlib.sha256(payload).hexdigest()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("private reviewed artifact already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def _write_private_text(path: Path, value: str) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("private reviewed artifact already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)


def _write_private_csv(
    path: Path, *, fieldnames: list[str], row: dict[str, Any]
) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("private reviewed artifact already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerow(row)


def _strict_allowlisted_path(root: Path, candidate: Path, *, expect: str) -> Path:
    root_input = root.expanduser().absolute()
    candidate_input = (
        candidate.expanduser().absolute()
        if candidate.is_absolute()
        else (root_input / candidate).absolute()
    )
    if root_input.is_symlink():
        raise ValueError("configured root cannot be a symlink")
    resolved_root = root_input.resolve(strict=True)
    try:
        relative = candidate_input.relative_to(root_input)
    except ValueError:
        # macOS can spell the same temporary directory through /var and
        # /private/var. Accept only that canonical-root equivalence; a resolved
        # candidate outside the canonical configured root still fails closed.
        try:
            canonical_candidate = candidate_input.resolve(strict=True)
            relative = canonical_candidate.relative_to(resolved_root)
        except (OSError, ValueError) as canonical_error:
            raise ValueError(
                "allowlisted path escapes its configured root"
            ) from canonical_error
        candidate_input = canonical_candidate
        cursor = resolved_root
    else:
        cursor = root_input
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("allowlisted path traverses a symlink")
    resolved = candidate_input.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("allowlisted path escapes its configured root")
    if expect == "file" and not resolved.is_file():
        raise ValueError("allowlisted file is unavailable")
    if expect == "directory" and not resolved.is_dir():
        raise ValueError("allowlisted directory is unavailable")
    return resolved

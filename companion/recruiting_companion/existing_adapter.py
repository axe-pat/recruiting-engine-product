from __future__ import annotations

import fcntl
import csv
import hashlib
import io
import json
import re
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import Settings


_SUMMARY_NAME = re.compile(
    r"^(?P<run_id>\d{8}-\d{6})-nightly-pipeline-summary\.json$"
)
_SUMMARY_TERMINAL = {"completed", "failed"}
_MANIFEST_TERMINAL = {"completed", "failed", "timed_out", "cancelled"}
_SOURCE_STATES = {
    "ran",
    "skipped",
    "partial",
    "failed",
    "partial_failed",
    "incomplete",
    "timed_out",
    "not_reported",
    "not_configured",
    "completed",
    "failed_missing_artifact",
    # ResumeGenerator's LinkedIn scoring stage emits these exact terminal
    # states. They are valid evidence-chain values, but every one still
    # normalizes to operator attention below.
    "failed_missing_scored_artifact",
    "failed_invalid_scored_artifact",
    "failed_scoring",
    "partial_failed_scoring",
}
_TYPED_ARRAYS = {
    "invite_send_artifacts",
    "linkedin_followup_draft_artifacts",
    "linkedin_followup_send_artifacts",
    "linkedin_reconcile_artifacts",
    "track_2_daily_run_artifacts",
    "track_2_phase_artifacts",
    "track_2_phase_results",
    "track_2_email_draft_artifacts",
    "track_2_email_send_artifacts",
}
_EXPECTED_SOURCE_FAMILIES = {
    "linkedin",
    "handshake",
    "jobspy",
    "startup_sources",
    "resume_generator_app_queue",
    "track_2",
}
_PUBLIC_REPORT_SOURCE_LABELS = {
    "LinkedIn",
    "LinkedIn home feed",
    "LinkedIn profile viewers",
    "Company/news feeds",
    "Handshake",
    "JobSpy",
    "Startup sources",
    "ResumeGenerator / app queue",
    "Track 2 imports / maintenance",
}
_REQUIRED_REPORT_SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "handshake": "Handshake",
    "jobspy": "JobSpy",
    "startup_sources": "Startup sources",
    "resume_generator_app_queue": "ResumeGenerator / app queue",
    "track_2": "Track 2 imports / maintenance",
}
_REPORTING_MISMATCH_CATEGORIES = (
    "missing_source",
    "duplicate_source",
    "status",
    "raw",
    "kept",
)
_DECISION_LANES = (
    "application_plus_outreach",
    "application_only",
    "outreach_only_today",
    "relationship_buffer",
    "follow_up",
    "skipped_internal",
)
_CURRENT_LOG_LIMIT_BYTES = 8 * 1024 * 1024
_CURRENT_STATE_LIMIT_BYTES = 256 * 1024
_CURRENT_COUNT_LIMIT = 1_000_000
_RUN_TIME_BINDING_TOLERANCE_SECONDS = 10
_MUTABLE_CAPTURE_MAX_BYTES = 192 * 1024 * 1024
_MUTABLE_CAPTURE_MAX_TREE_ENTRIES = 20_000
_LIVE_PROGRESS_NAME = re.compile(
    r"^linkedin_live_progress_(?P<stamp>\d{4}-\d{2}-\d{2}_\d{6})_"
    r"(?P<label>[A-Za-z0-9_-]{1,64})\.json$"
)
_LINKEDIN_SCORED_NAME = re.compile(
    r"^linkedin_live_scored_\d{4}-\d{2}-\d{2}_\d{6}\.json$"
)
_CURRENT_LOG_NAME = re.compile(
    r"^nightly_pipeline_(?P<stamp>\d{8}-\d{6})\.log$"
)
_CURRENT_PHASE_PATTERNS = (
    (
        "daily_engine",
        "Daily Engine",
        re.compile(r"(?m)^\$ [^\n]*discovery/scripts/run_daily_engine\.py(?:\s|$)"),
    ),
    (
        "linkedin_discovery",
        "LinkedIn discovery",
        re.compile(
            r"(?m)^\$ [^\n]*(?:run_linkedin_discovery\.sh|"
            r"discovery/auto/linkedin_live\.py)(?:\s|$)"
        ),
    ),
    (
        "handshake_discovery",
        "Handshake discovery",
        re.compile(r"(?m)^\$ [^\n]*run_handshake_discovery\.sh(?:\s|$)"),
    ),
    (
        "jobspy_discovery",
        "JobSpy discovery",
        re.compile(
            r"(?m)^\$ [^\n]*(?:fetch_jobspy_breadth\.py|"
            r"run_jobspy_scoring_lane\.py)(?:\s|$)"
        ),
    ),
    (
        "startup_sources",
        "Startup sources",
        re.compile(
            r"(?m)^\$ [^\n]*(?:startup_apply_pipeline\.py|"
            r"build_startup_source_report\.py|main\.py discover-source)(?:\s|$)"
        ),
    ),
    (
        "action_queue",
        "Daily action queue",
        re.compile(r"(?m)^\$ [^\n]*build_daily_action_queue\.py(?:\s|$)"),
    ),
    (
        "application_outreach",
        "Application outreach preparation",
        re.compile(r"(?m)^\$ [^\n]*main\.py run --company(?:\s|$)"),
    ),
    (
        "generation",
        "Resume generation",
        re.compile(
            r"(?m)^\$ [^\n]*(?:build_generation_shortlist\.py|"
            r"jobs\.py generate)(?:\s|$)"
        ),
    ),
    (
        "account_tracker",
        "Account tracker refresh",
        re.compile(r"(?m)^\$ [^\n]*main\.py account-tracker(?:\s|$)"),
    ),
    (
        "track_2",
        "Track 2 execution",
        re.compile(
            r"(?m)^\$ [^\n]*main\.py run-track-2-daily-plan(?:\s|$)"
        ),
    ),
    (
        "shared_discovery",
        "Shared discovery queue",
        re.compile(r"(?m)^\$ [^\n]*outreach\.shared_discovery(?:\s|$)"),
    ),
    (
        "final_report",
        "Exact run report",
        re.compile(
            r"(?m)^\$ [^\n]*main\.py write-daily-run-report(?:\s|$)"
        ),
    ),
    (
        "finalizing",
        "Run finalization",
        re.compile(r"(?m)^Nightly summary:"),
    ),
)


class MutableSnapshotBusy(ValueError):
    """A complete mutable capture observed an owned producer lock."""


class MutableSnapshotUnavailable(ValueError):
    """A required producer lock surface is missing or unreadable."""


class MutableSnapshotChanged(ValueError):
    """A tracked mutable artifact changed before capture finalization."""


class MutableSnapshotCapture:
    """Prove captured artifacts stayed stable without owning producer locks."""

    def __init__(self, *, started_ns: int) -> None:
        self.started_ns = started_ns
        self._files: dict[Path, tuple[int, int, int, int, str]] = {}
        self._trees: dict[Path, tuple[str, int]] = {}
        self._captured_bytes = 0

    def read_bytes(self, path: Path, *, limit: int) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise ValueError("mutable artifact is unavailable or unsafe")
        resolved = path.resolve(strict=True)
        before = resolved.stat()
        if before.st_mtime_ns > self.started_ns:
            raise MutableSnapshotChanged(
                "mutable artifact was modified after capture started"
            )
        if before.st_size > limit:
            raise ValueError("mutable artifact exceeds the capture limit")
        content = resolved.read_bytes()
        after = resolved.stat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(content) != before.st_size
        ):
            raise MutableSnapshotChanged("mutable artifact changed during read")
        digest = hashlib.sha256(content).hexdigest()
        fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            digest,
        )
        previous = self._files.get(resolved)
        if previous is not None and previous != fingerprint:
            raise MutableSnapshotChanged("mutable artifact changed between reads")
        if previous is None:
            if self._captured_bytes + len(content) > _MUTABLE_CAPTURE_MAX_BYTES:
                raise ValueError("mutable capture exceeds its cumulative byte limit")
            self._files[resolved] = fingerprint
            self._captured_bytes += len(content)
        return content

    def track_tree(self, path: Path) -> None:
        resolved = path.resolve(strict=False)
        fingerprint = self._tree_fingerprint(resolved)
        if fingerprint[1] > self.started_ns:
            raise MutableSnapshotChanged(
                "mutable inventory changed after capture started"
            )
        previous = self._trees.get(resolved)
        if previous is not None and previous != fingerprint:
            raise MutableSnapshotChanged("mutable directory changed between scans")
        self._trees[resolved] = fingerprint

    def revalidate(self) -> None:
        for path, expected in self._files.items():
            try:
                before = path.stat()
                if before.st_size > _MUTABLE_CAPTURE_MAX_BYTES:
                    raise MutableSnapshotChanged("mutable artifact grew beyond bounds")
                content = path.read_bytes()
                after = path.stat()
            except OSError as error:
                raise MutableSnapshotChanged(
                    "mutable artifact disappeared before finalization"
                ) from error
            current = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                hashlib.sha256(content).hexdigest(),
            )
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or len(content) != before.st_size
                or current != expected
            ):
                raise MutableSnapshotChanged(
                    "mutable artifact changed before capture finalization"
                )
        for path, expected in self._trees.items():
            if self._tree_fingerprint(path) != expected:
                raise MutableSnapshotChanged(
                    "mutable directory changed before capture finalization"
                )

    @staticmethod
    def _tree_fingerprint(path: Path) -> tuple[str, int]:
        if not path.exists():
            return hashlib.sha256(b"missing").hexdigest(), 0
        if path.is_symlink() or not path.is_dir():
            raise ValueError("mutable inventory root is unavailable or unsafe")
        records: list[tuple[Any, ...]] = []
        root_stat = path.stat()
        newest_mtime_ns = root_stat.st_mtime_ns
        records.append(
            (".", "directory", root_stat.st_dev, root_stat.st_ino, root_stat.st_mtime_ns)
        )
        for index, item in enumerate(
            sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()),
            start=1,
        ):
            if index > _MUTABLE_CAPTURE_MAX_TREE_ENTRIES:
                raise ValueError("mutable inventory exceeds the entry limit")
            item_stat = item.lstat()
            newest_mtime_ns = max(newest_mtime_ns, item_stat.st_mtime_ns)
            mode = item_stat.st_mode
            kind = (
                "symlink"
                if stat.S_ISLNK(mode)
                else "directory"
                if stat.S_ISDIR(mode)
                else "file"
                if stat.S_ISREG(mode)
                else "other"
            )
            records.append(
                (
                    item.relative_to(path).as_posix(),
                    kind,
                    item_stat.st_dev,
                    item_stat.st_ino,
                    item_stat.st_size,
                    item_stat.st_mtime_ns,
                )
            )
        encoded = json.dumps(
            records, sort_keys=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), newest_mtime_ns


class ExistingEngineAdapter:
    """Read-only, exact-pointer bridge to configured existing engines."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def status(self) -> dict[str, Any]:
        configured = bool(self.settings.resumegen_root and self.settings.outreach_root)
        roots_exist = bool(
            configured
            and self.settings.resumegen_root
            and self.settings.resumegen_root.is_dir()
            and self.settings.outreach_root
            and self.settings.outreach_root.is_dir()
        )
        attestation_available = bool(
            self.settings.attestation_path
            and self.settings.attestation_path.is_file()
        )
        result: dict[str, Any] = {
            "schema_version": "1.0",
            "mode": "existing",
            "data_class": "local-private",
            "access": "read_only",
            "configured": configured,
            "roots_available": roots_exist,
            "production_guard": (
                "configured" if attestation_available else "unavailable"
            ),
            "selection_policy": "parsed_created_at_then_run_id",
            "requires_exact_daily_engine_manifest": True,
            "workspace_latest_aliases_allowed": False,
            "mutations_enabled": False,
            "live_run": {
                "environment_enabled": self.settings.allow_live_runs,
                "supported": False,
                "reason": (
                    "Generic live-run mode is disabled. The separate operator "
                    "review ledger may expose one fixed production-nightly action."
                ),
            },
            "busy": False,
            "locks": self._lock_states(),
            "verified_run_count": 0,
            "latest_verified_run": None,
            "rejections": [],
        }
        result["busy"] = any(state == "busy" for state in result["locks"].values())
        if not configured:
            result["rejections"].append(
                "Configure both existing-engine repository roots to enable read-only verification."
            )
            return result
        if not roots_exist:
            result["rejections"].append("One or both configured engine roots do not exist.")
            return result
        if not attestation_available:
            result["rejections"].append(
                "A readable RECRUITING_ENGINE_ATTESTATION_PATH is required for production status."
            )
            return result

        verified, rejected = self._scan_runs()
        result["verified_run_count"] = len(verified)
        result["latest_verified_run"] = verified[-1] if verified else None
        result["rejections"] = rejected[-10:]
        return result

    def snapshot(self) -> dict[str, Any]:
        """Return aggregate run evidence and a separately labeled live snapshot."""
        status = self.status()
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        response: dict[str, Any] = {
            "schema_version": "1.0",
            "mode": "existing",
            "data_class": "local-private",
            "generated_at": generated_at,
            "run_snapshot": {
                "scope": "run-scoped",
                "status": "unavailable",
                "reason": "No fully verified terminal run is available.",
            },
            "current_workspace": {
                "scope": "current-snapshot",
                "status": "unavailable",
                "captured_at": generated_at,
                "reasons": [],
            },
        }
        latest = status.get("latest_verified_run")
        if isinstance(latest, dict):
            try:
                response["run_snapshot"] = self._project_verified_run(latest)
            except (ValueError, OSError, json.JSONDecodeError) as error:
                response["run_snapshot"] = {
                    "scope": "run-scoped",
                    "status": "unavailable",
                    "run_id": latest.get("run_id"),
                    "reason": f"Exact run projection failed: {error}",
                }

        if not status.get("roots_available"):
            response["current_workspace"]["reasons"] = [
                "Mutable workspace capture requires both configured engine roots."
            ]
        else:
            try:
                with self.mutable_snapshot_capture() as capture:
                    assert self.settings.resumegen_root is not None
                    capture.track_tree(
                        self.settings.resumegen_root
                        / "apps"
                        / "Apply queues"
                        / "current_apply_queue"
                    )
                    response["current_workspace"] = self._current_workspace_snapshot(
                        captured_at=generated_at,
                        capture=capture,
                    )
            except MutableSnapshotBusy as error:
                response["current_workspace"] = {
                    "scope": "current-snapshot",
                    "consistency": "not-captured",
                    "transactional": False,
                    "status": "busy",
                    "captured_at": generated_at,
                    "reasons": [str(error)],
                    "lock_states": status.get("locks", {}),
                }
            except MutableSnapshotUnavailable as error:
                response["current_workspace"] = {
                    "scope": "current-snapshot",
                    "consistency": "not-captured",
                    "transactional": False,
                    "status": "unavailable",
                    "captured_at": generated_at,
                    "reasons": [str(error)],
                    "lock_states": status.get("locks", {}),
                }
            except (MutableSnapshotChanged, OSError, ValueError) as error:
                response["current_workspace"] = {
                    "scope": "current-snapshot",
                    "consistency": "changed-during-capture",
                    "transactional": False,
                    "status": "partial",
                    "captured_at": generated_at,
                    "reasons": [f"Mutable workspace capture failed closed: {type(error).__name__}."],
                    "lock_states": status.get("locks", {}),
                }
        return response

    def verified_run_projections(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Project only summaries that pass the complete run evidence chain."""
        if not (
            self.settings.resumegen_root
            and self.settings.outreach_root
            and self.settings.attestation_path
            and self.settings.attestation_path.is_file()
        ):
            return []
        verified, _ = self._scan_runs()
        projections: list[dict[str, Any]] = []
        for run in verified[-min(max(int(limit), 1), 50) :]:
            try:
                projections.append(self._project_verified_run(run))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        return projections

    def _newest_verified_run_projection(self) -> dict[str, Any] | None:
        """Verify newest candidates only until the first complete chain succeeds."""
        if not (
            self.settings.resumegen_root
            and self.settings.outreach_root
            and self.settings.attestation_path
            and self.settings.attestation_path.is_file()
        ):
            return None
        directory = self.settings.resumegen_root / "discovery" / "source_validation"
        if not directory.is_dir():
            return None
        candidates: list[tuple[str, Path]] = []
        for path in directory.glob("*-nightly-pipeline-summary.json"):
            match = _SUMMARY_NAME.fullmatch(path.name)
            if match:
                candidates.append((match.group("run_id"), path))
        for run_id, path in sorted(candidates, reverse=True):
            try:
                verified = self._verify_summary(path, run_id)
                verified.pop("_created_at_epoch", None)
                return self._project_verified_run(verified)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        return None

    def lock_states(self) -> dict[str, str]:
        return self._lock_states()

    @contextmanager
    def mutable_snapshot_capture(self) -> Iterator[MutableSnapshotCapture]:
        """Prove a stable read without ever owning an upstream producer lock."""
        started_ns = time.time_ns()
        starting_locks = self._lock_states()
        if any(state == "busy" for state in starting_locks.values()):
            raise MutableSnapshotBusy(
                "mutable snapshot requires all five producer locks to be free"
            )
        if any(state != "free" for state in starting_locks.values()):
            raise MutableSnapshotUnavailable(
                "one or more mutable snapshot lock surfaces are unavailable"
            )
        capture = MutableSnapshotCapture(started_ns=started_ns)
        yield capture
        ending_locks = self._lock_states()
        if any(state != "free" for state in ending_locks.values()):
            raise MutableSnapshotChanged(
                "a producer lock became busy during mutable capture"
            )
        capture.revalidate()

    def current_workspace_snapshot(
        self,
        *,
        captured_at: str,
        capture: MutableSnapshotCapture,
    ) -> dict[str, Any]:
        """Read the mutable workspace inside a noninterfering capture ledger."""
        return self._current_workspace_snapshot(
            captured_at=captured_at,
            capture=capture,
        )

    def run_progress(
        self,
        *,
        verified_runs: list[dict[str, Any]] | None = None,
        locks: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Project an active run, or fall back to the newest exact terminal run.

        Active evidence is intentionally restricted to scheduler metadata, an
        exact timestamped log, an exact run-id manifest, and allowlisted
        aggregates from artifacts carrying the exact parent run id. Raw log
        lines, search terms, cards, URLs, and source rows never enter the
        projection.
        """
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        lock_states = locks if isinstance(locks, dict) else self._lock_states()
        if lock_states.get("pipeline") == "busy":
            progress = self._active_run_progress(
                captured_at=captured_at,
                starting_locks=lock_states,
            )
            ending_locks = self._lock_states()
            if ending_locks.get("pipeline") != "busy":
                return self._unavailable_run_progress(
                    captured_at=captured_at,
                    status="partial",
                    selection="current",
                    is_current=True,
                    reason=(
                        "Pipeline lock state changed during progress capture; "
                        "mutable progress evidence was discarded."
                    ),
                )
            return progress

        projections = verified_runs
        if projections is None:
            latest = self._newest_verified_run_projection()
        else:
            latest = projections[-1] if projections else None
        terminal_attempt = self._latest_scheduler_terminal_attempt_progress(
            latest_verified=latest,
            captured_at=captured_at,
        )
        if terminal_attempt is not None:
            return terminal_attempt
        if isinstance(latest, dict):
            return self._terminal_run_progress(latest, captured_at=captured_at)
        return self._unavailable_run_progress(
            captured_at=captured_at,
            reason="No active run or fully verified terminal run is available.",
        )

    def _latest_scheduler_terminal_attempt_progress(
        self,
        *,
        latest_verified: dict[str, Any] | None,
        captured_at: str,
    ) -> dict[str, Any] | None:
        if not self.settings.runtime_dir:
            return None
        state_path = self.settings.runtime_dir / "nightly_scheduler_state.json"
        try:
            content = self._read_stable_content(
                state_path,
                limit=_CURRENT_STATE_LIMIT_BYTES,
            )
            state = self._parse_object(content, state_path)
            if state.get("last_run_was_actual_pipeline") is not True:
                return None
            started = self._parse_time(
                state.get("last_attempt_started_at"),
                "scheduler last_attempt_started_at",
            )
            completed = self._parse_time(
                state.get("last_run_completed_at"),
                "scheduler last_run_completed_at",
            )
            captured = self._parse_time(captured_at, "progress captured_at")
            if not _progress_time_is_bounded(completed, started, captured):
                return None
            run_id = started.strftime("%Y%m%d-%H%M%S")
            expected_date = started.strftime("%Y-%m-%d")
            if state.get("last_attempt_date") != expected_date or state.get(
                "last_run_date"
            ) != expected_date:
                return None
            exit_code = state.get("last_run_exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                return None
            scheduler_status = state.get("last_run_status")
            if scheduler_status not in {
                "completed",
                "failed_or_incomplete",
                "failed_missing_summary",
            }:
                return None
            if isinstance(latest_verified, dict):
                latest_run_id = str(latest_verified.get("run_id") or "")
                if latest_run_id == run_id:
                    return None
                latest_started = _safe_timestamp(latest_verified.get("started_at"))
                if latest_started and (
                    started.timestamp()
                    <= _timestamp_epoch(latest_started)
                    + _RUN_TIME_BINDING_TOLERANCE_SECONDS
                ):
                    return None
            evidence = self._evidence_for_content(
                state_path,
                self.settings.runtime_dir,
                content,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None

        reason = (
            "The latest scheduler attempt completed with a nonzero exit, and "
            "its exact nightly summary/report evidence chain did not verify."
            if exit_code != 0
            else "The latest scheduler attempt completed, but its exact nightly "
            "summary/report evidence chain did not verify."
        )
        return {
            "schema_version": "1.0",
            "status": "attention",
            "reason": reason,
            "selection": "latest_scheduler_attempt",
            "scope": "run-scoped",
            "is_current": False,
            "run_id": run_id,
            "phase": {
                "id": "terminal_evidence_incomplete",
                "label": "Run evidence needs attention",
                "status": "attention",
            },
            "timestamps": {
                "started_at": started.isoformat(),
                "last_progress_at": completed.isoformat(),
                "completed_at": completed.isoformat(),
                "captured_at": captured_at,
            },
            "counts": _empty_progress_counts(),
            "evidence": [
                _progress_evidence_item(
                    "scheduler_state",
                    evidence,
                    binding="exact_terminal_scheduler_attempt",
                )
            ],
        }

    def _active_run_progress(
        self,
        *,
        captured_at: str,
        starting_locks: dict[str, str],
    ) -> dict[str, Any]:
        base = self._unavailable_run_progress(
            captured_at=captured_at,
            status="partial",
            selection="current",
            is_current=True,
            reason=(
                "The pipeline lock is owned, but exact current-run metadata is "
                "not yet readable."
            ),
        )
        if not self.settings.runtime_dir or not self.settings.resumegen_root:
            return base
        if starting_locks.get("scheduler") != "busy":
            base["reason"] = (
                "The pipeline is busy without an owned scheduler lock; current "
                "scheduler attempt metadata was not treated as active-run evidence."
            )
            return base

        state_path = self.settings.runtime_dir / "nightly_scheduler_state.json"
        evidence: list[dict[str, Any]] = []
        reasons: list[str] = []
        captured = self._parse_time(captured_at, "progress captured_at")
        try:
            state_content = self._read_stable_content(
                state_path,
                limit=_CURRENT_STATE_LIMIT_BYTES,
            )
            state = self._parse_object(state_content, state_path)
            state_evidence = self._evidence_for_content(
                state_path,
                self.settings.runtime_dir,
                state_content,
            )
            evidence.append(
                _progress_evidence_item(
                    "scheduler_state",
                    state_evidence,
                    binding="active_scheduler_attempt",
                )
            )
            started = self._parse_time(
                state.get("last_attempt_started_at"),
                "scheduler last_attempt_started_at",
            )
            if started.timestamp() > captured.timestamp():
                raise ValueError("scheduler attempt begins after progress capture")
            scheduler_attempt_id = started.strftime("%Y%m%d-%H%M%S")
            run_id = scheduler_attempt_id
            expected_date = started.strftime("%Y-%m-%d")
            state_date = state.get("last_attempt_date")
            if isinstance(state_date, str) and state_date and state_date != expected_date:
                raise ValueError("scheduler attempt date does not match its timestamp")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            base["reason"] = (
                "Pipeline is busy, but scheduler attempt evidence failed closed: "
                f"{type(error).__name__}."
            )
            return base

        phase_id = "starting"
        phase_label = "Starting run"
        phase_status = "running"
        counts = _empty_progress_counts()
        last_progress_at = started.isoformat()
        exact_log_bound = False
        timestamp_anomaly = False

        log_content = b""
        log_text = ""
        try:
            log_path = self._current_log_path(
                scheduler_attempt_id,
                started_epoch=started.timestamp(),
            )
            captured_log_content, captured_log_mtime = self._read_append_snapshot(
                log_path,
                limit=_CURRENT_LOG_LIMIT_BYTES,
            )
            if captured_log_mtime < started.timestamp() - 5:
                raise ValueError("current run log predates the scheduler attempt")
            captured_log_text = captured_log_content.decode(
                "utf-8", errors="replace"
            )
            filename_match = _CURRENT_LOG_NAME.fullmatch(log_path.name)
            if not filename_match:
                raise ValueError("current run log filename is not run scoped")
            filename_run_id = filename_match.group("stamp")
            if (
                abs(_run_id_epoch(filename_run_id) - started.timestamp())
                > _RUN_TIME_BINDING_TOLERANCE_SECONDS
            ):
                raise ValueError("current run log filename binds another attempt")
            bound_ids = set(
                re.findall(
                    r"(?m)^\$ [^\n]*--run-id\s+(\d{8}-\d{6})(?:\s|$)",
                    captured_log_text,
                )
            )
            if len(bound_ids) > 1:
                raise ValueError("run log binds multiple run ids")
            if bound_ids and next(iter(bound_ids)) != filename_run_id:
                raise ValueError("run log content and filename bind different run ids")
            if filename_run_id != scheduler_attempt_id and not bound_ids:
                raise ValueError("nearby run log lacks an explicit run-id binding")
            run_id = filename_run_id
            log_content = captured_log_content
            log_text = captured_log_text
            log_evidence = {
                "state": "captured_prefix",
                "path": log_path.name,
                "sha256": hashlib.sha256(log_content).hexdigest(),
                "size_bytes": len(log_content),
            }
            evidence.append(
                _progress_evidence_item(
                    "run_log",
                    log_evidence,
                    binding="exact_active_log_run_id",
                )
            )
            exact_log_bound = True
            phase_id, phase_label, _ = _phase_from_current_log(log_text)
            log_modified = datetime.fromtimestamp(captured_log_mtime).astimezone()
            if _progress_time_is_bounded(log_modified, started, captured):
                last_progress_at = log_modified.isoformat(timespec="seconds")
            else:
                timestamp_anomaly = True
                reasons.append(
                    "The exact run-log prefix timestamp fell outside the active "
                    "run capture window."
                )
        except (OSError, ValueError) as error:
            log_content = b""
            log_text = ""
            reasons.append(f"Current run log unavailable: {type(error).__name__}.")

        live_progress = self._live_progress_checkpoint(
            started,
            run_id=run_id,
            captured=captured,
        )
        if live_progress is not None:
            checkpoint, checkpoint_evidence = live_progress
            evidence.append(checkpoint_evidence)
            searches = checkpoint.get("searches")
            counts["searches_completed"] = _bounded_count(
                checkpoint.get("searches_completed")
            )
            counts["searches_total"] = (
                _bounded_count(len(searches)) if isinstance(searches, list) else None
            )
            counts["items_discovered"] = _bounded_count(
                checkpoint.get("total_extracted")
            )
            checkpoint_status = str(checkpoint.get("status") or "running")
            if not re.fullmatch(r"[a-z0-9_-]{1,40}", checkpoint_status):
                checkpoint_status = "running"
            checkpoint_time = _safe_timestamp(checkpoint.get("last_progress_at"))
            checkpoint_datetime = (
                self._parse_time(checkpoint_time, "live progress last_progress_at")
                if checkpoint_time
                else None
            )
            if (
                checkpoint_datetime is not None
                and _progress_time_is_bounded(
                    checkpoint_datetime, started, captured
                )
                and checkpoint_datetime.timestamp()
                > _timestamp_epoch(last_progress_at)
            ):
                last_progress_at = checkpoint_datetime.isoformat(timespec="seconds")
            phase_order = {
                item[0]: index for index, item in enumerate(_CURRENT_PHASE_PATTERNS)
            }
            if phase_order.get(phase_id, -1) <= phase_order.get(
                "linkedin_discovery", -1
            ):
                phase_id = "linkedin_discovery"
                phase_label = "LinkedIn discovery"
                phase_status = checkpoint_status

        manifest_progress = self._current_manifest_progress(run_id)
        if manifest_progress is not None:
            manifest_counts, manifest_evidence = manifest_progress
            counts.update(manifest_counts)
            evidence.extend(manifest_evidence)

        progress_status = (
            "running" if exact_log_bound and not timestamp_anomaly else "partial"
        )
        if "Scored artifact:" in log_text:
            scoring_progress = self._current_scoring_progress(started, log_text)
            if scoring_progress is None:
                progress_status = "partial"
                reasons.append(
                    "The current scoring artifact pointer could not be verified."
                )
            else:
                scoring_counts, scoring_evidence = scoring_progress
                counts.update(scoring_counts)
                evidence.append(scoring_evidence)
                attempted = scoring_counts["scoring_attempted"] or 0
                scoring_errors = scoring_counts["scoring_errors"] or 0
                accepted = scoring_counts["accepted_for_write"] or 0
                if attempted > 0 and scoring_errors >= attempted:
                    progress_status = "attention"
                    reasons.append(
                        "The exact current-run scoring artifact reports all "
                        f"{attempted} fresh scoring attempts as errors and "
                        f"{accepted} accepted for write."
                    )

        if not log_content and len(evidence) == 1:
            # The scheduler state and owned pipeline lock prove an attempt, but
            # not which producer stage has begun.
            reasons.append("No exact current-run producer artifact is readable yet.")

        return {
            "schema_version": "1.0",
            "status": progress_status,
            "reason": " ".join(reasons),
            "selection": "current",
            "scope": "current-snapshot",
            "is_current": True,
            "run_id": run_id,
            "phase": {
                "id": phase_id,
                "label": phase_label,
                "status": phase_status,
            },
            "timestamps": {
                "started_at": started.isoformat(),
                "last_progress_at": last_progress_at,
                "completed_at": None,
                "captured_at": captured_at,
            },
            "counts": counts,
            "evidence": evidence,
            "locks": {
                name: starting_locks.get(name, "unavailable")
                for name in ("scheduler", "pipeline", "adapter_mutation")
            },
        }

    def _terminal_run_progress(
        self,
        run: dict[str, Any],
        *,
        captured_at: str,
    ) -> dict[str, Any]:
        sources = [
            value for value in run.get("sources", []) if isinstance(value, dict)
        ]
        successful = 0
        attention = 0
        raw_total = 0
        kept_total = 0
        scoring_attempted_total = 0
        scoring_error_total = 0
        accepted_total = 0
        scoring_evidence_available = False
        for source in sources:
            source_status = str(source.get("status") or "not_reported")
            if source_status in {"ran", "completed", "not_configured"}:
                successful += 1
            elif _source_status_requires_attention(source_status):
                attention += 1
            raw_total += _bounded_count(source.get("raw_count")) or 0
            kept_total += _bounded_count(source.get("kept_count")) or 0
            if any(
                source.get(key) is not None
                for key in (
                    "scoring_attempted",
                    "scoring_errors",
                    "accepted_for_write",
                )
            ):
                scoring_evidence_available = True
            scoring_attempted_total += _bounded_count(
                source.get("scoring_attempted")
            ) or 0
            scoring_error_total += _bounded_count(source.get("scoring_errors")) or 0
            accepted_total += _bounded_count(source.get("accepted_for_write")) or 0
        report = run.get("report") if isinstance(run.get("report"), dict) else {}
        queue = run.get("queue") if isinstance(run.get("queue"), dict) else {}
        counts = _empty_progress_counts()
        counts.update(
            {
                "items_discovered": min(raw_total, _CURRENT_COUNT_LIMIT),
                "scoring_attempted": (
                    min(scoring_attempted_total, _CURRENT_COUNT_LIMIT)
                    if scoring_evidence_available
                    else None
                ),
                "scoring_errors": (
                    min(scoring_error_total, _CURRENT_COUNT_LIMIT)
                    if scoring_evidence_available
                    else None
                ),
                "accepted_for_write": (
                    min(accepted_total, _CURRENT_COUNT_LIMIT)
                    if scoring_evidence_available
                    else None
                ),
                "source_families_total": len(sources),
                "source_families_successful": successful,
                "source_families_attention": attention,
                "raw_total": min(raw_total, _CURRENT_COUNT_LIMIT),
                "kept_total": min(kept_total, _CURRENT_COUNT_LIMIT),
                "decision_total": _bounded_count(queue.get("decision_total")),
                "pending_review_count": _bounded_count(
                    report.get("pending_review_count")
                ),
            }
        )
        status = str(run.get("status") or "attention")
        complete = status == "complete"
        evidence = []
        for kind in (
            "summary",
            "daily_manifest",
            "source_metrics",
            "action_queue",
            "outreach_report",
        ):
            item = (run.get("evidence") or {}).get(kind)
            if isinstance(item, dict):
                evidence.append(
                    _progress_evidence_item(kind, item, binding="exact_terminal_run")
                )
        completed_at = _safe_timestamp(run.get("completed_at")) or None
        return {
            "schema_version": "1.0",
            "status": "complete" if complete else "attention",
            "reason": "" if complete else "Latest exact terminal run requires attention.",
            "selection": "most_recent_verified",
            "scope": "run-scoped",
            "is_current": False,
            "run_id": run.get("run_id"),
            "phase": {
                "id": "completed" if complete else "needs_attention",
                "label": "Run completed" if complete else "Run needs attention",
                "status": "complete" if complete else "attention",
            },
            "timestamps": {
                "started_at": _safe_timestamp(run.get("started_at")) or None,
                "last_progress_at": completed_at,
                "completed_at": completed_at,
                "captured_at": captured_at,
            },
            "counts": counts,
            "evidence": evidence,
        }

    def _unavailable_run_progress(
        self,
        *,
        captured_at: str,
        reason: str,
        status: str = "unavailable",
        selection: str = "unavailable",
        is_current: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "status": status,
            "reason": reason,
            "selection": selection,
            "scope": "current-snapshot" if is_current else "run-scoped",
            "is_current": is_current,
            "run_id": None,
            "phase": {
                "id": "unavailable",
                "label": "Run progress unavailable",
                "status": "unavailable",
            },
            "timestamps": {
                "started_at": None,
                "last_progress_at": None,
                "completed_at": None,
                "captured_at": captured_at,
            },
            "counts": _empty_progress_counts(),
            "evidence": [],
        }

    def _current_log_path(self, run_id: str, *, started_epoch: float) -> Path:
        if not self.settings.runtime_dir:
            raise ValueError("runtime directory is unavailable")
        if not re.fullmatch(r"\d{8}-\d{6}", run_id):
            raise ValueError("current run id is invalid")
        library_dir = self.settings.runtime_dir.parent.parent
        log_dir = library_dir / "Logs" / self.settings.runtime_dir.name
        resolved_dir = log_dir.resolve(strict=True)
        expected = resolved_dir / f"nightly_pipeline_{run_id}.log"
        candidates = [expected] if expected.is_file() else []
        if not candidates:
            for path in resolved_dir.iterdir():
                match = _CURRENT_LOG_NAME.fullmatch(path.name)
                if not match or path.is_symlink() or not path.is_file():
                    continue
                if abs(_run_id_epoch(match.group("stamp")) - started_epoch) <= 10:
                    candidates.append(path)
        if len(candidates) != 1 or candidates[0].is_symlink():
            raise ValueError("current run log identity is unavailable or ambiguous")
        candidate = candidates[0].resolve(strict=True)
        if not candidate.is_relative_to(resolved_dir):
            raise ValueError("current run log is unsafe")
        return candidate

    def _live_progress_checkpoint(
        self,
        started: datetime,
        *,
        run_id: str,
        captured: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        assert self.settings.resumegen_root is not None
        directory = self.settings.resumegen_root / "discovery" / "auto" / "logs"
        if not directory.is_dir():
            return None
        started_epoch = started.timestamp()
        candidates: list[tuple[float, Path]] = []
        for path in directory.iterdir():
            match = _LIVE_PROGRESS_NAME.fullmatch(path.name)
            if not match or path.is_symlink():
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if started_epoch <= modified <= captured.timestamp():
                candidates.append((modified, path))
        for _, path in sorted(candidates, reverse=True)[:20]:
            try:
                content = self._read_stable_content(
                    path,
                    limit=_CURRENT_STATE_LIMIT_BYTES,
                )
                payload = self._parse_object(content, path)
                if payload.get("parent_run_id") != run_id:
                    # A time-window match is not an exact producer binding. Old
                    # and manual LinkedIn sessions can overlap a nightly, so
                    # only an explicit parent id is eligible for projection.
                    continue
                payload_started = self._parse_time(
                    payload.get("started_at"), "live progress started_at"
                )
                if not _progress_time_is_bounded(
                    payload_started, started, captured
                ):
                    continue
                match = _LIVE_PROGRESS_NAME.fullmatch(path.name)
                if not match or payload.get("run_stamp") != match.group("stamp"):
                    continue
                item = _progress_evidence_item(
                    "linkedin_progress",
                    self._evidence_for_content(
                        path,
                        self.settings.resumegen_root,
                        content,
                    ),
                    binding="exact_active_parent_run_id",
                )
                return payload, item
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    def _current_manifest_progress(
        self,
        run_id: str,
    ) -> tuple[dict[str, int | None], list[dict[str, Any]]] | None:
        assert self.settings.resumegen_root is not None
        path = (
            self.settings.resumegen_root
            / "discovery"
            / "source_validation"
            / f"{run_id}-daily-engine-run-manifest.json"
        )
        if not path.is_file() or path.is_symlink():
            return None
        try:
            content = self._read_stable_content(path, limit=20 * 1024 * 1024)
            manifest = self._parse_object(content, path)
            self._validate_manifest(manifest, run_id)
            sources = manifest.get("source_families", {})
            successful = 0
            attention = 0
            raw_total = 0
            kept_total = 0
            for source in sources.values():
                if not isinstance(source, dict):
                    continue
                status = str(source.get("status") or "not_reported")
                if status in {"ran", "completed", "not_configured"}:
                    successful += 1
                elif _source_status_requires_attention(status):
                    attention += 1
                raw_total += _bounded_count(source.get("raw_count")) or 0
                kept_total += _bounded_count(source.get("kept_count")) or 0
            counts: dict[str, int | None] = {
                "items_discovered": min(raw_total, _CURRENT_COUNT_LIMIT),
                "source_families_total": min(
                    len(sources), _CURRENT_COUNT_LIMIT
                ),
                "source_families_successful": successful,
                "source_families_attention": attention,
                "raw_total": min(raw_total, _CURRENT_COUNT_LIMIT),
                "kept_total": min(kept_total, _CURRENT_COUNT_LIMIT),
                "decision_total": None,
                "pending_review_count": None,
            }
            manifest_evidence = self._evidence_for_content(
                path, self.settings.resumegen_root, content
            )
            evidence = [
                _progress_evidence_item(
                    "daily_manifest",
                    manifest_evidence,
                    binding="exact_active_run_id",
                )
            ]
            action_path = self._resolve_pointer(
                self.settings.resumegen_root,
                manifest.get("action_queue"),
                "current run action queue",
            )
            action_content = self._read_stable_content(
                action_path, limit=20 * 1024 * 1024
            )
            action_queue = self._parse_object(action_content, action_path)
            lane_counts = _validated_action_queue_lane_counts(action_queue)
            counts["decision_total"] = min(
                sum(lane_counts.values()), _CURRENT_COUNT_LIMIT
            )
            evidence.append(
                _progress_evidence_item(
                    "action_queue",
                    self._evidence_for_content(
                        action_path,
                        self.settings.resumegen_root,
                        action_content,
                    ),
                    binding="exact_active_manifest_pointer",
                )
            )
            return counts, evidence
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _current_scoring_progress(
        self,
        started: datetime,
        log_text: str,
    ) -> tuple[dict[str, int | None], dict[str, Any]] | None:
        assert self.settings.resumegen_root is not None
        pointers = re.findall(r"(?m)^Scored artifact:\s*(.+?)\s*$", log_text)
        for pointer in reversed(pointers):
            if not pointer or len(pointer) > 2048 or "\x00" in pointer:
                continue
            try:
                unresolved = Path(pointer).expanduser()
                if not unresolved.is_absolute():
                    unresolved = self.settings.resumegen_root / unresolved
                if unresolved.is_symlink():
                    continue
                path = self._resolve_pointer(
                    self.settings.resumegen_root,
                    pointer,
                    "current run LinkedIn scored artifact",
                )
                if not _LINKEDIN_SCORED_NAME.fullmatch(path.name) or path.is_symlink():
                    continue
                if path.stat().st_mtime < started.timestamp() - 5:
                    continue
                content = self._read_stable_content(path, limit=20 * 1024 * 1024)
                payload = self._parse_object(content, path)
                attempted = _bounded_count(payload.get("scored"))
                reviewed = _bounded_count(payload.get("reviewed"))
                cache_skipped = _bounded_count(payload.get("cache_skipped"))
                accepted = _bounded_count(payload.get("accepted_for_write"))
                jobs = payload.get("jobs")
                if (
                    attempted is None
                    or reviewed is None
                    or cache_skipped is None
                    or accepted is None
                    or not isinstance(jobs, list)
                    or reviewed != len(jobs)
                    or attempted > reviewed
                    or cache_skipped != reviewed - attempted
                    or accepted > attempted
                    or any(not isinstance(item, dict) for item in jobs)
                ):
                    continue
                fresh = jobs[:attempted]
                cached = jobs[attempted:]
                if any(str(item.get("status") or "") == "cached_skip" for item in fresh):
                    continue
                if any(str(item.get("status") or "") != "cached_skip" for item in cached):
                    continue
                scoring_errors = sum(
                    1 for item in fresh if str(item.get("decision") or "") == "Error"
                )
                counts = {
                    "scoring_attempted": attempted,
                    "scoring_errors": min(scoring_errors, _CURRENT_COUNT_LIMIT),
                    "accepted_for_write": accepted,
                }
                evidence = _progress_evidence_item(
                    "linkedin_scored",
                    self._evidence_for_content(
                        path,
                        self.settings.resumegen_root,
                        content,
                    ),
                    binding="exact_active_log_pointer",
                )
                return counts, evidence
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    @staticmethod
    def _read_stable_content(path: Path, *, limit: int) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise ValueError("artifact is unavailable or unsafe")
        before = path.stat()
        if before.st_size > limit:
            raise ValueError("artifact exceeds the projection limit")
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(content) != before.st_size
        ):
            raise ValueError("artifact changed during capture")
        return content

    @staticmethod
    def _read_append_snapshot(path: Path, *, limit: int) -> tuple[bytes, float]:
        if path.is_symlink() or not path.is_file():
            raise ValueError("append-only artifact is unavailable or unsafe")
        before = path.stat()
        if before.st_size > limit:
            raise ValueError("append-only artifact exceeds the projection limit")
        with path.open("rb") as handle:
            content = handle.read(before.st_size)
        after = path.stat()
        if before.st_ino != after.st_ino or after.st_size < before.st_size:
            raise ValueError("append-only artifact changed identity during capture")
        if len(content) != before.st_size:
            raise ValueError("append-only artifact was truncated during capture")
        # The mtime belongs to the exact prefix length captured above. A later
        # stat could observe bytes that are deliberately absent from `content`
        # and make an older projected phase appear fresh.
        return content, before.st_mtime

    def _project_verified_run(self, run: dict[str, Any]) -> dict[str, Any]:
        assert self.settings.resumegen_root is not None
        assert self.settings.outreach_root is not None
        evidence = run["evidence"]
        manifest_path = self._resolve_pointer(
            self.settings.resumegen_root,
            evidence["daily_manifest"]["path"],
            "verified daily manifest",
        )
        action_path = self._resolve_pointer(
            self.settings.resumegen_root,
            evidence["action_queue"]["path"],
            "verified action queue",
        )
        report_path = self._resolve_pointer(
            self.settings.outreach_root,
            evidence["outreach_report"]["path"],
            "verified outreach report",
        )
        manifest = self._read_bound_object(
            manifest_path,
            evidence["daily_manifest"],
            "verified daily manifest",
        )
        action_queue = self._read_bound_object(
            action_path,
            evidence["action_queue"],
            "verified action queue",
        )
        report = self._read_bound_object(
            report_path,
            evidence["outreach_report"],
            "verified outreach report",
        )
        reporting_consistency = _source_reporting_consistency(manifest, report)
        sources = []
        for name in sorted(_EXPECTED_SOURCE_FAMILIES):
            value = manifest.get("source_families", {}).get(name, {})
            if not isinstance(value, dict):
                continue
            reported_status = str(value.get("status") or "not_reported")
            scoring = _source_scoring_summary(value)
            health_status = (
                "failed_all_scoring"
                if scoring["all_failed"]
                else reported_status
            )
            sources.append(
                {
                    "source": str(name),
                    "status": health_status,
                    "reported_status": reported_status,
                    "raw_count": _safe_int(value.get("raw_count")),
                    "kept_count": _safe_int(value.get("kept_count")),
                    "scoring_attempted": scoring["attempted"],
                    "scoring_errors": scoring["errors"],
                    "accepted_for_write": scoring["accepted"],
                }
            )
        sources.sort(key=lambda item: item["source"])
        report_sources = []
        for value in report.get("source_breakdown", []):
            if not isinstance(value, dict):
                continue
            report_sources.append(
                {
                    "source": _safe_report_source_label(value.get("source")),
                    "status": str(value.get("status") or "not_reported"),
                    "raw": _safe_int(value.get("raw")),
                    "kept": _safe_int(value.get("kept")),
                }
            )
        stage_metrics: dict[str, Any] = {}
        for name, value in (report.get("stage_metrics") or {}).items():
            if not isinstance(value, dict):
                continue
            stage_metrics[str(name)] = {
                "status": str(value.get("status") or "not_reported"),
                "runtime_seconds": _safe_number(value.get("runtime_seconds")),
                "returncode": _safe_int_or_none(value.get("returncode")),
            }
        queue_counts = _numeric_tree(action_queue.get("counts", {}))
        track_2_execution = report.get("track_2_execution")
        if not isinstance(track_2_execution, dict):
            track_2_execution = {}
        decision_parts = _validated_action_queue_lane_counts(action_queue)
        return {
            "scope": "run-scoped",
            "status": run["status"],
            "run_id": run["run_id"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "failure_count": _safe_int(run.get("failure_count")),
            "reporting_consistency": reporting_consistency,
            "delivery_contract": run.get("delivery_contract", {}),
            "daily_engine": {
                "status": manifest.get("status"),
                "returncode": manifest.get("returncode"),
            },
            "sources": sources,
            "queue": {
                "counts": queue_counts,
                "decision_total": sum(decision_parts.values()),
                "decision_total_name": "validated_action_queue_lane_entries",
                "decision_total_parts": decision_parts,
            },
            "report": {
                "status": "valid",
                "run_status": str(report.get("run_status") or "not_reported"),
                "track_2_status": str(
                    track_2_execution.get("status") or "not_reported"
                ),
                "sources": report_sources,
                "stage_metrics": stage_metrics,
                "workspace_counts": _numeric_tree(report.get("workspace_counts", {})),
                "invite_totals": _numeric_tree(report.get("invite_totals", {})),
                "pending_review_count": _safe_int(report.get("pending_review_count")),
                "track_2_returncode": _safe_int_or_none(
                    report.get("track_2_returncode")
                ),
                "track_2_failed": bool(report.get("track_2_failed", False)),
                "reporting_consistency": reporting_consistency,
            },
            "evidence": evidence,
        }

    def _current_workspace_snapshot(
        self,
        *,
        captured_at: str,
        capture: MutableSnapshotCapture | None = None,
    ) -> dict[str, Any]:
        assert self.settings.resumegen_root is not None
        assert self.settings.outreach_root is not None
        result: dict[str, Any] = {
            "scope": "current-snapshot",
            "consistency": "stable-at-capture" if capture else "best-effort",
            "transactional": False,
            "status": "available",
            "captured_at": captured_at,
            "application_queue": None,
            "outreach_counts": {},
            "evidence": {},
            "reasons": [],
        }
        queue_root = (
            self.settings.resumegen_root
            / "apps"
            / "Apply queues"
            / "current_apply_queue"
        )
        manifest_path = queue_root / "manifest.json"
        priority_path = queue_root / "priority_order.json"
        try:
            reader = (
                (lambda path: capture.read_bytes(path, limit=20 * 1024 * 1024))
                if capture
                else self._read_bytes
            )
            first_manifest = reader(manifest_path)
            priority_content = reader(priority_path)
            second_manifest = reader(manifest_path)
            if hashlib.sha256(first_manifest).digest() != hashlib.sha256(
                second_manifest
            ).digest():
                raise ValueError("application queue changed during capture")
            manifest = self._parse_object(second_manifest, manifest_path)
            priority = json.loads(priority_content.decode("utf-8"))
            if not isinstance(priority, list):
                raise ValueError("priority_order.json is not an array")
            status_counts = _count_field(
                priority,
                "status",
                {"queued", "generated", "review", "ready", "applied", "closed"},
            )
            bucket_counts = _count_field(
                priority,
                "queue_bucket",
                {"new", "carry", "manual", "review", "ready"},
            )
            queue_type = str(manifest.get("queue_type") or "")
            result["application_queue"] = {
                "queue_type": (
                    queue_type if queue_type == "current_apply_queue" else "unknown"
                ),
                "created_at": _safe_timestamp(manifest.get("created_at")),
                "ready_count": _safe_int(manifest.get("ready_count")),
                "manual_review_count": _safe_int(
                    manifest.get("manual_review_count")
                ),
                "priority_item_count": len(priority),
                "status_counts": status_counts,
                "bucket_counts": bucket_counts,
                "in_latest_run_count": sum(
                    1
                    for item in priority
                    if isinstance(item, dict) and item.get("in_latest_run") is True
                ),
                "source_label_count": len(
                    {
                        value
                        for value in manifest.get("sources", [])
                        if isinstance(value, str) and value
                    }
                ),
                "material_flags": _queue_material_flags(priority, queue_root),
            }
            result["evidence"]["application_queue_manifest"] = self._evidence_for_content(
                manifest_path,
                self.settings.resumegen_root,
                second_manifest,
            )
            result["evidence"]["application_priority_order"] = self._evidence_for_content(
                priority_path,
                self.settings.resumegen_root,
                priority_content,
            )
        except (ValueError, OSError, json.JSONDecodeError) as error:
            result["status"] = "partial"
            result["reasons"].append(f"Application queue unavailable: {error}")

        workspace = self.settings.outreach_root / "workspace"
        for table in (
            "organizations",
            "opportunities",
            "contacts",
            "touchpoints",
            "sources",
        ):
            path = workspace / f"{table}.csv"
            try:
                content = (
                    capture.read_bytes(path, limit=20 * 1024 * 1024)
                    if capture
                    else self._read_bytes(path)
                )
                text = content.decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                if reader.fieldnames is None:
                    raise ValueError("CSV has no header")
                result["outreach_counts"][table] = sum(1 for _ in reader)
                result["evidence"][table] = self._evidence_for_content(
                    path,
                    self.settings.outreach_root,
                    content,
                )
            except (ValueError, OSError, UnicodeDecodeError) as error:
                result["status"] = "partial"
                result["reasons"].append(f"{table} count unavailable: {error}")
        if not result["application_queue"] and not result["outreach_counts"]:
            result["status"] = "unavailable"
        return result

    def _lock_paths(self) -> dict[str, Path | None]:
        return {
            "scheduler": (
                self.settings.runtime_dir / "nightly_scheduler.lock"
                if self.settings.runtime_dir
                else None
            ),
            "pipeline": (
                self.settings.runtime_dir / "nightly_pipeline.lock"
                if self.settings.runtime_dir
                else None
            ),
            "workbook": (
                self.settings.resumegen_root / "discovery" / ".jobs.lock"
                if self.settings.resumegen_root
                else None
            ),
            "queue": (
                self.settings.resumegen_root
                / "apps"
                / "Apply queues"
                / ".current_apply_queue.lock"
                if self.settings.resumegen_root
                else None
            ),
            "adapter_mutation": self.settings.adapter_mutation_lock_path,
        }

    def _lock_states(self) -> dict[str, str]:
        return {
            name: self._lock_state(path)
            for name, path in self._lock_paths().items()
        }

    @staticmethod
    def _lock_state(path: Path | None) -> str:
        if path is None:
            return "not_configured"
        if not path.is_file():
            return "unavailable"
        try:
            with path.open("rb") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return "busy"
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return "free"
        except OSError:
            return "unavailable"

    def _scan_runs(self) -> tuple[list[dict[str, Any]], list[str]]:
        assert self.settings.resumegen_root is not None
        directory = self.settings.resumegen_root / "discovery" / "source_validation"
        if not directory.is_dir():
            return [], ["Configured resume-engine root has no source-validation directory."]
        verified: list[dict[str, Any]] = []
        rejected: list[str] = []
        for path in directory.glob("*-nightly-pipeline-summary.json"):
            match = _SUMMARY_NAME.fullmatch(path.name)
            if not match:
                rejected.append(f"Rejected non-coherent summary filename: {path.name}")
                continue
            run_id = match.group("run_id")
            try:
                verified.append(self._verify_summary(path, run_id))
            except (ValueError, OSError, json.JSONDecodeError) as error:
                rejected.append(f"{path.name}: {error}")
        verified.sort(key=lambda item: (item["_created_at_epoch"], item["run_id"]))
        for item in verified:
            item.pop("_created_at_epoch", None)
        return verified, rejected

    def _verify_summary(self, path: Path, run_id: str) -> dict[str, Any]:
        assert self.settings.resumegen_root is not None
        assert self.settings.outreach_root is not None
        summary, summary_evidence = self._read_object_with_evidence(
            path, self.settings.resumegen_root
        )
        if summary.get("run_id") != run_id:
            raise ValueError("summary run_id does not match filename")
        created_at = self._parse_time(summary.get("created_at"), "summary created_at")
        completed_at = self._parse_time(
            summary.get("completed_at"), "summary completed_at"
        )
        expected_started_at = datetime.strptime(
            run_id, "%Y%m%d-%H%M%S"
        ).replace(tzinfo=created_at.tzinfo)
        if (
            abs(created_at.timestamp() - expected_started_at.timestamp())
            > _RUN_TIME_BINDING_TOLERANCE_SECONDS
        ):
            raise ValueError(
                "summary created_at does not match the filename run timestamp"
            )
        if completed_at.timestamp() < created_at.timestamp():
            raise ValueError("summary completed_at precedes created_at")
        if summary.get("status") not in _SUMMARY_TERMINAL:
            raise ValueError("summary does not have a supported terminal status")
        failures = summary.get("failures")
        if not isinstance(failures, list) or not all(
            isinstance(item, str) for item in failures
        ):
            raise ValueError("summary failures must be a typed array")

        manifest_path = self._resolve_pointer(
            self.settings.resumegen_root,
            summary.get("daily_engine_manifest"),
            "daily_engine_manifest",
        )
        if not manifest_path.name.startswith(run_id):
            raise ValueError("manifest filename does not bind to the selected run")
        manifest, manifest_evidence = self._read_object_with_evidence(
            manifest_path, self.settings.resumegen_root
        )
        self._validate_manifest(manifest, run_id)

        source_path = self._resolve_pointer(
            self.settings.resumegen_root,
            manifest.get("source_metrics"),
            "source_metrics",
        )
        action_path = self._resolve_pointer(
            self.settings.resumegen_root,
            manifest.get("action_queue"),
            "action_queue",
        )
        source_metrics, source_evidence = self._read_object_with_evidence(
            source_path, self.settings.resumegen_root
        )
        action_queue, action_evidence = self._read_object_with_evidence(
            action_path, self.settings.resumegen_root
        )
        self._validate_source_metrics_binding(
            source_metrics,
            run_id=run_id,
            created_at=created_at,
            action_path=action_path,
        )
        _validated_action_queue_lane_counts(action_queue)

        report_pointer = summary.get("outreach_daily_report")
        if not isinstance(report_pointer, dict):
            raise ValueError("summary outreach_daily_report is missing")
        report_path = self._resolve_pointer(
            self.settings.outreach_root,
            report_pointer.get("summary_artifact"),
            "outreach_daily_report.summary_artifact",
        )
        if not report_path.name.startswith(run_id):
            raise ValueError("outreach report filename does not bind to the selected run")
        report, report_evidence = self._read_object_with_evidence(
            report_path, self.settings.outreach_root
        )
        self._validate_report(
            report,
            summary_path=path,
            created_at=summary["created_at"],
            run_id=run_id,
        )
        reporting_consistency = _source_reporting_consistency(manifest, report)

        html_evidence: dict[str, Any] | None = None
        html_pointer = report_pointer.get("html_report_artifact")
        if html_pointer:
            html_path = self._resolve_pointer(
                self.settings.outreach_root,
                html_pointer,
                "outreach_daily_report.html_report_artifact",
            )
            if not html_path.name.startswith(run_id):
                raise ValueError("historical HTML filename does not bind to the run")
            html_evidence = self._file_evidence(html_path, self.settings.outreach_root)

        source_states = {
            str(value.get("status"))
            for value in manifest["source_families"].values()
            if isinstance(value, dict)
        }
        all_scoring_failed = any(
            _source_scoring_summary(value)["all_failed"]
            for value in manifest["source_families"].values()
            if isinstance(value, dict)
        )
        report_run_status = str(report.get("run_status") or "not_reported")
        track_2_execution = report.get("track_2_execution")
        track_2_status = (
            str(track_2_execution.get("status") or "not_reported")
            if isinstance(track_2_execution, dict)
            else "not_reported"
        )
        report_requires_attention = (
            report_run_status != "completed"
            or bool(report.get("track_2_failed", False))
            or track_2_status
            in {
                "failed",
                "partial_failed",
                "timed_out",
                "cancelled",
                "not_reported",
            }
        )
        normalized_status = (
            "attention"
            if failures
            or summary["status"] == "failed"
            or all_scoring_failed
            or report_requires_attention
            or reporting_consistency["status"] == "mismatch"
            or any(_source_status_requires_attention(state) for state in source_states)
            else "complete"
        )
        return {
            "schema_version": "1.0",
            "run_id": run_id,
            "mode": "existing",
            "scope": "run-scoped",
            "status": normalized_status,
            "summary_status": summary["status"],
            "daily_engine_status": manifest["status"],
            "started_at": created_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "_created_at_epoch": created_at.timestamp(),
            "failure_count": len(failures),
            "reporting_consistency": reporting_consistency,
            "delivery_contract": self._delivery_contract(summary, manifest),
            "source_status_counts": dict(
                _count_values(
                    str(value.get("status"))
                    for value in manifest["source_families"].values()
                    if isinstance(value, dict)
                )
            ),
            "evidence": {
                "summary": summary_evidence,
                "daily_manifest": manifest_evidence,
                "source_metrics": source_evidence,
                "action_queue": action_evidence,
                "outreach_report": report_evidence,
                "outreach_html": html_evidence,
            },
        }

    def _delivery_contract(
        self,
        summary: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify requested delivery only from artifacts bound to this run."""
        assert self.settings.outreach_root is not None
        app_target = summary.get("app_queue_target_sends")
        app_requested: bool | None = None
        if isinstance(app_target, (str, int)) and not isinstance(app_target, bool):
            app_requested = str(app_target).strip().casefold() not in {
                "",
                "0",
                "false",
                "none",
            }

        track_2_requested: bool | None = None
        track_2_evidence: dict[str, Any] | None = None
        track_2 = manifest.get("track_2")
        pointer = track_2.get("run_artifact") if isinstance(track_2, dict) else None
        if isinstance(pointer, str) and pointer.strip():
            try:
                path = self._resolve_pointer(
                    self.settings.outreach_root,
                    pointer,
                    "track_2.run_artifact",
                )
                payload, track_2_evidence = self._read_object_with_evidence(
                    path, self.settings.outreach_root
                )
                if isinstance(payload.get("send_linkedin"), bool):
                    track_2_requested = payload["send_linkedin"]
            except (OSError, ValueError, json.JSONDecodeError):
                track_2_evidence = None

        if app_requested is True and track_2_requested is True:
            mode = "full_delivery"
        elif app_requested is False and track_2_requested is False:
            mode = "preparation_only"
        elif app_requested is None and track_2_requested is None:
            mode = "not_reported"
        else:
            mode = "mixed"
        return {
            "mode": mode,
            "app_queue_delivery_requested": app_requested,
            "track_2_linkedin_delivery_requested": track_2_requested,
            "email_delivery": "recipient_review_only",
            "track_2_evidence": track_2_evidence,
        }

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], run_id: str) -> None:
        if manifest.get("manifest_schema") != "resume_generator.daily_engine_run_manifest":
            raise ValueError("manifest_schema is unsupported")
        manifest_version = manifest.get("manifest_version")
        if (
            not isinstance(manifest_version, int)
            or isinstance(manifest_version, bool)
            or manifest_version != 1
        ):
            raise ValueError("manifest_version is unsupported")
        if manifest.get("run_id") != run_id:
            raise ValueError("manifest run_id does not match summary")
        if manifest.get("status") not in _MANIFEST_TERMINAL:
            raise ValueError("manifest does not have a supported terminal status")
        returncode = manifest.get("returncode")
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or returncode < 0
        ):
            raise ValueError("manifest returncode must be a non-negative integer")
        source_families = manifest.get("source_families")
        if not isinstance(source_families, dict) or not source_families:
            raise ValueError("manifest source_families is missing")
        missing_families = _EXPECTED_SOURCE_FAMILIES - set(source_families)
        if missing_families:
            raise ValueError(
                "manifest source_families omits required typed families: "
                + ", ".join(sorted(missing_families))
            )
        for name, value in source_families.items():
            if not isinstance(value, dict):
                raise ValueError(f"source family {name} is not an object")
            if value.get("status") not in _SOURCE_STATES:
                raise ValueError(f"source family {name} has an invalid status")
            raw_count = value.get("raw_count")
            kept_count = value.get("kept_count")
            if (
                not isinstance(raw_count, int)
                or isinstance(raw_count, bool)
                or raw_count < 0
                or not isinstance(kept_count, int)
                or isinstance(kept_count, bool)
                or kept_count < 0
            ):
                raise ValueError(
                    f"source family {name} lacks non-negative integer counts"
                )
        for key in _TYPED_ARRAYS:
            if not isinstance(manifest.get(key), list):
                raise ValueError(f"manifest {key} must be a typed array")
        for key in ("app_invites", "track_2", "email_channel"):
            if not isinstance(manifest.get(key), dict):
                raise ValueError(f"manifest {key} must be an object")

    def _validate_source_metrics_binding(
        self,
        source_metrics: dict[str, Any],
        *,
        run_id: str,
        created_at: datetime,
        action_path: Path,
    ) -> None:
        """Bind timestamp-named metrics and queue artifacts to one exact run.

        ResumeGenerator names these two artifacts for their generation time,
        not the nightly start time. The metrics payload carries the exact run
        id/start time and an exact pointer to the otherwise-unidentified action
        queue, so that producer relationship is the run-scoped identity chain.
        """
        assert self.settings.resumegen_root is not None
        if source_metrics.get("run_id") != run_id:
            raise ValueError("source metrics run_id does not match the selected run")
        metrics_started_at = self._parse_time(
            source_metrics.get("run_started_at"),
            "source metrics run_started_at",
        )
        if (
            abs(metrics_started_at.timestamp() - created_at.timestamp())
            > _RUN_TIME_BINDING_TOLERANCE_SECONDS
        ):
            raise ValueError("source metrics run_started_at does not match the run")
        queue_summary = source_metrics.get("action_queue")
        if not isinstance(queue_summary, dict):
            raise ValueError("source metrics action_queue binding is missing")
        bound_action_path = self._resolve_pointer(
            self.settings.resumegen_root,
            queue_summary.get("artifact"),
            "source metrics action_queue.artifact",
        )
        if bound_action_path != action_path.resolve(strict=True):
            raise ValueError("source metrics points to a different action queue")

    def _validate_report(
        self,
        report: dict[str, Any],
        *,
        summary_path: Path,
        created_at: str,
        run_id: str,
    ) -> None:
        assert self.settings.resumegen_root is not None
        if report.get("report_mode") != "run_scoped":
            raise ValueError("outreach report is not run_scoped")
        if report.get("run_id") != run_id:
            raise ValueError("outreach report run_id does not match the selected run")
        nightly_path = self._resolve_pointer(
            self.settings.resumegen_root,
            report.get("nightly_summary"),
            "report nightly_summary",
        )
        if nightly_path != summary_path.resolve(strict=True):
            raise ValueError("outreach report points to a different nightly summary")
        if report.get("since") != created_at:
            raise ValueError("outreach report since does not match the run window")
        if not isinstance(report.get("source_breakdown"), list):
            raise ValueError("outreach report source_breakdown is missing")
        for key in ("stage_metrics", "workspace_counts"):
            if not isinstance(report.get(key), dict):
                raise ValueError(f"outreach report {key} is missing")

    @staticmethod
    def _parse_time(value: Any, label: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} is missing")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Legacy engine artifacts use naive local wall time. Make that zone
            # explicit, while preserving an explicit producer timezone so a
            # run-id binds to the wall clock in which `created_at` was emitted.
            return parsed if parsed.tzinfo is not None else parsed.astimezone()
        except ValueError as error:
            raise ValueError(f"{label} is invalid") from error

    @classmethod
    def _read_object_with_evidence(
        cls,
        path: Path,
        root: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        content = cls._read_bytes(path)
        return cls._parse_object(content, path), cls._evidence_for_content(
            path, root, content
        )

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        return ExistingEngineAdapter._parse_object(
            ExistingEngineAdapter._read_bytes(path), path
        )

    @classmethod
    def _read_bound_object(
        cls,
        path: Path,
        evidence: Any,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(evidence, dict):
            raise ValueError(f"{label} evidence is unavailable")
        expected_sha256 = evidence.get("sha256")
        expected_size = evidence.get("size_bytes")
        if (
            not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise ValueError(f"{label} evidence is invalid")
        content = cls._read_bytes(path)
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            raise ValueError(f"{label} changed after verification")
        return cls._parse_object(content, path)

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        if not path.is_file():
            raise ValueError(f"artifact is not a regular file: {path.name}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError(f"artifact is too large: {path.name}")
        return path.read_bytes()

    @staticmethod
    def _parse_object(content: bytes, path: Path) -> dict[str, Any]:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"artifact has duplicate JSON key: {key}")
                result[key] = value
            return result

        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(value, dict):
            raise ValueError(f"artifact is not an object: {path.name}")
        return value

    @staticmethod
    def _file_evidence(path: Path, root: Path) -> dict[str, Any]:
        content = ExistingEngineAdapter._read_bytes(path)
        return ExistingEngineAdapter._evidence_for_content(path, root, content)

    @staticmethod
    def _evidence_for_content(path: Path, root: Path, content: bytes) -> dict[str, Any]:
        return {
            "state": "valid",
            "path": path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    @staticmethod
    def _resolve_pointer(root: Path, pointer: Any, label: str) -> Path:
        if not isinstance(pointer, str) or not pointer:
            raise ValueError(f"{label} exact pointer is missing")
        candidate = Path(pointer).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            # Path.relative_to() compares path text. On the default macOS
            # case-insensitive filesystem, two spellings such as
            # "Claude Projects" and "Claude projects" can identify the same
            # directory while still failing that textual comparison. Prove
            # containment using filesystem identity, then rebuild the path
            # with the configured root's spelling so downstream relative-path
            # evidence remains stable. A traversal or symlink escape has no
            # ancestor with the root directory's identity and still fails.
            matching_ancestor: Path | None = None
            for ancestor in (resolved, *resolved.parents):
                try:
                    if ancestor.samefile(resolved_root):
                        matching_ancestor = ancestor
                        break
                except OSError:
                    continue
            if matching_ancestor is None:
                raise ValueError(f"{label} pointer escapes its configured root")
            suffix = resolved.parts[len(matching_ancestor.parts) :]
            resolved = resolved_root.joinpath(*suffix).resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                raise ValueError(f"{label} pointer escapes its configured root")
        if resolved.name.lower().startswith("latest"):
            raise ValueError(f"{label} cannot use a latest alias")
        if any(part.lower() in {"latest", "current"} for part in resolved.parts):
            raise ValueError(f"{label} cannot use a latest/current alias")
        return resolved


def _count_values(values: Any) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items())


def _empty_progress_counts() -> dict[str, int | None]:
    return {
        "searches_completed": None,
        "searches_total": None,
        "items_discovered": None,
        "scoring_attempted": None,
        "scoring_errors": None,
        "accepted_for_write": None,
        "source_families_total": None,
        "source_families_successful": None,
        "source_families_attention": None,
        "raw_total": None,
        "kept_total": None,
        "decision_total": None,
        "pending_review_count": None,
    }


def _bounded_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 0:
        return None
    return min(number, _CURRENT_COUNT_LIMIT)


def _source_status_requires_attention(value: Any) -> bool:
    status = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value or "").strip().casefold(),
    ).strip("_")
    return (
        status
        in {
            "skipped",
            "partial",
            "failed",
            "partial_failed",
            "timed_out",
            "partial_timed_out",
            "timeout",
            "not_reported",
            "incomplete",
        }
        or "failed" in status
        or "timeout" in status
    )


def _progress_time_is_bounded(
    candidate: datetime,
    started: datetime,
    captured: datetime,
) -> bool:
    candidate_epoch = candidate.timestamp()
    return started.timestamp() <= candidate_epoch <= captured.timestamp()


def _timestamp_epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _run_id_epoch(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y%m%d-%H%M%S").timestamp()
    except (TypeError, ValueError):
        return 0.0


def _source_scoring_summary(value: dict[str, Any]) -> dict[str, Any]:
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    attempted = _bounded_count(details.get("freshly_scored_count"))
    errors = _bounded_count(details.get("error_count"))
    accepted = _bounded_count(details.get("accepted_for_write"))
    return {
        "attempted": attempted,
        "errors": errors,
        "accepted": accepted,
        "all_failed": bool(
            attempted and errors is not None and errors >= attempted
        ),
    }


def _source_reporting_consistency(
    manifest: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Compare only bounded, non-private source aggregates across exact artifacts."""
    source_families = (
        manifest.get("source_families")
        if isinstance(manifest.get("source_families"), dict)
        else {}
    )
    report_rows = (
        report.get("source_breakdown")
        if isinstance(report.get("source_breakdown"), list)
        else []
    )
    rows_by_label: dict[str, list[dict[str, Any]]] = {
        label: [] for label in _REQUIRED_REPORT_SOURCE_LABELS.values()
    }
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        label = row.get("source")
        if isinstance(label, str) and label in rows_by_label:
            rows_by_label[label].append(row)

    category_counts = {category: 0 for category in _REPORTING_MISMATCH_CATEGORIES}
    mismatch_source_count = 0
    for source_id, report_label in _REQUIRED_REPORT_SOURCE_LABELS.items():
        manifest_row = source_families.get(source_id)
        candidates = rows_by_label[report_label]
        source_mismatched = False
        if len(candidates) == 0:
            category_counts["missing_source"] += 1
            source_mismatched = True
        elif len(candidates) > 1:
            category_counts["duplicate_source"] += 1
            source_mismatched = True
        elif not isinstance(manifest_row, dict):
            # Manifest validation normally makes this unreachable. Preserve a
            # bounded mismatch instead of exposing or trusting a malformed row.
            category_counts["missing_source"] += 1
            source_mismatched = True
        else:
            report_row = candidates[0]
            manifest_status = _canonical_source_status(manifest_row.get("status"))
            report_status = _canonical_source_status(report_row.get("status"))
            if not manifest_status or manifest_status != report_status:
                category_counts["status"] += 1
                source_mismatched = True
            for manifest_field, report_field in (
                ("raw_count", "raw"),
                ("kept_count", "kept"),
            ):
                manifest_count = _bounded_count(manifest_row.get(manifest_field))
                report_count = _bounded_count(report_row.get(report_field))
                if manifest_count is None or manifest_count != report_count:
                    category_counts[report_field] += 1
                    source_mismatched = True
        if source_mismatched:
            mismatch_source_count += 1

    bounded_categories = {
        category: min(count, len(_REQUIRED_REPORT_SOURCE_LABELS))
        for category, count in category_counts.items()
        if count
    }
    mismatch_count = min(
        sum(bounded_categories.values()),
        len(_REQUIRED_REPORT_SOURCE_LABELS) * 3,
    )
    return {
        "schema_version": "1.0",
        "scope": "exact-run-cross-artifact",
        "status": "mismatch" if mismatch_count else "consistent",
        "required_source_count": len(_REQUIRED_REPORT_SOURCE_LABELS),
        "mismatch_source_count": min(
            mismatch_source_count,
            len(_REQUIRED_REPORT_SOURCE_LABELS),
        ),
        "mismatch_count": mismatch_count,
        "categories": bounded_categories,
        "compared_fields": ["status", "raw", "kept"],
    }


def _canonical_source_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")[:80]


def _phase_from_current_log(content: str) -> tuple[str, str, int]:
    selected = ("starting", "Starting run", -1)
    selected_position = -1
    for index, (phase_id, label, pattern) in enumerate(_CURRENT_PHASE_PATTERNS):
        matches = list(pattern.finditer(content))
        if matches and matches[-1].start() > selected_position:
            selected = (phase_id, label, index)
            selected_position = matches[-1].start()
    return selected


def _progress_evidence_item(
    kind: str,
    evidence: dict[str, Any],
    *,
    binding: str,
) -> dict[str, Any]:
    state = str(evidence.get("state") or "unavailable")
    path = str(evidence.get("path") or "")
    sha256 = str(evidence.get("sha256") or "")
    size = evidence.get("size_bytes")
    return {
        "kind": kind,
        "state": state,
        "path": path if len(path) <= 512 else "",
        "sha256": sha256 if re.fullmatch(r"[0-9a-f]{64}", sha256) else "",
        "size_bytes": (
            min(size, 20 * 1024 * 1024)
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0
            else 0
        ),
        "binding": binding,
    }


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _validated_action_queue_lane_counts(
    action_queue: dict[str, Any],
) -> dict[str, int]:
    counts = action_queue.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("action queue counts must be an object")
    validated: dict[str, int] = {}
    for lane in _DECISION_LANES:
        entries = action_queue.get(lane)
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            raise ValueError(
                f"action queue lane {lane} must be a list of objects"
            )
        reported = counts.get(lane)
        if (
            not isinstance(reported, int)
            or isinstance(reported, bool)
            or reported < 0
        ):
            raise ValueError(
                f"action queue lane {lane} must have a non-negative numeric count"
            )
        if reported != len(entries):
            raise ValueError(
                f"action queue lane {lane} count does not match its entries"
            )
        validated[lane] = len(entries)
    return validated


def _safe_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return _safe_int(value)


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return None


def _numeric_tree(value: Any, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict) or depth > 4:
        return {}
    result: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, bool):
            result[str(key)] = child
        elif isinstance(child, (int, float)):
            result[str(key)] = child
        elif isinstance(child, dict):
            result[str(key)] = _numeric_tree(child, depth + 1)
    return result


def _count_field(
    rows: list[Any],
    field: str,
    allowed: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = str(row.get(field) or "unknown")
        value = candidate if candidate in allowed else "other"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _safe_report_source_label(value: Any) -> str:
    candidate = str(value or "unknown")
    return candidate if candidate in _PUBLIC_REPORT_SOURCE_LABELS else "Other configured source"


def _safe_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def _queue_material_flags(
    rows: list[Any],
    queue_root: Path,
) -> dict[str, int]:
    counts = {
        "folders_resolved": 0,
        "resume_ready": 0,
        "cover_letter_ready": 0,
        "job_description_ready": 0,
        "strategy_ready": 0,
        "intel_ready": 0,
    }
    try:
        resolved_root = queue_root.resolve(strict=True)
    except OSError:
        return counts
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_folder = row.get("folder_path")
        if not isinstance(raw_folder, str) or not raw_folder:
            continue
        candidate = Path(raw_folder).expanduser()
        if not candidate.is_absolute():
            candidate = queue_root / candidate
        try:
            unresolved_relative = candidate.absolute().relative_to(
                queue_root.absolute()
            )
            cursor = queue_root.absolute()
            if cursor.is_symlink():
                continue
            unsafe = False
            for part in unresolved_relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    unsafe = True
                    break
            if unsafe:
                continue
            folder = candidate.resolve(strict=True)
        except (OSError, ValueError):
            continue
        if not folder.is_relative_to(resolved_root) or not folder.is_dir():
            continue
        counts["folders_resolved"] += 1
        names = {
            item.name.casefold()
            for item in folder.iterdir()
            if item.is_file() and not item.is_symlink()
        }
        if any(
            name.startswith("resume_")
            and name.endswith((".docx", ".pdf", ".txt"))
            for name in names
        ):
            counts["resume_ready"] += 1
        if any(
            (
                name.startswith("cl_")
                or name.startswith("cover_letter")
                or name.startswith("cover-letter")
            )
            and name.endswith((".docx", ".pdf", ".txt"))
            for name in names
        ):
            counts["cover_letter_ready"] += 1
        if "jd.txt" in names:
            counts["job_description_ready"] += 1
        if "strategy.json" in names:
            counts["strategy_ready"] += 1
        if "intel.txt" in names:
            counts["intel_ready"] += 1
    return counts

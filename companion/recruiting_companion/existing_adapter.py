from __future__ import annotations

import fcntl
import csv
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings


_SUMMARY_NAME = re.compile(
    r"^(?P<run_id>\d{8}-\d{6})-nightly-pipeline-summary\.json$"
)
_SUMMARY_TERMINAL = {"completed", "failed"}
_MANIFEST_TERMINAL = {"completed", "failed", "timed_out", "cancelled"}
_SOURCE_STATES = {
    "ran",
    "skipped",
    "failed",
    "timed_out",
    "not_reported",
    "not_configured",
    "completed",
    "failed_missing_artifact",
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
_DECISION_LANES = (
    "application_plus_outreach",
    "application_only",
    "outreach_only_today",
    "relationship_buffer",
    "follow_up",
    "skipped_internal",
)


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
                    "review ledger may expose one fixed safe-nightly action."
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

        locks = status.get("locks") if isinstance(status.get("locks"), dict) else {}
        required_lock_names = {
            "scheduler",
            "pipeline",
            "workbook",
            "queue",
            "adapter_mutation",
        }
        lock_states = {name: locks.get(name, "unavailable") for name in required_lock_names}
        if any(state == "busy" for state in lock_states.values()):
            response["current_workspace"] = {
                "scope": "current-snapshot",
                "status": "busy",
                "captured_at": generated_at,
                "reasons": [
                    "A scheduler, pipeline, workbook, queue, or adapter lock is currently owned; mutable workspace files were not read."
                ],
            }
        elif status.get("roots_available") and all(
            state == "free" for state in lock_states.values()
        ):
            response["current_workspace"] = self._current_workspace_snapshot(
                captured_at=generated_at
            )
        else:
            response["current_workspace"]["reasons"] = [
                "Mutable workspace capture requires scheduler, pipeline, workbook, queue, and adapter locks to all be explicitly free."
            ]
            response["current_workspace"]["lock_states"] = lock_states
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

    def lock_states(self) -> dict[str, str]:
        return self._lock_states()

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
        manifest = self._read_object(manifest_path)
        action_queue = self._read_object(action_path)
        report = self._read_object(report_path)
        sources = []
        for name in sorted(_EXPECTED_SOURCE_FAMILIES):
            value = manifest.get("source_families", {}).get(name, {})
            if not isinstance(value, dict):
                continue
            sources.append(
                {
                    "source": str(name),
                    "status": str(value.get("status") or "not_reported"),
                    "raw_count": _safe_int(value.get("raw_count")),
                    "kept_count": _safe_int(value.get("kept_count")),
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
        decision_parts = {
            lane: _decision_count(queue_counts.get(lane)) for lane in _DECISION_LANES
        }
        return {
            "scope": "run-scoped",
            "status": run["status"],
            "run_id": run["run_id"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "failure_count": _safe_int(run.get("failure_count")),
            "daily_engine": {
                "status": manifest.get("status"),
                "returncode": manifest.get("returncode"),
            },
            "sources": sources,
            "queue": {
                "counts": queue_counts,
                "decision_total": sum(decision_parts.values()),
                "decision_total_name": "mutually_exclusive_decision_lanes",
                "decision_total_parts": decision_parts,
            },
            "report": {
                "status": "valid",
                "sources": report_sources,
                "stage_metrics": stage_metrics,
                "workspace_counts": _numeric_tree(report.get("workspace_counts", {})),
                "invite_totals": _numeric_tree(report.get("invite_totals", {})),
                "pending_review_count": _safe_int(report.get("pending_review_count")),
                "track_2_returncode": _safe_int_or_none(
                    report.get("track_2_returncode")
                ),
                "track_2_failed": bool(report.get("track_2_failed", False)),
            },
            "evidence": evidence,
        }

    def _current_workspace_snapshot(self, *, captured_at: str) -> dict[str, Any]:
        assert self.settings.resumegen_root is not None
        assert self.settings.outreach_root is not None
        result: dict[str, Any] = {
            "scope": "current-snapshot",
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
            first_manifest = self._read_bytes(manifest_path)
            priority_content = self._read_bytes(priority_path)
            second_manifest = self._read_bytes(manifest_path)
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
                content = self._read_bytes(path)
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

    def _lock_states(self) -> dict[str, str]:
        paths: dict[str, Path | None] = {
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
        return {name: self._lock_state(path) for name, path in paths.items()}

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
        if created_at.strftime("%Y%m%d") != run_id[:8]:
            raise ValueError("summary date does not match filename run date")
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
        # Parse to prove the exact pointers are readable JSON objects. Upstream
        # source/action artifacts do not currently promise their own run_id/schema.
        _, source_evidence = self._read_object_with_evidence(
            source_path, self.settings.resumegen_root
        )
        _, action_evidence = self._read_object_with_evidence(
            action_path, self.settings.resumegen_root
        )

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
        normalized_status = (
            "attention"
            if failures
            or summary["status"] == "failed"
            or source_states.intersection({"skipped", "failed", "timed_out", "not_reported"})
            or any(state.startswith("failed") for state in source_states)
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

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], run_id: str) -> None:
        if manifest.get("manifest_schema") != "resume_generator.daily_engine_run_manifest":
            raise ValueError("manifest_schema is unsupported")
        if manifest.get("manifest_version") != 1:
            raise ValueError("manifest_version is unsupported")
        if manifest.get("run_id") != run_id:
            raise ValueError("manifest run_id does not match summary")
        if manifest.get("status") not in _MANIFEST_TERMINAL:
            raise ValueError("manifest does not have a supported terminal status")
        if not isinstance(manifest.get("returncode"), int):
            raise ValueError("manifest returncode must be numeric")
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
            if not isinstance(value.get("raw_count"), int) or not isinstance(
                value.get("kept_count"), int
            ):
                raise ValueError(f"source family {name} lacks numeric counts")
        for key in _TYPED_ARRAYS:
            if not isinstance(manifest.get(key), list):
                raise ValueError(f"manifest {key} must be a typed array")
        for key in ("app_invites", "track_2", "email_channel"):
            if not isinstance(manifest.get(key), dict):
                raise ValueError(f"manifest {key} must be an object")

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
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _decision_count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    return 0


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

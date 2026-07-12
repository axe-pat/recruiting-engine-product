from __future__ import annotations

import json
import os
import fcntl
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from recruiting_companion.config import Settings
from recruiting_companion.existing_adapter import (
    ExistingEngineAdapter,
    MutableSnapshotChanged,
    _source_scoring_summary,
    _source_status_requires_attention,
    _timestamp_epoch,
)
from recruiting_companion.operator_backend import (
    OperatorBackend,
    _account_tracker_projection,
)


_PRIVATE = "PRIVATE-CARD-URL-CONTACT-OR-LOG-LINE"
_FREE_LOCKS = {
    "scheduler": "free",
    "pipeline": "free",
    "workbook": "free",
    "queue": "free",
    "adapter_mutation": "free",
}
_BUSY_LOCKS = {**_FREE_LOCKS, "scheduler": "busy", "pipeline": "busy", "adapter_mutation": "busy"}


def _mutable_capture_adapter(root: Path) -> ExistingEngineAdapter:
    runtime = root / "runtime"
    resume = root / "resume"
    outreach = root / "outreach"
    runtime.mkdir(parents=True)
    (resume / "discovery").mkdir(parents=True)
    (resume / "apps/Apply queues").mkdir(parents=True)
    outreach.mkdir(parents=True)
    for path in (
        runtime / "nightly_scheduler.lock",
        runtime / "nightly_pipeline.lock",
        runtime / "operator_mutation.lock",
        resume / "discovery/.jobs.lock",
        resume / "apps/Apply queues/.current_apply_queue.lock",
    ):
        path.touch()
    return ExistingEngineAdapter(
        Settings(
            data_dir=root / "data",
            resumegen_root=resume,
            outreach_root=outreach,
            runtime_dir=runtime,
        )
    )


def _verified_run(*, status: str = "attention") -> dict[str, object]:
    run_id = "20260712-010001"
    return {
        "scope": "run-scoped",
        "status": status,
        "run_id": run_id,
        "started_at": "2026-07-12T01:00:01+05:30",
        "completed_at": "2026-07-12T02:00:01+05:30",
        "failure_count": 2 if status == "attention" else 0,
        "sources": [
            {
                "source": "linkedin",
                "status": "timed_out" if status == "attention" else "ran",
                "raw_count": 28,
                "kept_count": 4,
            },
            {
                "source": "track_2",
                "status": "ran",
                "raw_count": 3,
                "kept_count": 3,
            },
        ],
        "queue": {
            "decision_total": 6,
            "decision_total_parts": {
                "application_plus_outreach": 2,
                "application_only": 1,
                "outreach_only_today": 0,
                "relationship_buffer": 1,
                "follow_up": 2,
                "skipped_internal": 0,
            },
        },
        "report": {
            "run_status": "failed_or_incomplete" if status == "attention" else "completed",
            "track_2_status": "completed",
            "pending_review_count": 3,
            "workspace_counts": {},
            "invite_totals": {},
        },
        "delivery_contract": {"mode": "full_delivery"},
        "evidence": {
            key: {
                "state": "valid",
                "path": f"evidence/{run_id}-{key}.json",
                "sha256": character * 64,
                "size_bytes": 100,
            }
            for key, character in (
                ("summary", "a"),
                ("daily_manifest", "b"),
                ("source_metrics", "c"),
                ("action_queue", "d"),
                ("outreach_report", "e"),
            )
        },
    }


def _typed_manifest() -> dict[str, object]:
    return {
        "manifest_schema": "resume_generator.daily_engine_run_manifest",
        "manifest_version": 1,
        "run_id": "20260712-010001",
        "status": "completed",
        "returncode": 0,
        "source_families": {
            name: {"status": "ran", "raw_count": 1, "kept_count": 1}
            for name in (
                "linkedin",
                "handshake",
                "jobspy",
                "startup_sources",
                "resume_generator_app_queue",
                "track_2",
            )
        },
        **{
            name: []
            for name in (
                "invite_send_artifacts",
                "linkedin_followup_draft_artifacts",
                "linkedin_followup_send_artifacts",
                "linkedin_reconcile_artifacts",
                "track_2_daily_run_artifacts",
                "track_2_phase_artifacts",
                "track_2_phase_results",
                "track_2_email_draft_artifacts",
                "track_2_email_send_artifacts",
            )
        },
        "app_invites": {},
        "track_2": {},
        "email_channel": {},
    }


def _write_attention_terminal_chain(root: Path) -> tuple[Settings, dict[str, Path]]:
    run_id = "20260713-010001"
    created_at = "2026-07-13T01:00:01+05:30"
    resume = root / "resume"
    outreach = root / "outreach"
    validation = resume / "discovery/source_validation"
    reports = outreach / "workspace/reports"
    validation.mkdir(parents=True)
    reports.mkdir(parents=True)
    attestation = root / "production_release.json"
    attestation.write_text("{}", encoding="utf-8")
    action_path = validation / "20260713-013214-daily-action-queue.json"
    action_queue = {
        "counts": {
            "application_plus_outreach": 1,
            "application_only": 0,
            "outreach_only_today": 0,
            "relationship_buffer": 0,
            "follow_up": 2,
            "skipped_internal": 0,
        },
        "application_plus_outreach": [{}],
        "application_only": [],
        "outreach_only_today": [],
        "relationship_buffer": [],
        "follow_up": [{}, {}],
        "skipped_internal": [],
    }
    action_path.write_text(json.dumps(action_queue), encoding="utf-8")
    source_path = validation / "20260713-013214-source-run-metrics.json"
    source_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "run_started_at": created_at,
                "action_queue": {"artifact": str(action_path)},
                "sources": {},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = validation / f"{run_id}-daily-engine-run-manifest.json"
    manifest = _typed_manifest()
    manifest.update(
        {
            "run_id": run_id,
            "source_metrics": str(source_path),
            "action_queue": str(action_path),
        }
    )
    manifest["source_families"]["linkedin"] = {
        "status": "failed_scoring",
        "raw_count": 28,
        "kept_count": 0,
        "details": {
            "freshly_scored_count": 21,
            "error_count": 21,
            "accepted_for_write": 0,
        },
    }
    manifest["source_families"]["track_2"] = {
        "status": "partial_failed",
        "raw_count": 55,
        "kept_count": 39,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary_path = validation / f"{run_id}-nightly-pipeline-summary.json"
    report_path = reports / f"{run_id}-daily-run-report.json"
    report_path.write_text(
        json.dumps(
            {
                "report_mode": "run_scoped",
                "run_id": run_id,
                "nightly_summary": str(summary_path),
                "since": created_at,
                "source_breakdown": [],
                "stage_metrics": {},
                "workspace_counts": {"contacts": 4},
                "invite_totals": {"sent": 5},
                "pending_review_count": 3,
                "track_2_returncode": 1,
                "track_2_failed": True,
                "track_2_execution": {"status": "partial_failed"},
                "run_status": "failed_or_incomplete",
            }
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": created_at,
                "completed_at": "2026-07-13T03:04:12+05:30",
                "status": "failed",
                "failures": ["linkedin_scoring", "track_2"],
                "daily_engine_manifest": str(manifest_path),
                "outreach_daily_report": {
                    "returncode": 0,
                    "summary_artifact": str(report_path),
                },
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=root / "data",
        resumegen_root=resume,
        outreach_root=outreach,
        attestation_path=attestation,
    )
    settings.prepare()
    return settings, {
        "source": source_path,
        "action": action_path,
        "manifest": manifest_path,
        "summary": summary_path,
        "report": report_path,
    }


class ManifestValidationTestCase(unittest.TestCase):
    def test_current_producer_source_terminal_states_are_accepted_as_attention(self) -> None:
        for status in (
            "partial",
            "partial_failed",
            "incomplete",
            "failed_missing_scored_artifact",
            "failed_invalid_scored_artifact",
            "failed_scoring",
            "partial_failed_scoring",
        ):
            with self.subTest(status=status):
                manifest = _typed_manifest()
                manifest["source_families"]["linkedin"]["status"] = status
                ExistingEngineAdapter._validate_manifest(
                    manifest,
                    "20260712-010001",
                )
                self.assertTrue(_source_status_requires_attention(status))

    def test_boolean_and_negative_numeric_fields_fail_closed(self) -> None:
        for field, value in (
            ("manifest_version", True),
            ("returncode", True),
            ("returncode", -1),
        ):
            with self.subTest(field=field, value=value):
                manifest = _typed_manifest()
                manifest[field] = value
                with self.assertRaises(ValueError):
                    ExistingEngineAdapter._validate_manifest(
                        manifest,
                        "20260712-010001",
                    )

    def test_failed_run_with_supported_source_attention_states_is_exactly_projected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings, _ = _write_attention_terminal_chain(Path(temporary))
            adapter = ExistingEngineAdapter(settings)

            projections = adapter.verified_run_projections()

            self.assertEqual(len(projections), 1)
            run = projections[0]
            self.assertEqual(run["run_id"], "20260713-010001")
            self.assertEqual(run["status"], "attention")
            self.assertEqual(run["queue"]["decision_total"], 3)
            self.assertEqual(run["report"]["pending_review_count"], 3)
            sources = {item["source"]: item for item in run["sources"]}
            self.assertEqual(sources["linkedin"]["status"], "failed_all_scoring")
            self.assertEqual(sources["linkedin"]["reported_status"], "failed_scoring")
            self.assertEqual(sources["linkedin"]["scoring_attempted"], 21)
            self.assertEqual(sources["linkedin"]["scoring_errors"], 21)
            self.assertEqual(sources["track_2"]["status"], "partial_failed")

            backend = OperatorBackend(settings)
            progress = adapter.run_progress(
                verified_runs=projections,
                locks=_FREE_LOCKS,
            )
            plan = backend._next_run_plan(
                projections,
                current_progress=progress,
                review_queue={"review_counts": {}},
            )
            ids = {item["id"] for item in plan["items"]}
            self.assertEqual(plan["basis_run_id"], "20260713-010001")
            self.assertIn("source:linkedin", ids)
            self.assertIn("source:track_2", ids)

    def test_manifest_report_source_mismatches_are_bounded_attention_and_plan_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings, paths = _write_attention_terminal_chain(Path(temporary))
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            manifest["source_families"]["resume_generator_app_queue"] = {
                "status": "ran",
                "raw_count": 5,
                "kept_count": 5,
            }
            paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            report["source_breakdown"] = [
                {
                    "source": "LinkedIn",
                    "status": "failed_scoring",
                    "raw": 28,
                    "kept": 0,
                },
                {"source": "Handshake", "status": "ran", "raw": 1, "kept": 1},
                {"source": "JobSpy", "status": "ran", "raw": 1, "kept": 1},
                {
                    "source": "Startup sources",
                    "status": "ran",
                    "raw": 1,
                    "kept": 1,
                },
                {
                    "source": "ResumeGenerator / app queue",
                    "status": "ran",
                    "raw": 0,
                    "kept": 0,
                },
                {
                    "source": "Track 2 imports / maintenance",
                    "status": "ran",
                    "raw": 55,
                    "kept": 39,
                },
            ]
            paths["report"].write_text(json.dumps(report), encoding="utf-8")

            adapter = ExistingEngineAdapter(settings)
            projections = adapter.verified_run_projections()

            self.assertEqual(len(projections), 1)
            run = projections[0]
            self.assertEqual(run["status"], "attention")
            consistency = run["reporting_consistency"]
            self.assertEqual(consistency["status"], "mismatch")
            self.assertEqual(consistency["mismatch_source_count"], 2)
            self.assertEqual(consistency["mismatch_count"], 3)
            self.assertEqual(
                consistency["categories"],
                {"status": 1, "raw": 1, "kept": 1},
            )
            serialized = json.dumps(consistency)
            self.assertNotIn("ResumeGenerator", serialized)
            self.assertNotIn("Track 2", serialized)

            backend = OperatorBackend(settings)
            progress = adapter.run_progress(
                verified_runs=projections,
                locks=_FREE_LOCKS,
            )
            plan = backend._next_run_plan(
                projections,
                current_progress=progress,
                review_queue={"review_counts": {}},
            )
            consistency_items = [
                item
                for item in plan["items"]
                if item["id"] == "run:reporting_consistency"
            ]
            self.assertEqual(len(consistency_items), 1)
            self.assertEqual(consistency_items[0]["count"], 3)
            self.assertEqual(
                consistency_items[0]["evidence"]["kind"],
                "exact_cross_artifact_source_consistency",
            )
            reports = backend._report_assets(projections, items_total=1)
            self.assertEqual(
                reports["items"][0]["reporting_consistency"]["categories"],
                {"kept": 1, "raw": 1, "status": 1},
            )

    def test_timestamp_named_source_and_action_artifacts_are_run_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings, paths = _write_attention_terminal_chain(Path(temporary))
            adapter = ExistingEngineAdapter(settings)
            source = json.loads(paths["source"].read_text(encoding="utf-8"))

            source["run_id"] = "20260712-010001"
            paths["source"].write_text(json.dumps(source), encoding="utf-8")
            status = adapter.status()
            self.assertEqual(status["verified_run_count"], 0)
            self.assertTrue(
                any("source metrics run_id" in item for item in status["rejections"])
            )

            source["run_id"] = "20260713-010001"
            alternate_action = paths["action"].with_name(
                "20260712-013214-daily-action-queue.json"
            )
            alternate_action.write_text(
                paths["action"].read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            source["action_queue"]["artifact"] = str(alternate_action)
            paths["source"].write_text(json.dumps(source), encoding="utf-8")
            status = adapter.status()
            self.assertEqual(status["verified_run_count"], 0)
            self.assertTrue(
                any("different action queue" in item for item in status["rejections"])
            )

            latest_source = paths["source"].with_name("latest-source-run-metrics.json")
            latest_source.write_text(json.dumps(source), encoding="utf-8")
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            manifest["source_metrics"] = str(latest_source)
            paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
            status = adapter.status()
            self.assertEqual(status["verified_run_count"], 0)
            self.assertTrue(
                any("latest alias" in item for item in status["rejections"])
            )
        for field, value in (("raw_count", True), ("kept_count", -1)):
            with self.subTest(field=field, value=value):
                manifest = _typed_manifest()
                manifest["source_families"]["linkedin"][field] = value
                with self.assertRaises(ValueError):
                    ExistingEngineAdapter._validate_manifest(
                        manifest,
                        "20260712-010001",
                    )


class MutableSnapshotCaptureTestCase(unittest.TestCase):
    def test_capture_never_owns_an_upstream_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            adapter = _mutable_capture_adapter(Path(temporary))
            paths = adapter._lock_paths()
            with adapter.mutable_snapshot_capture():
                # Every upstream writer uses a nonblocking exclusive lock. A UI
                # refresh must never make that acquisition fail.
                for name in (
                    "scheduler",
                    "pipeline",
                    "workbook",
                    "queue",
                    "adapter_mutation",
                ):
                    path = paths[name]
                    assert path is not None
                    with path.open("rb") as contender:
                        fcntl.flock(
                            contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)

    def test_capture_rejects_a_file_changed_before_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = _mutable_capture_adapter(root)
            artifact = root / "resume/discovery/current.json"
            artifact.write_text('{"generation": 1}', encoding="utf-8")
            with self.assertRaises(MutableSnapshotChanged):
                with adapter.mutable_snapshot_capture() as capture:
                    capture.read_bytes(artifact, limit=1024)
                    artifact.write_text('{"generation": 2}', encoding="utf-8")


class CurrentRunProgressTestCase(unittest.TestCase):
    def test_completed_projection_rejects_artifact_changed_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "20260712-010001-action-queue.json"
            path.write_text('{"counts": {}}', encoding="utf-8")
            evidence = ExistingEngineAdapter._file_evidence(path, path.parent)
            path.write_text('{"counts": {"follow_up": 1}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after verification"):
                ExistingEngineAdapter._read_bound_object(
                    path,
                    evidence,
                    "verified action queue",
                )

    def test_all_scoring_error_summary_is_attention_evidence(self) -> None:
        self.assertEqual(
            _source_scoring_summary(
                {
                    "status": "ran",
                    "details": {
                        "freshly_scored_count": 21,
                        "error_count": 21,
                        "accepted_for_write": 0,
                        "private_error": _PRIVATE,
                    },
                }
            ),
            {
                "attempted": 21,
                "errors": 21,
                "accepted": 0,
                "all_failed": True,
            },
        )

    def test_partial_failures_and_timeout_variants_require_attention(self) -> None:
        for status in (
            "partial_failed",
            "partial-failed",
            "partial failed scoring",
            "timed_out",
            "timed-out",
            "partial timed out",
            "timeout",
        ):
            with self.subTest(status=status):
                self.assertTrue(_source_status_requires_attention(status))
        for status in ("ran", "completed", "not_configured"):
            with self.subTest(status=status):
                self.assertFalse(_source_status_requires_attention(status))

    def test_terminal_summary_run_id_binds_full_local_timestamp(self) -> None:
        run_id = "20260712-010001"
        local_expected = datetime.strptime(run_id, "%Y%m%d-%H%M%S").astimezone()
        alternate_zone = (
            timezone(timedelta(hours=12))
            if local_expected.utcoffset() != timedelta(hours=12)
            else timezone(timedelta(hours=-12))
        )
        cases = {
            "same_day_wrong_time": "2026-07-12T02:00:01",
            # Even the same instant is not a valid filename binding when its
            # explicit producer timezone gives it a different wall-clock id.
            "same_instant_wrong_wall_time": local_expected.astimezone(
                alternate_zone
            ).isoformat(),
        }
        for case, created_at in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                resume = root / "resume"
                outreach = root / "outreach"
                resume.mkdir()
                outreach.mkdir()
                path = resume / f"{run_id}-nightly-pipeline-summary.json"
                path.write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "created_at": created_at,
                            "completed_at": "2026-07-12T03:00:01",
                            "status": "completed",
                            "failures": [],
                        }
                    ),
                    encoding="utf-8",
                )
                adapter = ExistingEngineAdapter(
                    Settings(
                        data_dir=root / "data",
                        resumegen_root=resume,
                        outreach_root=outreach,
                    )
                )
                with self.assertRaisesRegex(
                    ValueError, "does not match the filename run timestamp"
                ):
                    adapter._verify_summary(path, run_id)

    def test_terminal_summary_completion_cannot_precede_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            outreach = root / "outreach"
            resume.mkdir()
            outreach.mkdir()
            run_id = "20260712-010001"
            path = resume / f"{run_id}-nightly-pipeline-summary.json"
            path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "created_at": "2026-07-12T01:00:01",
                        "completed_at": "2026-07-12T00:59:59",
                        "status": "completed",
                        "failures": [],
                    }
                ),
                encoding="utf-8",
            )
            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resume,
                    outreach_root=outreach,
                )
            )
            with self.assertRaisesRegex(ValueError, "precedes created_at"):
                adapter._verify_summary(path, run_id)

    def test_active_progress_omits_unbound_checkpoint_but_keeps_exact_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            runtime = home / "Library" / "Application Support" / "ResumeGenerator"
            logs = home / "Library" / "Logs" / "ResumeGenerator"
            resume = root / "resume"
            outreach = root / "outreach"
            runtime.mkdir(parents=True)
            logs.mkdir(parents=True)
            progress_dir = resume / "discovery" / "auto" / "logs"
            progress_dir.mkdir(parents=True)
            outreach.mkdir()

            started_at = "2026-07-13T01:00:01"
            run_id = "20260713-010001"
            (runtime / "nightly_scheduler_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_date": "2026-07-13",
                        "last_attempt_started_at": started_at,
                        "private": _PRIVATE,
                    }
                ),
                encoding="utf-8",
            )
            scored = progress_dir / "linkedin_live_scored_2026-07-13_010900.json"
            scored.write_text(
                json.dumps(
                    {
                        "scored": 2,
                        "reviewed": 3,
                        "cache_skipped": 1,
                        "accepted_for_write": 0,
                        "jobs": [
                            {"status": "new", "decision": "Error", "url": _PRIVATE},
                            {"status": "new", "decision": "Error", "url": _PRIVATE},
                            {
                                "status": "cached_skip",
                                "decision": "Reject",
                                "url": _PRIVATE,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            scored_epoch = datetime.fromisoformat("2026-07-13T01:09:00").timestamp()
            os.utime(scored, (scored_epoch, scored_epoch))
            (logs / f"nightly_pipeline_{run_id}.log").write_text(
                "\n".join(
                    (
                        f"raw private output {_PRIVATE}",
                        f"$ python discovery/scripts/run_daily_engine.py --run-id {run_id}",
                        f"$ python discovery/auto/linkedin_live.py --search {_PRIVATE}",
                        f"Scored artifact: {scored}",
                    )
                ),
                encoding="utf-8",
            )
            checkpoint = progress_dir / (
                "linkedin_live_progress_2026-07-13_010040_past-24h.json"
            )
            checkpoint.write_text(
                json.dumps(
                    {
                        "run_stamp": "2026-07-13_010040",
                        "status": "repairing",
                        "started_at": "2026-07-13T01:00:40",
                        "last_progress_at": "2026-07-13T01:08:54",
                        "searches_completed": 2,
                        "searches": [_PRIVATE, _PRIVATE],
                        "total_extracted": 28,
                        "cards": [{"url": _PRIVATE}],
                    }
                ),
                encoding="utf-8",
            )
            checkpoint_epoch = datetime.fromisoformat(
                "2026-07-13T01:08:54"
            ).timestamp()
            os.utime(checkpoint, (checkpoint_epoch, checkpoint_epoch))

            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resume,
                    outreach_root=outreach,
                    runtime_dir=runtime,
                )
            )
            with patch.object(adapter, "_lock_states", return_value=_BUSY_LOCKS):
                progress = adapter.run_progress()

            self.assertEqual(progress["status"], "attention")
            self.assertEqual(progress["selection"], "current")
            self.assertEqual(progress["run_id"], run_id)
            self.assertEqual(progress["phase"]["id"], "linkedin_discovery")
            self.assertEqual(progress["phase"]["status"], "running")
            self.assertIsNone(progress["counts"]["searches_completed"])
            self.assertIsNone(progress["counts"]["items_discovered"])
            self.assertEqual(progress["counts"]["scoring_attempted"], 2)
            self.assertEqual(progress["counts"]["scoring_errors"], 2)
            self.assertEqual(progress["counts"]["accepted_for_write"], 0)
            self.assertIn("all 2 fresh scoring attempts", progress["reason"])
            self.assertEqual(
                {item["kind"] for item in progress["evidence"]},
                {
                    "scheduler_state",
                    "run_log",
                    "linkedin_scored",
                },
            )
            self.assertNotIn(_PRIVATE, json.dumps(progress))

    def test_busy_projection_fails_closed_on_mismatched_log_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "home/Library/Application Support/ResumeGenerator"
            logs = root / "home/Library/Logs/ResumeGenerator"
            resume = root / "resume"
            outreach = root / "outreach"
            runtime.mkdir(parents=True)
            logs.mkdir(parents=True)
            resume.mkdir()
            outreach.mkdir()
            (runtime / "nightly_scheduler_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_date": "2026-07-13",
                        "last_attempt_started_at": "2026-07-13T01:00:01",
                    }
                ),
                encoding="utf-8",
            )
            (logs / "nightly_pipeline_20260713-010001.log").write_text(
                "$ python run_daily_engine.py --run-id 20260713-010101",
                encoding="utf-8",
            )
            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resume,
                    outreach_root=outreach,
                    runtime_dir=runtime,
                )
            )
            with patch.object(adapter, "_lock_states", return_value=_BUSY_LOCKS):
                progress = adapter.run_progress()

            self.assertEqual(progress["status"], "partial")
            self.assertEqual(progress["phase"]["id"], "starting")
            self.assertIn("Current run log unavailable", progress["reason"])
            self.assertEqual(
                [item["kind"] for item in progress["evidence"]],
                ["scheduler_state"],
            )

    def test_missing_and_ambiguous_active_logs_are_partial(self) -> None:
        for case in ("missing", "ambiguous"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / "home/Library/Application Support/ResumeGenerator"
                logs = root / "home/Library/Logs/ResumeGenerator"
                resume = root / "resume"
                outreach = root / "outreach"
                runtime.mkdir(parents=True)
                logs.mkdir(parents=True)
                resume.mkdir()
                outreach.mkdir()
                (runtime / "nightly_scheduler_state.json").write_text(
                    json.dumps(
                        {
                            "last_attempt_date": "2026-07-13",
                            "last_attempt_started_at": "2026-07-13T01:00:01",
                        }
                    ),
                    encoding="utf-8",
                )
                if case == "ambiguous":
                    for run_id in ("20260713-005958", "20260713-010004"):
                        (logs / f"nightly_pipeline_{run_id}.log").write_text(
                            "$ python discovery/scripts/run_daily_engine.py",
                            encoding="utf-8",
                        )
                adapter = ExistingEngineAdapter(
                    Settings(
                        data_dir=root / "data",
                        resumegen_root=resume,
                        outreach_root=outreach,
                        runtime_dir=runtime,
                    )
                )
                with patch.object(adapter, "_lock_states", return_value=_BUSY_LOCKS):
                    progress = adapter.run_progress()

                self.assertEqual(progress["status"], "partial")
                self.assertEqual(progress["phase"]["id"], "starting")
                self.assertEqual(
                    [item["kind"] for item in progress["evidence"]],
                    ["scheduler_state"],
                )

    def test_active_phase_time_uses_the_captured_log_prefix_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "home/Library/Application Support/ResumeGenerator"
            logs = root / "home/Library/Logs/ResumeGenerator"
            resume = root / "resume"
            outreach = root / "outreach"
            runtime.mkdir(parents=True)
            logs.mkdir(parents=True)
            resume.mkdir()
            outreach.mkdir()
            run_id = "20260713-010001"
            (runtime / "nightly_scheduler_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_date": "2026-07-13",
                        "last_attempt_started_at": "2026-07-13T01:00:01",
                    }
                ),
                encoding="utf-8",
            )
            log = logs / f"nightly_pipeline_{run_id}.log"
            log.write_text(
                f"$ python discovery/scripts/run_daily_engine.py --run-id {run_id}",
                encoding="utf-8",
            )
            later_epoch = datetime.fromisoformat("2026-07-13T01:09:00").timestamp()
            os.utime(log, (later_epoch, later_epoch))
            prefix_epoch = datetime.fromisoformat("2026-07-13T01:05:00").timestamp()
            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resume,
                    outreach_root=outreach,
                    runtime_dir=runtime,
                )
            )
            with (
                patch.object(adapter, "_lock_states", return_value=_BUSY_LOCKS),
                patch.object(
                    adapter,
                    "_read_append_snapshot",
                    return_value=(log.read_bytes(), prefix_epoch),
                ),
            ):
                progress = adapter.run_progress()

            self.assertEqual(progress["status"], "running")
            self.assertEqual(
                _timestamp_epoch(progress["timestamps"]["last_progress_at"]),
                prefix_epoch,
            )

    def test_exact_parent_run_checkpoint_can_contribute_aggregate_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            progress_dir = resume / "discovery/auto/logs"
            progress_dir.mkdir(parents=True)
            run_id = "20260713-010001"
            checkpoint = progress_dir / (
                "linkedin_live_progress_2026-07-13_010040_past-24h.json"
            )
            checkpoint.write_text(
                json.dumps(
                    {
                        "parent_run_id": run_id,
                        "run_stamp": "2026-07-13_010040",
                        "started_at": "2026-07-13T01:00:40",
                        "searches_completed": 2,
                        "total_extracted": 28,
                        "cards": [{"url": _PRIVATE}],
                    }
                ),
                encoding="utf-8",
            )
            modified = datetime.fromisoformat("2026-07-13T01:08:54").timestamp()
            os.utime(checkpoint, (modified, modified))
            adapter = ExistingEngineAdapter(
                Settings(data_dir=root / "data", resumegen_root=resume)
            )
            result = adapter._live_progress_checkpoint(
                datetime.fromisoformat("2026-07-13T01:00:01").astimezone(),
                run_id=run_id,
                captured=datetime.fromisoformat(
                    "2026-07-13T01:10:00"
                ).astimezone(),
            )

            self.assertIsNotNone(result)
            assert result is not None
            payload, evidence = result
            self.assertEqual(payload["searches_completed"], 2)
            self.assertEqual(evidence["binding"], "exact_active_parent_run_id")
            self.assertNotIn(_PRIVATE, json.dumps(evidence))

    def test_idle_progress_uses_only_most_recent_verified_projection(self) -> None:
        adapter = ExistingEngineAdapter(Settings(data_dir=Path("/tmp/progress-test")))
        progress = adapter.run_progress(
            verified_runs=[_verified_run(status="complete")],
            locks=_FREE_LOCKS,
        )
        self.assertEqual(progress["selection"], "most_recent_verified")
        self.assertEqual(progress["status"], "complete")
        self.assertEqual(progress["phase"]["id"], "completed")
        self.assertEqual(progress["counts"]["raw_total"], 31)
        self.assertTrue(
            all(item["binding"] == "exact_terminal_run" for item in progress["evidence"])
        )

    def test_newer_completed_scheduler_attempt_without_exact_chain_is_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            resume = root / "resume"
            outreach = root / "outreach"
            runtime.mkdir()
            resume.mkdir()
            outreach.mkdir()
            completed = datetime.now().astimezone() - timedelta(minutes=1)
            started = completed - timedelta(minutes=10)
            run_id = started.strftime("%Y%m%d-%H%M%S")
            date_key = started.strftime("%Y-%m-%d")
            (runtime / "nightly_scheduler_state.json").write_text(
                json.dumps(
                    {
                        "last_attempt_date": date_key,
                        "last_attempt_started_at": started.isoformat(),
                        "last_run_date": date_key,
                        "last_run_completed_at": completed.isoformat(),
                        "last_run_exit_code": 1,
                        "last_run_status": "failed_missing_summary",
                        "last_run_was_actual_pipeline": True,
                        "private_failure": _PRIVATE,
                    }
                ),
                encoding="utf-8",
            )
            prior = _verified_run(status="complete")
            prior["run_id"] = (started - timedelta(days=1)).strftime(
                "%Y%m%d-%H%M%S"
            )
            prior["started_at"] = (started - timedelta(days=1)).isoformat()
            prior["completed_at"] = (completed - timedelta(days=1)).isoformat()
            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resume,
                    outreach_root=outreach,
                    runtime_dir=runtime,
                )
            )
            progress = adapter.run_progress(
                verified_runs=[prior],
                locks=_FREE_LOCKS,
            )

            self.assertEqual(progress["status"], "attention")
            self.assertFalse(progress["is_current"])
            self.assertEqual(progress["selection"], "latest_scheduler_attempt")
            self.assertEqual(progress["run_id"], run_id)
            self.assertEqual(progress["phase"]["id"], "terminal_evidence_incomplete")
            self.assertIn("nonzero exit", progress["reason"])
            self.assertEqual(
                [(item["kind"], item["binding"]) for item in progress["evidence"]],
                [("scheduler_state", "exact_terminal_scheduler_attempt")],
            )
            self.assertNotIn(_PRIVATE, json.dumps(progress))

    def test_missing_or_malformed_latest_report_never_leaks_rejection_text(self) -> None:
        for case in ("missing", "malformed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / "runtime"
                validation = root / "resume/discovery/source_validation"
                outreach = root / "outreach"
                runtime.mkdir()
                validation.mkdir(parents=True)
                outreach.mkdir()
                attestation = root / "attestation.json"
                attestation.write_text("{}", encoding="utf-8")
                completed = datetime.now().astimezone() - timedelta(minutes=1)
                started = completed - timedelta(minutes=10)
                run_id = started.strftime("%Y%m%d-%H%M%S")
                date_key = started.strftime("%Y-%m-%d")
                (runtime / "nightly_scheduler_state.json").write_text(
                    json.dumps(
                        {
                            "last_attempt_date": date_key,
                            "last_attempt_started_at": started.isoformat(),
                            "last_run_date": date_key,
                            "last_run_completed_at": completed.isoformat(),
                            "last_run_exit_code": 0,
                            "last_run_status": "completed",
                            "last_run_was_actual_pipeline": True,
                        }
                    ),
                    encoding="utf-8",
                )
                if case == "malformed":
                    (validation / f"{run_id}-nightly-pipeline-summary.json").write_text(
                        "{}", encoding="utf-8"
                    )
                adapter = ExistingEngineAdapter(
                    Settings(
                        data_dir=root / "data",
                        resumegen_root=root / "resume",
                        outreach_root=outreach,
                        runtime_dir=runtime,
                        attestation_path=attestation,
                    )
                )
                error_patch = (
                    patch.object(
                        adapter,
                        "_verify_summary",
                        side_effect=ValueError(f"malformed terminal report {_PRIVATE}"),
                    )
                    if case == "malformed"
                    else nullcontext()
                )
                with error_patch:
                    progress = adapter.run_progress(locks=_FREE_LOCKS)

                self.assertEqual(progress["status"], "attention")
                self.assertFalse(progress["is_current"])
                self.assertEqual(progress["run_id"], run_id)
                self.assertIn("did not verify", progress["reason"])
                self.assertNotIn(_PRIVATE, json.dumps(progress))

    def test_idle_newest_valid_scan_stops_after_first_valid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validation = root / "resume/discovery/source_validation"
            outreach = root / "outreach"
            validation.mkdir(parents=True)
            outreach.mkdir()
            attestation = root / "attestation.json"
            attestation.write_text("{}", encoding="utf-8")
            run_ids = (
                "20260713-030000",
                "20260713-020000",
                "20260713-010000",
            )
            for run_id in run_ids:
                (validation / f"{run_id}-nightly-pipeline-summary.json").write_text(
                    "{}", encoding="utf-8"
                )
            adapter = ExistingEngineAdapter(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=root / "resume",
                    outreach_root=outreach,
                    attestation_path=attestation,
                )
            )
            visited: list[str] = []

            def verify(_path: Path, run_id: str) -> dict[str, object]:
                visited.append(run_id)
                if run_id == run_ids[0]:
                    raise ValueError("newest report is malformed")
                if run_id == run_ids[1]:
                    return {"run_id": run_id, "evidence": {}}
                raise AssertionError("older candidate must not be scanned")

            projection = {"run_id": run_ids[1], "started_at": "2026-07-13T02:00:00"}
            with (
                patch.object(adapter, "_verify_summary", side_effect=verify),
                patch.object(
                    adapter, "_project_verified_run", return_value=projection
                ) as project,
                patch.object(
                    adapter,
                    "_scan_runs",
                    side_effect=AssertionError("idle progress must not scan history"),
                ),
            ):
                result = adapter._newest_verified_run_projection()

            self.assertEqual(result, projection)
            self.assertEqual(visited, list(run_ids[:2]))
            project.assert_called_once()


class NextRunAndAccountProjectionTestCase(unittest.TestCase):
    def test_progress_surface_avoids_heavy_operator_asset_scans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            current = {
                "schema_version": "1.0",
                "status": "running",
                "selection": "current",
                "is_current": True,
                "run_id": "20260713-010001",
            }
            recent_jobs = [{"id": "opjob_123", "status": "running"}]
            with (
                patch.object(
                    backend.adapter,
                    "run_progress",
                    return_value=current,
                ) as run_progress,
                patch.object(
                    backend.adapter,
                    "verified_run_projections",
                    side_effect=AssertionError(
                        "progress endpoint must let run_progress select its "
                        "single terminal fallback only when idle"
                    ),
                ),
                patch.object(
                    backend,
                    "assets",
                    side_effect=AssertionError(
                        "progress endpoint must not build heavy operator assets"
                    ),
                ),
                patch.object(
                    backend.adapter,
                    "mutable_snapshot_capture",
                    side_effect=AssertionError(
                        "progress endpoint must never enter mutable capture"
                    ),
                ),
                patch.object(
                    backend,
                    "list_jobs",
                    return_value=recent_jobs,
                ) as list_jobs,
            ):
                projection = backend.progress()

            self.assertEqual(
                set(projection),
                {
                    "schema_version",
                    "generated_at",
                    "current_run_progress",
                    "recent_jobs",
                },
            )
            self.assertEqual(projection["schema_version"], "1.0")
            self.assertEqual(projection["current_run_progress"], current)
            self.assertEqual(projection["recent_jobs"], recent_jobs)
            run_progress.assert_called_once_with()
            list_jobs.assert_called_once_with(limit=10)

    def test_next_run_plan_uses_exact_failures_action_lanes_and_durable_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            plan = backend._next_run_plan(
                [_verified_run()],
                current_progress={
                    "is_current": True,
                    "status": "attention",
                    "run_id": "20260713-010001",
                    "counts": {
                        "scoring_attempted": 21,
                        "scoring_errors": 21,
                    },
                    "evidence": [
                        {"kind": "linkedin_scored", "sha256": "9" * 64}
                    ],
                },
                review_queue={
                    "review_counts": {
                        "pending": 2,
                        "reviewed": 1,
                        "consumed": 99,
                    }
                },
            )
            ids = {item["id"] for item in plan["items"]}
            self.assertEqual(plan["status"], "partial")
            self.assertTrue(plan["current_run_in_progress"])
            self.assertIn("current_source:linkedin_scoring", ids)
            self.assertIn("source:linkedin", ids)
            self.assertIn("action_queue:application_plus_outreach", ids)
            self.assertIn("action_queue:follow_up", ids)
            self.assertIn("review_queue:pending", ids)
            self.assertIn("review_queue:reviewed", ids)
            self.assertNotIn("review_queue:consumed", ids)
            self.assertNotIn(_PRIVATE, json.dumps(plan))

            unavailable = backend._next_run_plan(
                [],
                current_progress={"is_current": False, "status": "unavailable"},
                review_queue={"review_counts": {}},
            )
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertEqual(unavailable["items"], [])

    def test_first_ever_active_scoring_failure_is_a_partial_grounded_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            plan = backend._next_run_plan(
                [],
                current_progress={
                    "is_current": True,
                    "status": "attention",
                    "run_id": "20260713-010001",
                    "counts": {
                        "scoring_attempted": 21,
                        "scoring_errors": 21,
                    },
                    "evidence": [
                        {
                            "kind": "linkedin_scored",
                            "sha256": "9" * 64,
                            "private": _PRIVATE,
                        }
                    ],
                },
                review_queue={"review_counts": {}},
            )

            self.assertEqual(plan["status"], "partial")
            self.assertIsNone(plan["basis_run_id"])
            self.assertIsNone(plan["basis_run_status"])
            self.assertIsNone(plan["basis_completed_at"])
            self.assertTrue(plan["current_run_in_progress"])
            self.assertEqual(plan["items_total"], 1)
            self.assertEqual(plan["items_returned"], 1)
            self.assertFalse(plan["truncated"])
            self.assertEqual(
                plan["items"][0]["id"], "current_source:linkedin_scoring"
            )
            self.assertEqual(plan["items"][0]["count"], 21)
            self.assertEqual(plan["items"][0]["evidence"]["sha256"], "9" * 64)
            self.assertEqual(
                plan["items"][0]["evidence"]["run_id"], "20260713-010001"
            )
            self.assertNotIn(_PRIVATE, json.dumps(plan))

    def test_review_only_plan_is_partial_without_a_terminal_run_basis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            plan = backend._next_run_plan(
                [],
                current_progress={
                    "is_current": False,
                    "status": "unavailable",
                    "run_id": _PRIVATE,
                },
                review_queue={
                    "review_counts": {
                        "pending": 2,
                        "reviewed": 1,
                        "approved": 3,
                        "consumed": 99,
                    },
                    "private": _PRIVATE,
                },
            )

            self.assertEqual(plan["status"], "partial")
            self.assertIsNone(plan["basis_run_id"])
            self.assertFalse(plan["current_run_in_progress"])
            self.assertEqual(plan["items_total"], 3)
            self.assertEqual(plan["items_returned"], 3)
            self.assertFalse(plan["truncated"])
            self.assertEqual(
                {item["id"] for item in plan["items"]},
                {
                    "review_queue:pending",
                    "review_queue:reviewed",
                    "review_queue:approved",
                },
            )
            self.assertTrue(
                all("run_id" not in item["evidence"] for item in plan["items"])
            )
            self.assertNotIn(_PRIVATE, json.dumps(plan))

    def test_account_tracker_surface_is_aggregate_only_with_safe_open_action(self) -> None:
        today = datetime.now().astimezone().date()
        projection = _account_tracker_projection(
            {
                "Account Tracker": [
                    {
                        "Company": "Example",
                        "Tier": "A",
                        "Account Stage": "outreach_active",
                        "People Mapped": "3",
                        "Invites Sent": "2",
                        "Accepted": "1",
                        "Replies": "1",
                        "Account Score": "85",
                        "Fit Score": "8",
                        "Contact Name": _PRIVATE,
                    }
                ],
                "Action Queue": [
                    {
                        "Company": "Example",
                        "Next Action": "Map contacts on LinkedIn",
                        "Next Due": (today - timedelta(days=1)).isoformat(),
                        "Contact Name": _PRIVATE,
                    },
                    {
                        "Company": "Future",
                        "Next Action": "Send LinkedIn invites",
                        "Next Due": (today + timedelta(days=1)).isoformat(),
                    },
                ],
            },
            {
                "state": "valid",
                "path": "workspace/account_tracker.xlsx",
                "sha256": "f" * 64,
                "size_bytes": 123,
            },
        )
        surface = OperatorBackend._account_tracker_surface(
            {"status": "available", "account_tracker": projection},
            open_action={
                "command_id": "open.account_tracker",
                "label": "Open account tracker",
                "status": "available",
                "reason": "",
                "confirmation_phrase": "OPEN_ACCOUNT_TRACKER",
                "asynchronous": True,
            },
        )
        self.assertEqual(surface["summary"]["account_count"], 1)
        self.assertEqual(surface["summary"]["action_count"], 2)
        self.assertEqual(surface["summary"]["actions_due_now"], 1)
        self.assertEqual(surface["summary"]["due_counts"]["overdue"], 1)
        self.assertEqual(
            surface["summary"]["action_type_counts"]["Map contacts on LinkedIn"],
            1,
        )
        self.assertEqual(surface["open_action"]["status"], "available")
        self.assertEqual(surface["open_action"]["parameters"], {})
        self.assertNotIn("action_items", surface["summary"])
        self.assertNotIn(_PRIVATE, json.dumps(surface))

    def test_assets_wires_all_three_projections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            run = _verified_run(status="complete")
            tracker = {
                "evidence": {
                    "state": "valid",
                    "path": "workspace/account_tracker.xlsx",
                    "sha256": "f" * 64,
                    "size_bytes": 123,
                },
                "account_count": 1,
                "action_count": 0,
                "actions_due_now": 0,
                "due_counts": {},
                "tier_counts": {"A": 1},
                "stage_counts": {"outreach_active": 1},
                "action_type_counts": {},
                "activity_totals": {"People Mapped": 3},
                "people_mapped": 3,
                "score_summary": {},
            }
            capability = {
                "commands": [
                    {
                        "command_id": "open.account_tracker",
                        "label": "Open account tracker",
                        "status": "available",
                        "reason": "",
                        "confirmation_phrase": "OPEN_ACCOUNT_TRACKER",
                        "asynchronous": True,
                    }
                ]
            }
            with (
                patch.object(
                    backend.adapter, "verified_run_projections", return_value=[run]
                ),
                patch.object(backend.adapter, "lock_states", return_value=_FREE_LOCKS),
                patch.object(
                    backend.adapter,
                    "mutable_snapshot_capture",
                    return_value=nullcontext(object()),
                ),
                patch.object(
                    backend.adapter,
                    "current_workspace_snapshot",
                    return_value={
                        "status": "available",
                        "consistency": "stable-at-capture",
                        "application_queue": None,
                        "reasons": [],
                        "evidence": {},
                    },
                ),
                patch.object(
                    backend,
                    "_workbook_assets",
                    return_value={
                        "status": "available",
                        "account_tracker": tracker,
                    },
                ),
                patch.object(
                    backend,
                    "_current_apply_queue_assets",
                    return_value={"status": "available", "items": []},
                ),
                patch.object(backend, "_story_comms_assets", return_value={}),
                patch.object(backend, "review_queue", return_value={"review_counts": {}}),
            ):
                assets = backend.assets(capability=capability)

            self.assertEqual(assets["current_run_progress"]["status"], "complete")
            self.assertEqual(assets["next_run_plan"]["basis_run_id"], run["run_id"])
            self.assertEqual(assets["account_tracker"]["status"], "available")
            self.assertEqual(
                assets["account_tracker"]["open_action"]["command_id"],
                "open.account_tracker",
            )

    def test_assets_discards_every_mutable_projection_on_capture_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))

            @contextmanager
            def changed_capture():
                yield object()
                raise MutableSnapshotChanged("adversarial rewrite")

            with (
                patch.object(
                    backend.adapter, "verified_run_projections", return_value=[]
                ),
                patch.object(backend.adapter, "lock_states", return_value=_FREE_LOCKS),
                patch.object(
                    backend.adapter,
                    "mutable_snapshot_capture",
                    side_effect=changed_capture,
                ),
                patch.object(
                    backend.adapter,
                    "current_workspace_snapshot",
                    return_value={
                        "status": "available",
                        "consistency": "stable-at-capture",
                        "application_queue": {},
                        "reasons": [],
                        "evidence": {},
                    },
                ),
                patch.object(
                    backend,
                    "_workbook_assets",
                    return_value={"status": "available", "account_tracker": {}},
                ),
                patch.object(
                    backend,
                    "_story_comms_assets",
                    return_value={"status": "available"},
                ),
                patch.object(backend, "review_queue", return_value={"review_counts": {}}),
            ):
                assets = backend.assets(capability={"commands": []})

            self.assertEqual(assets["mutable_capture"]["status"], "partial")
            self.assertEqual(assets["workbooks"]["status"], "partial")
            self.assertEqual(assets["current_apply_queue"]["status"], "partial")
            self.assertEqual(assets["current_apply_queue"]["items"], [])
            self.assertEqual(assets["story_comms"]["status"], "partial")
            self.assertEqual(assets["account_tracker"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()

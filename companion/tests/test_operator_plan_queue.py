from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from recruiting_companion.config import Settings
from recruiting_companion.operator_backend import (
    OperatorBackend,
    _combine_plan_and_queue_rows,
    _planned_action_summary,
    _project_next_run_queue_items,
    _project_track_2_plan_entries,
)


def _action_queue_payload() -> dict[str, object]:
    return {
        "counts": {
            "application_plus_outreach": 1,
            "application_only": 1,
            "outreach_only_today": 0,
            "relationship_buffer": 0,
            "follow_up": 1,
            "skipped_internal": 0,
        },
        "application_plus_outreach": [
            {
                "company": "Assembled",
                "role_title": "Product Manager Intern",
                "source": "linkedin",
                "reasons": ["high fit", "warm path"],
                "fit_score": "8.5",
                "recommended_action": "run_linkedin_company_pipeline",
                "queue_rank": 1,
            }
        ],
        "application_only": [
            {
                "company": "Stripe",
                "role_title": "APM Intern",
                "source": "apply_queue",
                "reasons": ["ready_to_generate"],
                "fit_score": 9.1,
                "recommended_action": "generate_resume",
                "queue_rank": 2,
            }
        ],
        "outreach_only_today": [],
        "relationship_buffer": [],
        "follow_up": [
            {
                "company": "Cisco",
                "role_title": "Follow up",
                "source": "track_2",
                "reasons": ["unanswered_inbound"],
                "recommended_action": "linkedin_follow_up",
            }
        ],
        "skipped_internal": [],
    }


def _track_2_plan_payload() -> dict[str, object]:
    return {
        "budget": {
            "max_total_actions": 80,
            "max_companies": 55,
            "max_linkedin_invites": 25,
            "max_linkedin_followups": 25,
            "max_company_mapping": 15,
            "max_email_research": 10,
            "max_context_enrichment": 8,
            "max_email_drafts": 5,
        },
        "selected": [
            {
                "company": "Salesforce",
                "tier": "A",
                "campaign_action": "send_initial_invites",
                "campaign_channel": "linkedin",
                "phase": "5_send_linkedin_invites",
                "account_score": 52,
                "expected_linkedin_invites": 10,
                "expected_company_mapping": 1,
                "reason": "Contacts mapped; ready for first invite wave.",
                "target_role": "AI Strategy Intern",
            },
            {
                "company": "Assembled",
                "tier": "B",
                "campaign_action": "map_more_contacts",
                "campaign_channel": "linkedin",
                "phase": "4_contact_mapping",
                "account_score": 34,
                "expected_company_mapping": 1,
                "reason": "Only 0 relevant contacts mapped.",
                "target_role": "Product Manager Intern",
            },
        ],
    }


class OperatorPlanQueueTests(unittest.TestCase):
    def test_planned_action_summary_formats_counts(self) -> None:
        summary = _planned_action_summary(
            "send_initial_invites", {"linkedin_invites": 10, "company_mapping": 1}
        )
        self.assertEqual(
            summary, "send initial invites · 10 invites, 1 mapping pass"
        )
        self.assertEqual(
            _planned_action_summary("enrich_company_context", {}),
            "enrich company context",
        )

    def test_project_track_2_plan_entries_extracts_actions_and_counts(self) -> None:
        entries = _project_track_2_plan_entries(_track_2_plan_payload())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["company"], "Salesforce")
        self.assertEqual(entries[0]["planned_action"], "send_initial_invites")
        self.assertEqual(
            entries[0]["planned_counts"],
            {"linkedin_invites": 10, "company_mapping": 1},
        )
        self.assertIn("10 invites", entries[0]["action_summary"])
        self.assertEqual(entries[1]["planned_action"], "map_more_contacts")

    def test_combine_plan_and_queue_rows_merges_and_appends(self) -> None:
        plan_entries = _project_track_2_plan_entries(_track_2_plan_payload())
        queue_items, _ = _project_next_run_queue_items(
            _action_queue_payload(),
            run_id="20260715-010001",
            sha256="a" * 64,
            limit=10,
        )
        combined, total = _combine_plan_and_queue_rows(
            plan_entries,
            queue_items,
            run_id="20260715-010001",
            plan_sha256="f" * 64,
            limit=10,
        )
        # Two plan rows first, then the two queue rows that were not planned
        # (Assembled merged into its plan row instead of duplicating).
        self.assertEqual(total, 4)
        companies = [row["company"] for row in combined]
        self.assertEqual(companies, ["Salesforce", "Assembled", "Stripe", "Cisco"])
        assembled = combined[1]
        self.assertEqual(assembled["lane"], "track_2_plan")
        self.assertEqual(assembled["fit_score"], 8.5)
        self.assertIn("1 mapping pass", assembled["action_summary"])
        self.assertEqual(assembled["evidence"]["sha256"], "f" * 64)
        self.assertEqual([row["rank"] for row in combined], [1, 2, 3, 4])
    def test_project_next_run_queue_items_orders_lanes_and_bounds(self) -> None:
        items, total = _project_next_run_queue_items(
            _action_queue_payload(),
            run_id="20260715-010001",
            sha256="a" * 64,
            limit=2,
        )
        self.assertEqual(total, 3)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["company"], "Assembled")
        self.assertEqual(items[0]["target_run"], "next-nightly")
        self.assertEqual(items[0]["fit_score"], 8.5)
        self.assertEqual(items[1]["company"], "Stripe")
        self.assertEqual(items[1]["target_run"], "current-apply-queue")

    def test_next_run_plan_attaches_budgets_and_queue_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resumegen = root / "ResumeGenerator"
            outreach = root / "Outreach"
            queue_dir = resumegen / "discovery" / "source_validation"
            queue_dir.mkdir(parents=True)
            outreach.mkdir(parents=True)
            payload = _action_queue_payload()
            queue_path = queue_dir / "20260715-daily-action-queue.json"
            encoded = (json.dumps(payload) + "\n").encode("utf-8")
            queue_path.write_bytes(encoded)
            relative = queue_path.relative_to(resumegen).as_posix()
            sha256 = hashlib.sha256(encoded).hexdigest()

            backend = OperatorBackend(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resumegen,
                    outreach_root=outreach,
                )
            )
            plan = backend._next_run_plan(
                [
                    {
                        "scope": "run-scoped",
                        "status": "complete",
                        "run_id": "20260715-010001",
                        "completed_at": "2026-07-15T02:00:00+05:30",
                        "failure_count": 0,
                        "sources": [],
                        "queue": {
                            "decision_total_parts": {
                                "application_plus_outreach": 1,
                                "application_only": 1,
                                "follow_up": 1,
                            }
                        },
                        "report": {
                            "run_status": "completed",
                            "track_2_status": "completed",
                        },
                        "evidence": {
                            "action_queue": {
                                "state": "valid",
                                "path": relative,
                                "sha256": sha256,
                                "size_bytes": len(encoded),
                            },
                            "daily_manifest": {
                                "state": "valid",
                                "path": "manifest.json",
                                "sha256": "b" * 64,
                                "size_bytes": 1,
                            },
                            "outreach_report": {
                                "state": "valid",
                                "path": "report.json",
                                "sha256": "c" * 64,
                                "size_bytes": 1,
                            },
                            "summary": {
                                "state": "valid",
                                "path": "summary.json",
                                "sha256": "d" * 64,
                                "size_bytes": 1,
                            },
                        },
                    }
                ],
                current_progress={"is_current": False, "status": "unavailable"},
                review_queue={"review_counts": {}},
            )

            self.assertEqual(plan["schema_version"], "1.1")
            self.assertEqual(plan["queue_items_status"], "available")
            self.assertEqual(plan["queue_items_total"], 3)
            self.assertEqual(plan["queue_items_returned"], 3)
            self.assertEqual(plan["budgets"]["max_linkedin_invites"], 12)
            self.assertEqual(plan["plan_status"], "unavailable")
            companies = [item["company"] for item in plan["queue_items"]]
            self.assertEqual(companies, ["Assembled", "Stripe", "Cisco"])

    def test_next_run_plan_binds_track_2_plan_budgets_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resumegen = root / "ResumeGenerator"
            outreach = root / "Outreach"
            queue_dir = resumegen / "discovery" / "source_validation"
            queue_dir.mkdir(parents=True)
            artifacts = outreach / "artifacts"
            artifacts.mkdir(parents=True)

            queue_payload = _action_queue_payload()
            queue_path = queue_dir / "20260715-daily-action-queue.json"
            queue_encoded = (json.dumps(queue_payload) + "\n").encode("utf-8")
            queue_path.write_bytes(queue_encoded)

            plan_path = artifacts / "20260715-014259-track-2-daily-plan.json"
            plan_path.write_text(json.dumps(_track_2_plan_payload()))
            run_path = artifacts / "20260715-025138-track-2-daily-run.json"
            run_path.write_text(
                json.dumps(
                    {"plan_artifact": "artifacts/20260715-014259-track-2-daily-plan.json"}
                )
            )
            manifest_payload = {
                "run_id": "20260715-010001",
                "track_2_daily_run_artifacts": [str(run_path)],
            }
            manifest_encoded = json.dumps(manifest_payload).encode("utf-8")
            manifest_path = resumegen / "manifest.json"
            manifest_path.write_bytes(manifest_encoded)

            backend = OperatorBackend(
                Settings(
                    data_dir=root / "data",
                    resumegen_root=resumegen,
                    outreach_root=outreach,
                )
            )
            plan = backend._next_run_plan(
                [
                    {
                        "scope": "run-scoped",
                        "status": "complete",
                        "run_id": "20260715-010001",
                        "completed_at": "2026-07-15T02:00:00+05:30",
                        "failure_count": 0,
                        "sources": [],
                        "queue": {
                            "decision_total_parts": {
                                "application_plus_outreach": 1,
                                "application_only": 1,
                                "follow_up": 1,
                            }
                        },
                        "report": {
                            "run_status": "completed",
                            "track_2_status": "completed",
                        },
                        "evidence": {
                            "action_queue": {
                                "state": "valid",
                                "path": queue_path.relative_to(resumegen).as_posix(),
                                "sha256": hashlib.sha256(queue_encoded).hexdigest(),
                                "size_bytes": len(queue_encoded),
                            },
                            "daily_manifest": {
                                "state": "valid",
                                "path": "manifest.json",
                                "sha256": hashlib.sha256(manifest_encoded).hexdigest(),
                                "size_bytes": len(manifest_encoded),
                            },
                            "outreach_report": {
                                "state": "valid",
                                "path": "report.json",
                                "sha256": "c" * 64,
                                "size_bytes": 1,
                            },
                            "summary": {
                                "state": "valid",
                                "path": "summary.json",
                                "sha256": "d" * 64,
                                "size_bytes": 1,
                            },
                        },
                    }
                ],
                current_progress={"is_current": False, "status": "unavailable"},
                review_queue={"review_counts": {}},
            )

            self.assertEqual(plan["plan_status"], "bound")
            self.assertEqual(plan["budgets"]["max_linkedin_invites"], 25)
            self.assertEqual(plan["budgets"]["source"], "exact_track_2_daily_plan")
            companies = [item["company"] for item in plan["queue_items"]]
            self.assertEqual(
                companies, ["Salesforce", "Assembled", "Stripe", "Cisco"]
            )
            salesforce = plan["queue_items"][0]
            self.assertEqual(salesforce["planned_action"], "send_initial_invites")
            self.assertIn("10 invites", salesforce["action_summary"])

    def test_next_run_plan_fails_closed_on_missing_queue_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backend = OperatorBackend(Settings(data_dir=Path(temporary)))
            plan = backend._next_run_plan(
                [],
                current_progress={"is_current": False, "status": "unavailable"},
                review_queue={"review_counts": {}},
            )
            self.assertEqual(plan["schema_version"], "1.1")
            self.assertEqual(plan["queue_items"], [])
            self.assertEqual(plan["queue_items_status"], "unavailable")
            self.assertEqual(plan["budgets"]["max_linkedin_followups"], 8)


if __name__ == "__main__":
    unittest.main()

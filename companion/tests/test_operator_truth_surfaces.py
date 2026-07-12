from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from recruiting_companion.config import Settings
from recruiting_companion.operator_backend import OperatorBackend


class OperatorTruthSurfaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.backend = OperatorBackend(
            Settings(
                data_dir=Path(self.temporary.name),
                user_id="truth-surfaces",
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def verified_runs(count: int) -> list[dict[str, object]]:
        return [
            {
                "run_id": f"20260712-{index:06d}",
                "status": "complete",
                "failure_count": 0,
                "report": {
                    "run_status": "completed",
                    "track_2_status": "completed",
                },
                "delivery_contract": {"mode": "full_delivery"},
                "sources": [],
                "evidence": {},
            }
            for index in range(count)
        ]

    def test_report_history_uses_full_verified_total_beyond_twenty(self) -> None:
        adapter_status = self.backend.adapter.status()
        adapter_status["verified_run_count"] = 47
        with patch.object(
            self.backend.adapter,
            "status",
            return_value=adapter_status,
        ):
            public_capability = self.backend.capabilities()
            capability = self.backend.capabilities(_include_internal=True)
        self.assertNotIn("_verified_run_count", public_capability)
        self.assertEqual(capability["_verified_run_count"], 47)

        runs = self.verified_runs(20)
        with (
            patch.object(
                self.backend.adapter,
                "verified_run_projections",
                return_value=runs,
            ) as verified,
            patch.object(
                self.backend.adapter,
                "run_progress",
                return_value={"status": "unavailable", "is_current": False},
            ),
            patch.object(
                self.backend,
                "_next_run_plan",
                return_value={"status": "unavailable"},
            ),
        ):
            assets = self.backend.assets(
                capability=capability,
                review_queue={"review_counts": {}},
            )
        verified.assert_called_once_with(limit=20)
        reports = assets["daily_reports"]
        self.assertEqual(reports["count"], 20)
        self.assertEqual(reports["items_returned"], 20)
        self.assertEqual(reports["items_total"], 47)
        self.assertEqual(reports["total"], 47)
        self.assertTrue(reports["truncated"])
        self.assertEqual(reports["limit"], 20)
        self.assertEqual(reports["items"][0]["run_id"], runs[-1]["run_id"])

    def test_source_rows_bind_to_daily_manifest_not_source_metrics(self) -> None:
        daily_manifest = {
            "path": "discovery/source_validation/exact-manifest.json",
            "sha256": "a" * 64,
        }
        source_metrics = {
            "path": "discovery/source_validation/exact-source-metrics.json",
            "sha256": "b" * 64,
        }
        projection = self.backend._source_assets(
            [
                {
                    "run_id": "20260712-010001",
                    "status": "complete",
                    "failure_count": 0,
                    "sources": [
                        {
                            "source": "linkedin",
                            "status": "ran",
                            "raw_count": 10,
                            "kept_count": 3,
                        }
                    ],
                    "evidence": {
                        "daily_manifest": daily_manifest,
                        "source_metrics": source_metrics,
                    },
                }
            ]
        )
        self.assertEqual(
            projection["metric_source"],
            "daily_manifest.source_families",
        )
        self.assertEqual(projection["evidence"], daily_manifest)
        self.assertEqual(projection["latest"]["evidence"], daily_manifest)
        self.assertEqual(projection["items"][0]["evidence"], daily_manifest)
        self.assertEqual(
            projection["items"][0]["metric_source"],
            "daily_manifest.source_families",
        )
        self.assertNotIn(source_metrics["sha256"], json.dumps(projection))

    def insert_reviews(self) -> None:
        now = datetime.now(UTC)
        rows = []
        for index in range(33):
            if index < 31:
                state = "pending"
            else:
                state = "consumed"
            expires_at = (
                now - timedelta(hours=1)
                if index == 30
                else now + timedelta(hours=24)
            )
            updated_at = now - timedelta(seconds=index)
            rows.append(
                (
                    f"review_{index:032x}",
                    self.backend.settings.user_id,
                    "outreach.linkedin.send",
                    f"target_{index:032x}",
                    "linkedin_invite",
                    f"Target {index}",
                    "{}",
                    f"{index + 1:064x}",
                    state,
                    expires_at.isoformat(),
                    updated_at.isoformat(),
                    updated_at.isoformat(),
                )
            )
        with self.backend.db.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO operator_reviews (
                    id, user_id, command_id, target_id, target_type,
                    target_label, target_snapshot_json, artifact_sha256,
                    state, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def test_review_counts_cover_full_ledger_and_recent_rows_stay_bounded(self) -> None:
        self.insert_reviews()
        with patch.object(
            self.backend,
            "review_targets",
            return_value={"lanes": []},
        ):
            queue = self.backend.review_queue()

        self.assertEqual(queue["review_counts"]["pending"], 30)
        self.assertEqual(queue["review_counts"]["expired"], 1)
        self.assertEqual(queue["review_counts"]["consumed"], 2)
        self.assertEqual(len(queue["recent_reviews"]), 25)
        self.assertEqual(queue["recent_reviews_items_returned"], 25)
        self.assertEqual(queue["recent_reviews_items_total"], 33)
        self.assertTrue(queue["recent_reviews_truncated"])
        self.assertEqual(
            queue["recent_reviews_meta"],
            {
                "items_returned": 25,
                "items_total": 33,
                "truncated": True,
                "limit": 25,
            },
        )

        plan = self.backend._next_run_plan(
            [
                {
                    "run_id": "20260712-010001",
                    "status": "complete",
                    "completed_at": "2026-07-12T02:00:00+00:00",
                    "failure_count": 0,
                    "sources": [],
                    "report": {
                        "run_status": "completed",
                        "track_2_status": "completed",
                    },
                    "queue": {},
                    "evidence": {},
                }
            ],
            current_progress={"status": "unavailable", "is_current": False},
            review_queue=queue,
        )
        review_items = [
            item
            for item in plan["items"]
            if item["category"] == "review_queue"
        ]
        self.assertTrue(review_items)
        self.assertEqual(plan["basis_run_id"], "20260712-010001")
        self.assertTrue(
            all("run_id" not in item["evidence"] for item in review_items)
        )


if __name__ == "__main__":
    unittest.main()

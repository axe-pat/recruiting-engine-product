from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from recruiting_companion.api import make_server
from recruiting_companion.auth import TokenStore
from recruiting_companion.config import Settings
from recruiting_companion.existing_adapter import ExistingEngineAdapter
from recruiting_companion.service import (
    CompanionService,
    ConflictError,
    ValidationError,
)


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root, user_id="test-user", port=8765)
        self.service = CompanionService(self.settings)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profile_preferences_documents_and_user_isolation(self) -> None:
        profile = self.service.put_profile(
            {
                "headline": "Product builder",
                "target_roles": ["Product", "Strategy", "Product"],
                "skills": ["Research"],
            }
        )
        self.assertEqual(profile["target_roles"], ["Product", "Strategy"])
        preferences = self.service.put_preferences(
            {"minimum_fit_score": 7.5, "human_review": True}
        )
        self.assertTrue(preferences["human_review"])

        document = self.service.add_document(
            filename="../../private.txt",
            content=b"local-only example",
            kind="resume",
            media_type="text/plain",
        )
        self.assertEqual(document["filename"], "private.txt")
        self.assertNotIn("storage_path", document)
        self.assertFalse((self.root / "private.txt").exists())
        stored = list(self.settings.documents_dir.iterdir())
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].read_bytes(), b"local-only example")
        duplicate = self.service.add_document(
            filename="another-name.txt",
            content=b"local-only example",
            kind="resume",
            media_type="text/plain",
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(list(self.settings.documents_dir.iterdir())), 1)

        other = CompanionService(
            Settings(data_dir=self.root, user_id="other-user", port=8765)
        )
        self.assertIsNone(other.get_profile())
        self.assertEqual(other.list_documents(), [])

    def test_onboarding_and_truthful_portable_run(self) -> None:
        encoded = base64.b64encode(b"portable document").decode("ascii")
        onboarding = self.service.onboard(
            {
                "profile": {"headline": "Builder", "target_roles": ["Product"]},
                "preferences": {"minimum_fit_score": 7.0},
                "companies": [{"name": "Example Local Company", "strategic": True}],
                "jobs": [
                    {
                        "company_name": "Example Local Company",
                        "title": "Product Lead",
                        "fit_score": 8.7,
                        "status": "active",
                    },
                    {"title": "Needs Review", "status": "intake"},
                ],
                "contacts": [
                    {
                        "company_name": "Example Local Company",
                        "name": "Example Contact",
                        "status": "approved",
                    }
                ],
                "documents": [
                    {
                        "filename": "source.txt",
                        "kind": "resume",
                        "media_type": "text/plain",
                        "content_base64": encoded,
                    }
                ],
            }
        )
        self.assertEqual(len(onboarding["jobs"]), 2)
        run_result = self.service.run_portable({"min_fit_score": 7.0, "limit": 50})
        self.assertEqual(run_result["run"]["status"], "completed")
        report_id = run_result["reports"][0]["id"]
        report = self.service.get_report(report_id)
        queue = report["summary"]["queue"]
        actions = {item["action"] for item in queue}
        self.assertIn("application_review", actions)
        self.assertIn("fit_review", actions)
        self.assertIn("relationship_review", actions)
        self.assertEqual(
            report["summary"]["source_scope"],
            "current_user_local_database_only",
        )
        self.assertIn(
            "No external source was queried.",
            report["summary"]["truth_contract"],
        )

    def test_outreach_requires_review_approval_and_confirmed_delivery(self) -> None:
        contact = self.service.create_resource(
            "contacts", {"name": "Example Contact", "status": "approved"}
        )
        outreach = self.service.create_outreach(
            {
                "contact_id": contact["id"],
                "channel": "professional_network",
                "draft_text": "A locally authored draft.",
            }
        )
        self.assertEqual(outreach["state"], "draft")
        with self.assertRaises(ConflictError):
            self.service.transition_outreach(
                outreach["id"], to_state="approved", actor="reviewer"
            )
        reviewed, _ = self.service.transition_outreach(
            outreach["id"],
            to_state="reviewed",
            actor="reviewer",
            reviewed_text="A reviewed local draft.",
        )
        self.assertEqual(reviewed["state"], "reviewed")
        approved, _ = self.service.transition_outreach(
            outreach["id"], to_state="approved", actor="approver"
        )
        self.assertEqual(approved["state"], "approved")
        with self.assertRaises(ValidationError):
            self.service.transition_outreach(
                outreach["id"],
                to_state="sent",
                actor="recorder",
                delivery_reference="external-receipt",
            )
        sent, _ = self.service.transition_outreach(
            outreach["id"],
            to_state="sent",
            actor="recorder",
            delivery_reference="external-receipt",
            confirmed=True,
        )
        self.assertEqual(sent["state"], "sent")
        self.assertEqual(len(sent["events"]), 4)

    def test_bounded_job_import_validates_and_deduplicates(self) -> None:
        rows = [
            {
                "company": "Example Company",
                "title": "Product Manager",
                "location": "Remote",
                "url": "https://example.test/jobs/1",
                "fit_score": "8.2",
                "role_family": "Product",
            },
            {
                "company": "Example Company",
                "title": "Product Manager",
                "location": "Remote",
                "url": "https://example.test/jobs/1",
            },
            {"company": "Missing Title"},
        ]
        result = self.service.import_jobs(rows, source_label="handshake_export")
        self.assertEqual(result["received"], 3)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(result["errors"]), 1)
        jobs = self.service.list_resource("jobs")
        self.assertEqual(jobs[0]["source_label"], "handshake_export")

    def test_dashboard_returns_minimal_dtos_full_capped_queue_and_reports(self) -> None:
        company = self.service.create_resource(
            "companies", {"name": "Presentation Company"}
        )
        jobs = []
        for index in range(12):
            jobs.append(
                self.service.create_resource(
                    "jobs",
                    {
                        "company_id": company["id"],
                        "title": f"Product Role {index}",
                        "status": "active",
                        "fit_score": 8.0,
                    },
                )
            )
        application = self.service.create_resource(
            "applications",
            {"job_id": jobs[0]["id"], "status": "planned"},
        )
        second_application = self.service.create_resource(
            "applications",
            {"job_id": jobs[1]["id"], "status": "planned"},
        )
        contact = self.service.create_resource(
            "contacts",
            {
                "company_id": company["id"],
                "name": "Recipient Label",
                "email": "private@example.test",
                "profile_url": "https://example.test/private-profile",
                "status": "approved",
                "notes": "Private notes must not enter the presentation DTO.",
            },
        )
        outreach = self.service.create_outreach(
            {
                "contact_id": contact["id"],
                "channel": "email",
                "draft_text": "Reviewable dashboard draft.",
            }
        )
        second_outreach = self.service.create_outreach(
            {
                "contact_id": contact["id"],
                "channel": "professional_network",
                "draft_text": "Second reviewable draft.",
            }
        )
        self.service.run_portable({"min_fit_score": 7.0, "limit": 20})
        with patch(
            "recruiting_companion.service.DASHBOARD_PRESENTATION_LIMIT", 1
        ):
            dashboard = self.service.dashboard_snapshot()

        self.assertGreater(len(dashboard["action_queue"]), 10)
        self.assertEqual(
            len(dashboard["action_queue"]),
            dashboard["latest_report"]["output_counts"]["queue_items"],
        )
        self.assertIn("input_counts", dashboard["latest_report"])
        self.assertIn("output_counts", dashboard["latest_report"])
        self.assertEqual(len(dashboard["recent_reports"]), 1)
        self.assertNotIn("input_counts", dashboard["recent_reports"][0])
        self.assertIn("summary_text", dashboard["recent_reports"][0])

        application_item = dashboard["application_items"][0]
        self.assertIn(
            application_item["id"],
            {application["id"], second_application["id"]},
        )
        self.assertEqual(
            set(application_item),
            {"id", "company", "role", "status", "updated_at"},
        )
        outreach_item = dashboard["outreach_items"][0]
        self.assertIn(
            outreach_item["id"],
            {outreach["id"], second_outreach["id"]},
        )
        self.assertEqual(outreach_item["recipient"], "Recipient Label")
        self.assertIn(
            outreach_item["text"],
            {"Reviewable dashboard draft.", "Second reviewable draft."},
        )
        self.assertEqual(
            set(outreach_item),
            {
                "id",
                "company",
                "recipient",
                "channel",
                "state",
                "text",
                "updated_at",
            },
        )
        rendered = json.dumps(
            {
                "application_items": dashboard["application_items"],
                "outreach_items": dashboard["outreach_items"],
            }
        )
        self.assertNotIn("private-profile", rendered)
        self.assertNotIn("Private notes", rendered)
        self.assertEqual(
            dashboard["presentation_meta"],
            {
                "applications": {"total": 2, "returned": 1, "truncated": True},
                "outreach": {"total": 2, "returned": 1, "truncated": True},
            },
        )


class APITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            data_dir=Path(self.temporary.name),
            user_id="api-user",
            port=8765,
        )
        self.settings.prepare()
        self.now = [1_800_000_000.0]
        self.tokens = TokenStore(
            self.settings.user_dir,
            clock=lambda: self.now[0],
        )
        self.bootstrap = self.tokens.bootstrap()
        self.service = CompanionService(self.settings)
        self.server = make_server(
            self.settings,
            self.service,
            self.tokens,
            host="127.0.0.1",
            port=0,
        )
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        bearer: str | None = None,
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return response.status, payload, response_headers

    def test_pair_auth_rotation_cors_and_host_protection(self) -> None:
        status, health, headers = self.request(
            "GET",
            "/api/v1/health",
            origin="https://axe-pat.github.io",
        )
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(
            headers["Access-Control-Allow-Origin"],
            "https://axe-pat.github.io",
        )
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        status, _, _ = self.request(
            "GET",
            "/api/v1/health",
            origin="https://untrusted.example",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET",
            "/api/v1/health",
            host="attacker.example",
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer="re_local_invalid"
        )
        self.assertEqual(status, 401)

        status, paired, _ = self.request(
            "POST",
            "/api/v1/pair",
            body={"pairing_token": self.bootstrap.pairing_token},
        )
        self.assertEqual(status, 200)
        bearer = str(paired["bearer_token"])
        self.assertEqual(set(paired), {"bearer_token", "token_type"})
        self.assertTrue(bearer.startswith("re_local_"))
        self.assertEqual(bearer, self.bootstrap.bearer_token)
        status, _, _ = self.request(
            "POST",
            "/api/v1/pair",
            body={"pairing_token": self.bootstrap.pairing_token},
        )
        self.assertEqual(status, 401)

        status, rotated, _ = self.request(
            "POST", "/api/v1/auth/rotate", body={}, bearer=bearer
        )
        self.assertEqual(status, 200)
        replacement = str(rotated["bearer_token"])
        self.assertNotEqual(replacement, bearer)
        status, _, _ = self.request("GET", "/api/v1/dashboard", bearer=bearer)
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=replacement
        )
        self.assertEqual(status, 200)
        status, snapshot, _ = self.request(
            "GET", "/api/v1/existing-engine/snapshot", bearer=replacement
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            snapshot["existing_engine"]["run_snapshot"]["status"],
            "unavailable",
        )

    def test_web_pair_is_short_lived_hash_only_and_cannot_rotate(self) -> None:
        state_before = json.loads(
            self.tokens.state_path.read_text(encoding="utf-8")
        )
        local_bearer = self.bootstrap.bearer_token or ""
        status, paired, _ = self.request(
            "POST",
            "/api/v1/pair",
            body={
                "pairing_token": self.bootstrap.pairing_token,
                "client_type": "web",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            set(paired),
            {"bearer_token", "token_type", "client_type", "expires_in"},
        )
        self.assertEqual(paired["client_type"], "web")
        self.assertEqual(paired["expires_in"], 1800)
        web_bearer = str(paired["bearer_token"])
        self.assertTrue(web_bearer.startswith("re_web_"))
        self.assertNotEqual(web_bearer, local_bearer)

        persisted = self.tokens.state_path.read_text(encoding="utf-8")
        state_after = json.loads(persisted)
        self.assertNotIn(web_bearer, persisted)
        self.assertEqual(
            state_after["bearer_sha256"], state_before["bearer_sha256"]
        )
        self.assertEqual(len(state_after["web_sessions"]), 1)
        self.assertEqual(
            set(state_after["web_sessions"][0]), {"sha256", "expires_at"}
        )
        self.assertEqual(
            self.tokens.bearer_path.read_text(encoding="utf-8").strip(),
            local_bearer,
        )

        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=web_bearer
        )
        self.assertEqual(status, 200)
        status, error, _ = self.request(
            "POST", "/api/v1/auth/rotate", body={}, bearer=web_bearer
        )
        self.assertEqual(status, 403)
        self.assertEqual(error["error"]["code"], "insufficient_scope")

        self.now[0] += 1801
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=web_bearer
        )
        self.assertEqual(status, 401)
        expired_state = json.loads(
            self.tokens.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(expired_state["web_sessions"], [])
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=local_bearer
        )
        self.assertEqual(status, 200)

    def test_local_bearer_rotation_invalidates_all_web_sessions(self) -> None:
        local_bearer = self.bootstrap.bearer_token or ""
        status, paired, _ = self.request(
            "POST",
            "/api/v1/pair",
            body={
                "pairing_token": self.bootstrap.pairing_token,
                "client_type": "web",
            },
        )
        self.assertEqual(status, 200)
        web_bearer = str(paired["bearer_token"])
        status, rotated, _ = self.request(
            "POST", "/api/v1/auth/rotate", body={}, bearer=local_bearer
        )
        self.assertEqual(status, 200)
        replacement = str(rotated["bearer_token"])
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=web_bearer
        )
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "GET", "/api/v1/dashboard", bearer=replacement
        )
        self.assertEqual(status, 200)
        state = json.loads(self.tokens.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["web_sessions"], [])

    def test_web_scope_is_a_server_side_hosted_ui_allowlist(self) -> None:
        local_bearer = self.bootstrap.bearer_token or ""
        status, company_response, _ = self.request(
            "POST",
            "/api/v1/companies",
            bearer=local_bearer,
            body={"name": "Scoped Company"},
        )
        self.assertEqual(status, 201)
        company_id = company_response["company"]["id"]
        status, contact_response, _ = self.request(
            "POST",
            "/api/v1/contacts",
            bearer=local_bearer,
            body={
                "company_id": company_id,
                "name": "Scoped Recipient",
                "status": "approved",
            },
        )
        self.assertEqual(status, 201)
        contact_id = contact_response["contact"]["id"]
        status, outreach_response, _ = self.request(
            "POST",
            "/api/v1/outreach",
            bearer=local_bearer,
            body={
                "contact_id": contact_id,
                "channel": "email",
                "draft_text": "Scoped draft.",
            },
        )
        self.assertEqual(status, 201)
        outreach_id = outreach_response["outreach"]["id"]

        status, paired, _ = self.request(
            "POST",
            "/api/v1/pair",
            body={
                "pairing_token": self.bootstrap.pairing_token,
                "client_type": "web",
            },
        )
        self.assertEqual(status, 200)
        web_bearer = str(paired["bearer_token"])

        for method, path, body in (
            ("GET", "/api/v1/profile", None),
            ("GET", "/api/v1/documents", None),
            ("GET", "/api/v1/jobs", None),
            ("GET", "/api/v1/companies", None),
            ("GET", "/api/v1/contacts", None),
            ("GET", "/api/v1/applications", None),
            ("GET", "/api/v1/outreach", None),
            ("GET", "/api/v1/runs", None),
            ("POST", "/api/v1/onboarding", {}),
            ("POST", "/api/v1/contacts", {"name": "Denied"}),
            (
                "POST",
                "/api/v1/outreach",
                {
                    "contact_id": contact_id,
                    "channel": "email",
                    "draft_text": "Denied.",
                },
            ),
            ("PATCH", "/api/v1/jobs/job_unknown", {"status": "active"}),
        ):
            denied_status, denied, _ = self.request(
                method,
                path,
                bearer=web_bearer,
                body=body,
            )
            self.assertEqual(denied_status, 403, path)
            self.assertEqual(denied["error"]["code"], "insufficient_scope")

        for method, path, body in (
            ("GET", "/api/v1/dashboard", None),
            ("GET", "/api/v1/preferences", None),
            ("GET", "/api/v1/existing-engine/status", None),
            ("GET", "/api/v1/existing-engine/snapshot", None),
            (
                "PUT",
                "/api/v1/profile",
                {"profile": {"headline": "Web-updated profile"}},
            ),
            (
                "PUT",
                "/api/v1/preferences",
                {"preferences": {"minimum_fit_score": 7.5}},
            ),
            (
                "POST",
                "/api/v1/documents",
                {
                    "filename": "web-note.txt",
                    "kind": "notes",
                    "media_type": "text/plain",
                    "content_base64": base64.b64encode(b"web upload").decode(
                        "ascii"
                    ),
                },
            ),
            (
                "POST",
                "/api/v1/imports/jobs",
                {
                    "source_label": "web_import",
                    "rows": [{"title": "Web imported role"}],
                },
            ),
            (
                "POST",
                "/api/v1/runs",
                {"type": "portable", "config": {"limit": 5}},
            ),
        ):
            allowed_status, _, _ = self.request(
                method,
                path,
                bearer=web_bearer,
                body=body,
            )
            self.assertIn(allowed_status, {200, 201}, path)

        status, reviewed, _ = self.request(
            "PATCH",
            f"/api/v1/outreach/{outreach_id}",
            bearer=web_bearer,
            body={
                "state": "reviewed",
                "actor": "hosted-ui",
                "reviewed_text": "Scoped reviewed draft.",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(reviewed["outreach"]["state"], "reviewed")
        status, approved, _ = self.request(
            "POST",
            f"/api/v1/outreach/{outreach_id}/approve",
            bearer=web_bearer,
            body={"actor": "hosted-ui"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["outreach"]["state"], "approved")
        for forbidden_state in ("sent", "replied", "cancelled", "failed"):
            status, denied, _ = self.request(
                "PATCH",
                f"/api/v1/outreach/{outreach_id}",
                bearer=web_bearer,
                body={"state": forbidden_state, "actor": "hosted-ui"},
            )
            self.assertEqual(status, 403)
            self.assertEqual(denied["error"]["code"], "insufficient_scope")

    def test_intake_profile_and_invalid_outreach_transition_api(self) -> None:
        bearer = self.bootstrap.bearer_token or ""
        status, profile, _ = self.request(
            "PUT",
            "/api/v1/profile",
            bearer=bearer,
            body={"profile": {"headline": "Local profile", "target_roles": ["Product"]}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["profile"]["headline"], "Local profile")
        status, intake, _ = self.request(
            "POST",
            "/api/v1/intakes",
            bearer=bearer,
            body={
                "source_url": "https://example.test/job/1",
                "title": "Product role",
                "selected_text": "A user-selected local excerpt.",
                "notes": "Review later.",
                "kind": "job",
            },
        )
        self.assertEqual(status, 201)
        job_id = intake["job"]["id"]
        status, draft, _ = self.request(
            "POST",
            "/api/v1/outreach",
            bearer=bearer,
            body={
                "job_id": job_id,
                "channel": "professional_network",
                "draft_text": "A local draft.",
            },
        )
        self.assertEqual(status, 201)
        outreach_id = draft["outreach"]["id"]
        status, _, _ = self.request(
            "PATCH",
            f"/api/v1/outreach/{outreach_id}",
            bearer=bearer,
            body={"status": "approved", "actor": "reviewer"},
        )
        self.assertEqual(status, 409)

    def test_api_upload_traversal_and_json_job_import(self) -> None:
        bearer = self.bootstrap.bearer_token or ""
        status, uploaded, _ = self.request(
            "POST",
            "/api/v1/documents",
            bearer=bearer,
            body={
                "filename": "../../outside.txt",
                "kind": "notes",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"local content").decode("ascii"),
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(uploaded["document"]["filename"], "outside.txt")
        self.assertNotIn("storage_path", uploaded["document"])
        self.assertFalse((Path(self.temporary.name) / "outside.txt").exists())

        import_body = {
            "source_label": "generic_csv_export",
            "rows": [
                {
                    "company": "Example Import Company",
                    "title": "Imported Product Role",
                    "location": "Remote",
                    "url": "https://example.test/imported-role",
                    "fit_score": 8.0,
                    "role_family": "Product",
                },
                {
                    "company": "Example Import Company",
                    "title": "Imported Product Role",
                    "location": "Remote",
                    "url": "https://example.test/imported-role",
                },
            ],
        }
        status, imported, _ = self.request(
            "POST",
            "/api/v1/imports/jobs",
            bearer=bearer,
            body=import_body,
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["import"]["imported"], 1)
        self.assertEqual(imported["import"]["skipped"], 1)


class ExistingAdapterTestCase(unittest.TestCase):
    def test_adapter_fails_closed_then_accepts_exact_attested_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume-engine"
            outreach = root / "outreach-engine"
            validation = resume / "discovery" / "source_validation"
            validation.mkdir(parents=True)
            reports = outreach / "reports"
            reports.mkdir(parents=True)
            runtime = root / "runtime"
            runtime.mkdir()
            attestation = "local-release-attestation"
            attestation_path = root / "release-attestation.json"
            attestation_path.write_text(attestation, encoding="utf-8")
            settings = Settings(
                data_dir=root / "data",
                resumegen_root=resume,
                outreach_root=outreach,
                runtime_dir=runtime,
                attestation_path=attestation_path,
            )
            settings.prepare()
            self.assertTrue(settings.adapter_mutation_lock_path.is_file())
            self.assertEqual(
                ExistingEngineAdapter(settings).status()["locks"]["adapter_mutation"],
                "free",
            )
            empty = ExistingEngineAdapter(settings).status()
            self.assertEqual(empty["verified_run_count"], 0)

            run_id = "20260711-120000"
            artifacts = {
                validation / f"{run_id}-source-metrics.json": {"sources": []},
                validation / f"{run_id}-action-queue.json": {
                    "counts": {
                        "scored_application_selected": 99,
                        "application_only": 2,
                        "follow_up": 3,
                    },
                    "source_counts": {"application_only": {"import": 2}},
                    "application_only": [],
                    "follow_up": [],
                },
            }
            for path, value in artifacts.items():
                path.write_text(json.dumps(value), encoding="utf-8")
            manifest_path = validation / f"{run_id}-daily-engine-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "manifest_schema": "resume_generator.daily_engine_run_manifest",
                        "manifest_version": 1,
                        "run_id": run_id,
                        "status": "completed",
                        "returncode": 0,
                        "source_metrics": str(
                            validation / f"{run_id}-source-metrics.json"
                        ),
                        "action_queue": str(
                            validation / f"{run_id}-action-queue.json"
                        ),
                        "source_families": {
                            "linkedin": {
                                "status": "ran",
                                "raw_count": 2,
                                "kept_count": 1,
                            },
                            "handshake": {
                                "status": "ran",
                                "raw_count": 0,
                                "kept_count": 0,
                            },
                            "jobspy": {
                                "status": "ran",
                                "raw_count": 4,
                                "kept_count": 1,
                            },
                            "startup_sources": {
                                "status": "ran",
                                "raw_count": 2,
                                "kept_count": 2,
                            },
                            "resume_generator_app_queue": {
                                "status": "ran",
                                "raw_count": 3,
                                "kept_count": 1,
                            },
                            "track_2": {
                                "status": "skipped",
                                "raw_count": 0,
                                "kept_count": 0,
                            },
                        },
                        "invite_send_artifacts": [],
                        "linkedin_followup_draft_artifacts": [],
                        "linkedin_followup_send_artifacts": [],
                        "linkedin_reconcile_artifacts": [],
                        "track_2_daily_run_artifacts": [],
                        "track_2_phase_artifacts": [],
                        "track_2_phase_results": [],
                        "track_2_email_draft_artifacts": [],
                        "track_2_email_send_artifacts": [],
                        "app_invites": {
                            "status": "completed",
                            "sent": 0,
                            "failed_companies": [],
                            "unresolved_companies": [],
                        },
                        "track_2": {
                            "status": "skipped",
                            "returncode": None,
                            "planned_action_count": 0,
                            "actual_action_count": 0,
                            "phase_results": [],
                        },
                        "email_channel": {
                            "status": "skipped_missing_credentials",
                            "blockers": ["Not configured"],
                            "draft_artifacts": [],
                            "send_artifacts": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary_path = validation / f"{run_id}-nightly-pipeline-summary.json"
            report_path = reports / f"{run_id}-daily-run-report.json"
            html_path = reports / f"{run_id}-daily-run-report.html"
            html_path.write_text("<!doctype html><title>Run report</title>", encoding="utf-8")
            report_path.write_text(
                json.dumps(
                    {
                        "report_mode": "run_scoped",
                        "nightly_summary": str(summary_path),
                        "since": "2026-07-11T12:00:00Z",
                        "source_breakdown": [],
                        "stage_metrics": {
                            "job_import": {
                                "status": "ran",
                                "runtime_seconds": 1.2,
                            }
                        },
                        "workspace_counts": {"organizations": 4, "contacts": 3},
                        "invite_totals": {"sent": 1},
                        "pending_review_count": 2,
                        "track_2_returncode": None,
                        "track_2_failed": False,
                    }
                ),
                encoding="utf-8",
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "created_at": "2026-07-11T12:00:00Z",
                        "completed_at": "2026-07-11T12:30:00Z",
                        "status": "completed",
                        "failures": [],
                        "daily_engine_manifest": str(manifest_path),
                        "outreach_daily_report": {
                            "returncode": 0,
                            "summary_artifact": str(report_path),
                            "html_report_artifact": str(html_path),
                        },
                    }
                ),
                encoding="utf-8",
            )
            status = ExistingEngineAdapter(settings).status()
            self.assertEqual(status["verified_run_count"], 1)
            self.assertEqual(status["latest_verified_run"]["run_id"], run_id)
            self.assertEqual(status["latest_verified_run"]["status"], "attention")
            self.assertFalse(status["live_run"]["supported"])

            queue_root = (
                resume / "apps" / "Apply queues" / "current_apply_queue"
            )
            queue_root.mkdir(parents=True)
            (queue_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "created_at": "2026-07-11T13:00:00Z",
                        "queue_type": "current_apply_queue",
                        "sources": ["generic_import"],
                        "ready_count": 1,
                        "manual_review_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            private_marker = "PRIVATE-ROW-MUST-NOT-LEAK"
            (queue_root / "priority_order.json").write_text(
                json.dumps(
                    [
                        {
                            "company": private_marker,
                            "role_title": private_marker,
                            "url": f"https://example.test/{private_marker}",
                            "status": "queued",
                            "queue_bucket": "new",
                            "in_latest_run": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            outreach_workspace = outreach / "workspace"
            outreach_workspace.mkdir()
            for table in (
                "organizations",
                "opportunities",
                "contacts",
                "touchpoints",
                "sources",
            ):
                (outreach_workspace / f"{table}.csv").write_text(
                    f"id,private_text\n1,{private_marker}\n",
                    encoding="utf-8",
                )

            blocked_snapshot = ExistingEngineAdapter(settings).snapshot()
            self.assertEqual(
                blocked_snapshot["current_workspace"]["status"], "unavailable"
            )
            self.assertEqual(
                blocked_snapshot["current_workspace"]["lock_states"],
                {
                    "scheduler": "unavailable",
                    "pipeline": "unavailable",
                    "workbook": "unavailable",
                    "adapter_mutation": "free",
                },
            )

            for lock_path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
            ):
                lock_path.write_text("", encoding="utf-8")
            snapshot = ExistingEngineAdapter(settings).snapshot()
            self.assertEqual(snapshot["run_snapshot"]["run_id"], run_id)
            queue = snapshot["run_snapshot"]["queue"]
            self.assertEqual(queue["decision_total"], 5)
            self.assertEqual(
                queue["decision_total_name"], "mutually_exclusive_decision_lanes"
            )
            self.assertEqual(
                queue["decision_total_parts"],
                {
                    "application_plus_outreach": 0,
                    "application_only": 2,
                    "outreach_only_today": 0,
                    "relationship_buffer": 0,
                    "follow_up": 3,
                    "skipped_internal": 0,
                },
            )
            self.assertNotIn("total", queue)
            self.assertEqual(snapshot["current_workspace"]["status"], "available")
            self.assertEqual(
                snapshot["current_workspace"]["application_queue"][
                    "priority_item_count"
                ],
                1,
            )
            self.assertEqual(
                snapshot["current_workspace"]["outreach_counts"]["contacts"],
                1,
            )
            self.assertNotIn(private_marker, json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()

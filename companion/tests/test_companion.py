from __future__ import annotations

import base64
import fcntl
import hashlib
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape, quoteattr

from recruiting_companion.api import _seal_legacy_static_root, make_server
from recruiting_companion.auth import TokenStore
from recruiting_companion.config import Settings
from recruiting_companion.existing_adapter import ExistingEngineAdapter
from recruiting_companion.operator_backend import OperatorBackend
from recruiting_companion.service import (
    CompanionService,
    ConflictError,
    NotFoundError,
    ValidationError,
)


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _write_minimal_xlsx(
    path: Path,
    sheets: dict[str, list[list[object]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook_sheets = []
    relationships = []
    worksheet_payloads: list[tuple[str, str]] = []
    for sheet_index, (name, rows) in enumerate(sheets.items(), start=1):
        relationship_id = f"rId{sheet_index}"
        workbook_sheets.append(
            f'<sheet name={quoteattr(name)} sheetId="{sheet_index}" '
            f'r:id="{relationship_id}"/>'
        )
        relationships.append(
            f'<Relationship Id="{relationship_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{sheet_index}.xml"/>'
        )
        row_xml = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for column_index, value in enumerate(row):
                reference = f"{_column_name(column_index)}{row_index}"
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>'
                    f"{escape(str(value))}</t></is></c>"
                )
            row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        worksheet_payloads.append(
            (
                f"xl/worksheets/sheet{sheet_index}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"><sheetData>'
                + "".join(row_xml)
                + "</sheetData></worksheet>",
            )
        )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(relationships)
        + "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        for filename, payload in worksheet_payloads:
            archive.writestr(filename, payload)


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root, user_id="test-user", port=8765)
        self.service = CompanionService(self.settings)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_environment_default_mode_only_applies_before_user_choice(self) -> None:
        existing_settings = Settings(
            data_dir=self.root,
            user_id="existing-default",
            default_mode="existing",
        )
        existing_service = CompanionService(existing_settings)
        self.assertEqual(existing_service.get_preferences(), {"mode": "existing"})
        existing_service.put_preferences(
            {"mode": "portable", "minimum_fit_score": 7.5}
        )
        restarted = CompanionService(
            Settings(
                data_dir=self.root,
                user_id="existing-default",
                default_mode="existing",
            )
        )
        self.assertEqual(restarted.get_preferences()["mode"], "portable")

        with patch.dict(
            "os.environ", {"RECRUITING_ENGINE_MODE": "invalid"}, clear=True
        ):
            with self.assertRaisesRegex(
                ValueError, "RECRUITING_ENGINE_MODE must be portable or existing"
            ):
                Settings.from_env()

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
        static_root = Path(self.temporary.name) / "static-export"
        (static_root / "app").mkdir(parents=True)
        (static_root / "assets").mkdir()
        (static_root / "index.html").write_text("<html>test</html>", encoding="utf-8")
        (static_root / "app" / "index.html").write_text(
            "<html>app</html>", encoding="utf-8"
        )
        (static_root / "assets" / "app.js").write_text("export {};", encoding="utf-8")
        (static_root / "release-compatibility.json").write_text(
            json.dumps(
                {
                    "schema": "recruiting_engine.static_compatibility",
                    "schema_version": 1,
                    "product_version": "1.3.0",
                    "compatible_companion_version": "0.3.0",
                }
            ),
            encoding="utf-8",
        )
        _seal_legacy_static_root(static_root)
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
            static_root=static_root,
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

    def test_paired_web_client_can_fetch_one_exact_report_without_caching(self) -> None:
        run_id = "20260711-120000"
        path = f"/api/v1/operator/reports/{run_id}/html"
        status, unauthorized, _ = self.request("GET", path)
        self.assertEqual(status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")

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
        exact_report = {
            "run_id": run_id,
            "html": "<!doctype html><title>Exact report</title>",
            "sha256": "a" * 64,
            "size_bytes": 48,
            "content_type": "text/html; charset=utf-8",
        }
        with patch.object(
            OperatorBackend,
            "exact_report_html",
            return_value=exact_report,
        ) as viewer:
            status, payload, headers = self.request(
                "GET",
                path,
                bearer=web_bearer,
                origin="https://axe-pat.github.io",
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"report": exact_report})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            headers["Access-Control-Allow-Origin"],
            "https://axe-pat.github.io",
        )
        viewer.assert_called_once_with(run_id)

    def test_authenticated_operator_progress_is_lightweight_and_no_store(self) -> None:
        path = "/api/v1/operator/progress"
        status, unauthorized, _ = self.request("GET", path)
        self.assertEqual(status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")

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
        expected = {
            "schema_version": "1.0",
            "generated_at": "2026-07-13T02:10:00+00:00",
            "current_run_progress": {
                "status": "running",
                "run_id": "20260713-010001",
            },
            "recent_jobs": [{"id": "opjob_123", "status": "running"}],
        }
        with patch.object(
            OperatorBackend,
            "progress",
            return_value=expected,
        ) as progress:
            status, payload, headers = self.request(
                "GET",
                path,
                bearer=web_bearer,
                origin="https://axe-pat.github.io",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        progress.assert_called_once_with()

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
        self.assertEqual(paired["expires_in"], TokenStore.WEB_SESSION_SECONDS)
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

        self.now[0] += TokenStore.WEB_SESSION_SECONDS + 1
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
            ("GET", "/api/v1/operator/overview", None),
            ("GET", "/api/v1/operator/progress", None),
            ("GET", "/api/v1/operator/capabilities", None),
            ("GET", "/api/v1/operator/assets", None),
            ("GET", "/api/v1/operator/review-targets", None),
            ("GET", "/api/v1/operator/reviews", None),
            ("GET", "/api/v1/operator/jobs", None),
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

        status, detail_missing, _ = self.request(
            "GET",
            "/api/v1/operator/review-targets/target_aaaaaaaaaaaaaaaaaaaaaaaa/detail",
            bearer=web_bearer,
        )
        self.assertEqual(status, 404)
        self.assertEqual(detail_missing["error"]["code"], "not_found")
        status, review_missing, _ = self.request(
            "GET",
            "/api/v1/operator/reviews/review_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            bearer=web_bearer,
        )
        self.assertEqual(status, 404)
        self.assertEqual(review_missing["error"]["code"], "not_found")
        status, injected_review, _ = self.request(
            "POST",
            "/api/v1/operator/reviews",
            bearer=web_bearer,
            body={
                "command_id": "nightly.run",
                "target_id": "target_aaaaaaaaaaaaaaaaaaaaaaaa",
                "path": "/tmp/injected",
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(injected_review["error"]["code"], "validation_error")

        status, blocked_job, _ = self.request(
            "POST",
            "/api/v1/operator/jobs",
            bearer=web_bearer,
            body={"command_id": "nightly.run", "confirmation": ""},
        )
        self.assertEqual(status, 422)
        self.assertEqual(blocked_job["error"]["code"], "validation_error")
        status, rejected_job, _ = self.request(
            "POST",
            "/api/v1/operator/jobs",
            bearer=web_bearer,
            body={"command_id": "arbitrary.shell", "confirmation": ""},
        )
        self.assertEqual(status, 422)
        self.assertEqual(rejected_job["error"]["code"], "validation_error")
        status, blocked_refresh, _ = self.request(
            "POST",
            "/api/v1/operator/jobs",
            bearer=web_bearer,
            body={
                "command_id": "accounts.refresh",
                "confirmation": "REFRESH_ACCOUNT_TRACKER",
                "parameters": {},
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(blocked_refresh["operator_job"]["status"], "blocked")
        self.assertEqual(
            blocked_refresh["operator_job"]["requested_scope"], "web"
        )
        status, injected, _ = self.request(
            "POST",
            "/api/v1/operator/jobs",
            bearer=web_bearer,
            body={
                "command_id": "application.resume.generate",
                "confirmation": "GENERATE_ONE_RESUME_WITH_MODEL_COST",
                "parameters": {"job_id": 1, "flags": ["--force"]},
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(injected["error"]["code"], "validation_error")

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


class OperatorBackendTestCase(unittest.TestCase):
    def test_exact_report_html_is_verified_bounded_and_daily_html_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outreach = root / "outreach"
            report_root = outreach / "workspace" / "reports" / "daily_html"
            report_root.mkdir(parents=True)
            settings = Settings(
                data_dir=root / "data",
                user_id="report-viewer-user",
                outreach_root=outreach,
            )
            settings.prepare()
            backend = OperatorBackend(settings)
            run_id = "20260711-120000"
            filename = f"{run_id}-daily-run-report.html"
            report_path = report_root / filename
            content = b"<!doctype html><title>Verified exact report</title>"
            report_path.write_bytes(content)
            evidence = {
                "state": "valid",
                "path": report_path.relative_to(outreach).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            projection = {
                "run_id": run_id,
                "evidence": {"outreach_html": evidence},
            }

            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[projection],
            ) as projections:
                report = backend.exact_report_html(run_id)
            self.assertEqual(report["run_id"], run_id)
            self.assertEqual(report["html"], content.decode("utf-8"))
            self.assertEqual(report["sha256"], evidence["sha256"])
            self.assertEqual(report["size_bytes"], len(content))
            self.assertEqual(report["content_type"], "text/html; charset=utf-8")
            projections.assert_called_once_with(limit=50)

            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[projection],
            ) as latest_projection:
                self.assertEqual(
                    backend._open_target("open.latest_report"),
                    report_path.resolve(strict=True),
                )
            latest_projection.assert_called_once_with(limit=1)

            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[projection],
            ):
                with self.assertRaises(NotFoundError):
                    backend.exact_report_html("20260711-120001")

                report_path.write_bytes(content + b" changed")
                with self.assertRaisesRegex(
                    ConflictError, "changed after verification"
                ):
                    backend.exact_report_html(run_id)
                report_path.write_bytes(content)

                evidence["path"] = f"workspace/reports/{filename}"
                with self.assertRaisesRegex(
                    ValidationError, "unavailable or unsafe"
                ):
                    backend.exact_report_html(run_id)

                evidence["path"] = f"workspace/reports/daily_html/latest/{filename}"
                with self.assertRaisesRegex(
                    ValidationError, "unavailable or unsafe"
                ):
                    backend.exact_report_html(run_id)

                evidence["path"] = report_path.relative_to(outreach).as_posix()
                evidence["size_bytes"] = 5 * 1024 * 1024 + 1
                with self.assertRaisesRegex(
                    ValidationError, "exceeds the viewer limit"
                ):
                    backend.exact_report_html(run_id)
                evidence["size_bytes"] = len(content)

                outside = root / "outside.html"
                outside.write_bytes(content)
                report_path.unlink()
                report_path.symlink_to(outside)
                with self.assertRaisesRegex(
                    ValidationError, "unavailable or unsafe"
                ):
                    backend.exact_report_html(run_id)

    def test_strict_audited_preflight_and_local_open_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            outreach = root / "outreach"
            runtime = root / "runtime"
            (resume / "discovery" / "scripts").mkdir(parents=True)
            (resume / "discovery" / "source_validation").mkdir()
            current_queue = (
                resume / "apps" / "Apply queues" / "current_apply_queue"
            )
            current_queue.mkdir(parents=True)
            (resume / "docs" / "career_workbench").mkdir(parents=True)
            (outreach / "workspace" / "comms_learning").mkdir(parents=True)
            runtime.mkdir()
            script = resume / "discovery" / "scripts" / "nightly_prompt.py"
            script.write_text(
                "import json\nprint(json.dumps({'status': 'valid'}))\n",
                encoding="utf-8",
            )
            attestation = root / "attestation.json"
            attestation.write_text("{}", encoding="utf-8")
            account_tracker = outreach / "workspace" / "account_tracker.xlsx"
            account_tracker.write_bytes(b"operator fixture")
            review = (
                outreach
                / "workspace"
                / "comms_learning"
                / "outcome_recommendation_review_2026-07-11.json"
            )
            review.write_text("{}", encoding="utf-8")
            settings = Settings(
                data_dir=root / "data",
                user_id="operator-user",
                resumegen_root=resume,
                outreach_root=outreach,
                runtime_dir=runtime,
                attestation_path=attestation,
                resume_python=Path(sys.executable),
            )
            settings.prepare()
            for path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
                current_queue.parent / ".current_apply_queue.lock",
            ):
                path.write_text("", encoding="utf-8")

            backend = OperatorBackend(settings)
            commands = {
                item["command_id"]: item
                for item in backend.capabilities()["commands"]
            }
            self.assertEqual(commands["production.preflight"]["status"], "available")
            self.assertEqual(commands["reports.daily.refresh"]["status"], "unavailable")
            self.assertEqual(commands["reports.sources.refresh"]["status"], "unavailable")
            self.assertEqual(commands["nightly.run"]["status"], "unavailable")
            self.assertIn(
                "reviewed actions are disabled",
                commands["nightly.run"]["reason"],
            )

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=b'PRIVATE OUTPUT {"status":"valid"}\n',
                stderr=b"",
            )
            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                return_value=completed,
            ) as runner:
                job = backend.submit_job(
                    command_id="production.preflight",
                    confirmation="RUN_PRODUCTION_PREFLIGHT",
                    requested_scope="local",
                )
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result_code"], "preflight_valid")
            self.assertEqual(job["returncode"], 0)
            self.assertEqual(job["stdout_lines"], 1)
            self.assertNotIn("PRIVATE OUTPUT", json.dumps(job))
            argv = runner.call_args.args[0]
            self.assertEqual(
                argv[1:],
                [
                    "discovery/scripts/nightly_prompt.py",
                    "--production-check-only",
                    "--production-attestation",
                    str(attestation.resolve()),
                ],
            )
            self.assertEqual(runner.call_args.kwargs["cwd"], resume.resolve())
            self.assertIs(runner.call_args.kwargs["shell"], False)

            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="nightly.run",
                    confirmation="RUN_REVIEWED_NIGHTLY",
                    requested_scope="web",
                )
            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="production.preflight",
                    confirmation="wrong",
                    requested_scope="local",
                )
            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="arbitrary.shell",
                    confirmation="",
                    requested_scope="local",
                )

            if Path("/usr/bin/open").is_file():
                open_completed = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=b"", stderr=b""
                )
                with patch(
                    "recruiting_companion.operator_backend.subprocess.run",
                    return_value=open_completed,
                ) as open_runner:
                    opened = backend.submit_job(
                        command_id="open.account_tracker",
                        confirmation="OPEN_ACCOUNT_TRACKER",
                        requested_scope="web",
                    )
                self.assertEqual(opened["status"], "completed")
                self.assertEqual(opened["result_code"], "local_open_requested")
                self.assertEqual(
                    open_runner.call_args.args[0],
                    ["/usr/bin/open", str(account_tracker.resolve())],
                )
                self.assertIs(open_runner.call_args.kwargs["shell"], False)

                account_tracker.unlink()
                outside = root / "outside.xlsx"
                outside.write_bytes(b"outside")
                account_tracker.symlink_to(outside)
                commands = {
                    item["command_id"]: item
                    for item in backend.capabilities()["commands"]
                }
                self.assertEqual(
                    commands["open.account_tracker"]["status"], "unavailable"
                )

            jobs = backend.list_jobs()
            self.assertGreaterEqual(len(jobs), 2)
            self.assertNotIn("PRIVATE OUTPUT", json.dumps(jobs))


class GuardedOperatorActionTestCase(unittest.TestCase):
    def test_parameter_guards_async_execution_busy_lock_and_fixed_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            outreach = root / "outreach"
            runtime = root / "runtime"
            validation = resume / "discovery" / "source_validation"
            validation.mkdir(parents=True)
            (resume / "discovery" / "scripts").mkdir()
            (resume / "apply_assist").mkdir()
            queue_root = (
                resume / "apps" / "Apply queues" / "current_apply_queue"
            )
            application_folder = queue_root / "jobs" / "one-role"
            application_folder.mkdir(parents=True)
            (application_folder / "jd.txt").write_text(
                "private job description", encoding="utf-8"
            )
            (resume / "jobs.py").write_text("# fixed fixture\n", encoding="utf-8")
            (resume / "apply_assist" / "build_apply_task.py").write_text(
                "# fixed fixture\n", encoding="utf-8"
            )
            (resume / ".env").write_text(
                "ANTHROPIC_API_KEY=test-model-key\n", encoding="utf-8"
            )
            (queue_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "queue_type": "current_apply_queue",
                        "ready_count": 1,
                        "manual_review_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            (queue_root / "priority_order.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 42,
                            "company": "Example Company",
                            "role_title": "Product Lead",
                            "status": "queued",
                            "queue_bucket": "new",
                            "fit_score": 8.4,
                            "priority_rank": 1,
                            "folder_path": "jobs/one-role",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (outreach / "workspace").mkdir(parents=True)
            (outreach / "main.py").write_text("# fixed fixture\n", encoding="utf-8")
            resume_python = resume / "venv" / "bin" / "python"
            outreach_python = outreach / ".venv" / "bin" / "python"
            resume_python.parent.mkdir(parents=True)
            outreach_python.parent.mkdir(parents=True)
            resume_python.symlink_to(sys.executable)
            outreach_python.symlink_to(sys.executable)
            runtime.mkdir()
            for path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
                queue_root.parent / ".current_apply_queue.lock",
            ):
                path.write_text("", encoding="utf-8")
            attestation = root / "attestation.json"
            attestation.write_text("{}", encoding="utf-8")
            settings = Settings(
                data_dir=root / "data",
                user_id="guarded-actions",
                resumegen_root=resume,
                outreach_root=outreach,
                runtime_dir=runtime,
                attestation_path=attestation,
                resume_python=resume_python,
                outreach_python=outreach_python,
            )
            backend = OperatorBackend(settings)
            self.assertEqual(
                settings.adapter_mutation_lock_path,
                runtime / "operator_mutation.lock",
            )
            self.assertTrue(settings.adapter_mutation_lock_path.is_file())

            commands = {
                item["command_id"]: item
                for item in backend.capabilities()["commands"]
            }
            resume_command = commands["application.resume.generate"]
            self.assertEqual(resume_command["status"], "available")
            self.assertTrue(resume_command["asynchronous"])
            self.assertEqual(
                resume_command["parameters_schema"]["required"], ["job_id"]
            )
            self.assertEqual(
                resume_command["confirmation_phrase"],
                "GENERATE_ONE_RESUME_WITH_MODEL_COST",
            )
            with settings.adapter_mutation_lock_path.open("r+b") as shared_lock:
                fcntl.flock(
                    shared_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                shared_status = backend.adapter.status()
                self.assertEqual(
                    shared_status["locks"]["adapter_mutation"], "busy"
                )
                shared_commands = {
                    item["command_id"]: item
                    for item in backend.capabilities()["commands"]
                }
                self.assertEqual(
                    shared_commands["application.resume.generate"]["status"],
                    "unavailable",
                )
                jobs_before_maintenance = len(backend.list_jobs())
                with self.assertRaisesRegex(
                    ConflictError,
                    "temporarily paused",
                ):
                    backend.submit_job(
                        command_id="production.preflight",
                        confirmation="RUN_PRODUCTION_PREFLIGHT",
                        requested_scope="local",
                    )
                self.assertEqual(
                    len(backend.list_jobs()),
                    jobs_before_maintenance,
                    "exclusive maintenance must reject before job insertion",
                )
                fcntl.flock(shared_lock.fileno(), fcntl.LOCK_UN)

            queue_lock_path = queue_root.parent / ".current_apply_queue.lock"
            with queue_lock_path.open("r+b") as queue_lock:
                fcntl.flock(
                    queue_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                queue_locked_status = backend.adapter.status()
                self.assertEqual(
                    queue_locked_status["locks"]["queue"], "busy"
                )
                queue_locked_assets = backend.assets()
                self.assertIn(
                    queue_locked_assets["current_apply_queue"]["status"],
                    {"busy", "unavailable"},
                )
                self.assertEqual(
                    queue_locked_assets["current_apply_queue"]["items"], []
                )
                queue_locked_commands = {
                    item["command_id"]: item
                    for item in backend.capabilities()["commands"]
                }
                self.assertEqual(
                    queue_locked_commands["application.apply_packet.build"][
                        "status"
                    ],
                    "unavailable",
                )
                blocked_by_queue = backend.submit_job(
                    command_id="application.apply_packet.build",
                    confirmation="BUILD_ONE_APPLY_PACKET",
                    parameters={"job_id": 42},
                    requested_scope="web",
                )
                self.assertEqual(blocked_by_queue["status"], "blocked")
                self.assertEqual(
                    blocked_by_queue["result_code"],
                    "capability_unavailable",
                )
                fcntl.flock(queue_lock.fileno(), fcntl.LOCK_UN)

            assets = backend.assets()
            queue_action_states = {
                item["command_id"]: item
                for item in assets["current_apply_queue"]["items"][0]["actions"]
            }
            self.assertEqual(
                queue_action_states["application.resume.generate"]["status"],
                "available",
            )
            self.assertEqual(
                queue_action_states["application.resume.generate"]["parameters"],
                {"job_id": 42},
            )

            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="application.resume.generate",
                    confirmation="GENERATE_ONE_RESUME_WITH_MODEL_COST",
                    parameters={"job_id": 42, "flags": "--force"},
                    requested_scope="web",
                )
            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="application.resume.generate",
                    confirmation="GENERATE_ONE_RESUME_WITH_MODEL_COST",
                    parameters={"job_id": "42"},
                    requested_scope="web",
                )
            with self.assertRaises(ValidationError):
                backend.submit_job(
                    command_id="application.resume.generate",
                    confirmation="wrong",
                    parameters={"job_id": 42},
                    requested_scope="web",
                )
            with self.assertRaisesRegex(ValidationError, "not present"):
                backend.submit_job(
                    command_id="application.apply_packet.build",
                    confirmation="BUILD_ONE_APPLY_PACKET",
                    parameters={"job_id": 999},
                    requested_scope="local",
                )

            started = threading.Event()
            release = threading.Event()

            def delayed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                started.set()
                release.wait(timeout=3)
                return subprocess.CompletedProcess(
                    args=args[0],
                    returncode=0,
                    stdout=b"PRIVATE MODEL OUTPUT\n",
                    stderr=b"",
                )

            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=delayed_run,
            ) as runner:
                before = time.monotonic()
                queued = backend.submit_job(
                    command_id="application.resume.generate",
                    confirmation="GENERATE_ONE_RESUME_WITH_MODEL_COST",
                    parameters={"job_id": 42},
                    requested_scope="web",
                )
                elapsed = time.monotonic() - before
                self.assertLess(elapsed, 1.0)
                self.assertIn(queued["status"], {"queued", "running"})
                self.assertTrue(started.wait(timeout=2))
                running = backend.get_job(queued["id"])
                self.assertEqual(running["status"], "running")
                argv = runner.call_args.args[0]
                self.assertEqual(argv[0], str(resume_python.absolute()))
                self.assertEqual(
                    argv[1:],
                    [
                        "jobs.py",
                        "--no-color",
                        "generate",
                        "--id",
                        "42",
                        "--resume-only",
                        "--budget-mode",
                        "--parallel",
                        "1",
                        "--timeout",
                        "2400",
                        "--model",
                        "claude-sonnet-4-6",
                    ],
                )
                self.assertNotIn("--force", argv)
                self.assertNotIn("--with-cl", argv)
                self.assertIs(runner.call_args.kwargs["shell"], False)
                self.assertEqual(
                    runner.call_args.kwargs["cwd"], resume.resolve()
                )
                release.set()
                completed = self._wait_for_job(backend, queued["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(
                completed["result_code"], "resume_generation_completed"
            )
            self.assertEqual(completed["parameters"], {"job_id": 42})
            self.assertNotIn("PRIVATE MODEL OUTPUT", json.dumps(completed))

            with (runtime / "nightly_scheduler.lock").open("r+b") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                blocked = backend.submit_job(
                    command_id="application.apply_packet.build",
                    confirmation="BUILD_ONE_APPLY_PACKET",
                    parameters={"job_id": 42},
                    requested_scope="local",
                )
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(
                    blocked["result_code"], "capability_unavailable"
                )
                with patch.object(
                    backend,
                    "capabilities",
                    return_value={
                        "commands": [
                            {
                                "command_id": "accounts.refresh",
                                "status": "available",
                            }
                        ]
                    },
                ):
                    raced = backend.submit_job(
                        command_id="accounts.refresh",
                        confirmation="REFRESH_ACCOUNT_TRACKER",
                        parameters={},
                        requested_scope="local",
                    )
                raced = self._wait_for_job(backend, raced["id"])
                self.assertEqual(raced["status"], "blocked")
                self.assertEqual(
                    raced["result_code"], "engine_locks_not_free"
                )
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

            if Path("/usr/bin/open").is_file():
                opened_process = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=b"", stderr=b""
                )
                with patch(
                    "recruiting_companion.operator_backend.subprocess.run",
                    return_value=opened_process,
                ) as open_runner:
                    opened = backend.submit_job(
                        command_id="open.application_folder",
                        confirmation="OPEN_APPLICATION_FOLDER",
                        parameters={"job_id": 42},
                        requested_scope="web",
                    )
                self.assertEqual(opened["status"], "completed")
                self.assertEqual(
                    open_runner.call_args.args[0],
                    ["/usr/bin/open", str(application_folder.resolve())],
                )

            summary_path = validation / "20260711-120000-nightly-pipeline-summary.json"
            source_path = validation / "20260711-120000-source-run-metrics.json"
            summary_path.write_text(
                json.dumps({"created_at": "2026-07-11T12:00:00Z"}),
                encoding="utf-8",
            )
            source_path.write_text("{}", encoding="utf-8")
            verified = {
                "run_id": "20260711-120000",
                "started_at": "2026-07-11T12:00:00+00:00",
                "evidence": {
                    "summary": {
                        "path": summary_path.relative_to(resume).as_posix()
                    },
                    "source_metrics": {
                        "path": source_path.relative_to(resume).as_posix()
                    },
                },
            }
            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[verified],
            ):
                daily_argv, daily_cwd, _, _ = backend._fixed_action_argv(
                    "reports.daily.refresh", {}
                )
                self.assertEqual(
                    daily_argv[1:],
                    [
                        "main.py",
                        "write-daily-run-report",
                        "--workspace",
                        "workspace",
                        "--since",
                        "2026-07-11T12:00:00Z",
                        "--nightly-summary",
                        str(summary_path.resolve()),
                        "--run-id",
                        "20260711-120000",
                    ],
                )
                self.assertEqual(daily_cwd, outreach.resolve())
                source_argv, _, _, _ = backend._fixed_action_argv(
                    "reports.sources.refresh", {}
                )
                self.assertEqual(
                    source_argv[1:],
                    [
                        "main.py",
                        "build-role-surface-report",
                        "--source-metrics",
                        str(source_path.resolve()),
                        "--run-id",
                        "20260711-120000",
                        "--workspace",
                        "workspace",
                    ],
                )

            plan_argv, _, _, _ = backend._fixed_action_argv(
                "outreach.plan.preview", {}
            )
            self.assertIn("build-track-2-daily-plan", plan_argv)
            self.assertNotIn("--execute", plan_argv)
            self.assertNotIn("--send-linkedin", plan_argv)
            account_argv, _, _, _ = backend._fixed_action_argv(
                "accounts.refresh", {}
            )
            self.assertEqual(account_argv[0], str(outreach_python.absolute()))
            self.assertEqual(
                account_argv[1:],
                [
                    "main.py",
                    "account-tracker",
                    "--workspace",
                    "workspace",
                    "--output",
                    "workspace/account_tracker.xlsx",
                ],
            )
            cadence_argv, _, _, _ = backend._fixed_action_argv(
                "reports.cadence.refresh", {}
            )
            self.assertEqual(
                cadence_argv[1:],
                [
                    "main.py",
                    "build-outreach-cadence-report",
                    "--workspace",
                    "workspace",
                ],
            )
            outcome_argv, _, _, _ = backend._fixed_action_argv(
                "reports.outcomes.refresh", {}
            )
            self.assertEqual(
                outcome_argv[1:],
                [
                    "main.py",
                    "build-outcome-learning-report",
                    "--workspace",
                    "workspace",
                ],
            )
            lab_argv, _, _, _ = backend._fixed_action_argv(
                "communications.lab.refresh", {}
            )
            self.assertEqual(
                lab_argv[1:],
                [
                    "main.py",
                    "build-communication-lab",
                    "--workspace",
                    "workspace",
                    "--resume-root",
                    str(resume.resolve()),
                ],
            )
            packet_argv, _, _, _ = backend._fixed_action_argv(
                "application.apply_packet.build", {"job_id": 42}
            )
            self.assertEqual(
                packet_argv[1:],
                [
                    "apply_assist/build_apply_task.py",
                    "--job-id",
                    "42",
                    "--queue-json",
                    "apps/Apply queues/current_apply_queue/priority_order.json",
                    "--out-dir",
                    "apply_assist/tasks",
                ],
            )
            self.assertNotIn("rtrvr_apply_runner.py", packet_argv)
            self.assertNotIn("--live", packet_argv)

    def test_durable_exact_target_review_approval_and_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            outreach = root / "outreach"
            runtime = root / "runtime"
            queue_root = resume / "apps" / "Apply queues" / "current_apply_queue"
            folder = queue_root / "jobs" / "one-role"
            folder.mkdir(parents=True)
            (folder / "jd.txt").write_text("private role", encoding="utf-8")
            resume_file = folder / "resume_one.pdf"
            resume_file.write_bytes(b"reviewed resume v1")
            (queue_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "queue_type": "current_apply_queue",
                        "ready_count": 1,
                        "manual_review_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            (queue_root / "priority_order.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 42,
                            "company": "Example Company",
                            "role_title": "Product Lead",
                            "status": "generated",
                            "queue_bucket": "new",
                            "folder_path": "jobs/one-role",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            transition_script = resume / "discovery" / "scripts" / "transition_application.py"
            transition_script.parent.mkdir(parents=True)
            transition_script.write_text("# fixed lifecycle fixture\n", encoding="utf-8")
            nightly_script = resume / "discovery" / "scripts" / "nightly_prompt.py"
            nightly_script.write_text("# fixed nightly wrapper fixture\n", encoding="utf-8")
            contract_args = (
                "--cycle-config offcycle_light --generate --prepare-outreach "
                "--execute-sends --target-sends auto "
                "--execute-track-2-daily-plan --track-2-send-linkedin"
            )
            contract_script = (
                resume / "discovery" / "scripts" / "nightly_contract.py"
            )
            contract_script.write_text(
                "import sys\n"
                f"print({contract_args!r}) if sys.argv[1:] == ['print'] else sys.exit(2)\n",
                encoding="utf-8",
            )
            (resume / "discovery" / "scripts" / "run_nightly_pipeline.py").write_text(
                "# fixed nightly pipeline fixture\n", encoding="utf-8"
            )
            resume_python = resume / "venv" / "bin" / "python"
            resume_python.parent.mkdir(parents=True)
            resume_python.symlink_to(sys.executable)
            (outreach / "workspace").mkdir(parents=True)
            runtime.mkdir()
            for path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
                queue_root.parent / ".current_apply_queue.lock",
            ):
                path.write_text("", encoding="utf-8")
            attestation = root / "attestation.json"
            attestation.write_text('{"release":"exact"}', encoding="utf-8")
            backend = OperatorBackend(
                Settings(
                    data_dir=root / "data",
                    user_id="review-ledger",
                    resumegen_root=resume,
                    outreach_root=outreach,
                    runtime_dir=runtime,
                    attestation_path=attestation,
                    resume_python=resume_python,
                    allow_reviewed_actions=True,
                )
            )

            lanes = {
                lane["command_id"]: lane
                for lane in backend.review_targets()["lanes"]
            }
            self.assertEqual(
                lanes["nightly.run"]["state"], "review_stage_available"
            )
            self.assertEqual(
                lanes["application.assist.fill_to_review"]["targets_total"],
                0,
            )
            self.assertIn(
                "tool-enforced final-submit interceptor",
                lanes["application.assist.fill_to_review"]["reason"],
            )
            self.assertEqual(
                lanes["application.status.applied"]["targets_total"], 1
            )
            self.assertEqual(
                lanes["application.status.closed"]["targets_total"], 1
            )
            self.assertEqual(
                lanes["outreach.linkedin.send"]["targets_total"], 0
            )
            self.assertEqual(lanes["outreach.email.send"]["targets_total"], 0)

            nightly_target = lanes["nightly.run"]["targets"][0]
            private_target = backend._nightly_review_target()
            self.assertEqual(
                private_target["_execution_binding"]["pipeline_args_string"],
                contract_args,
            )
            self.assertEqual(
                len(private_target["_execution_binding"]["nightly_contract_sha256"]),
                64,
            )
            self.assertEqual(
                len(
                    private_target["_execution_binding"][
                        "nightly_contract_stdout_sha256"
                    ]
                ),
                64,
            )
            expiring = backend.create_review(
                command_id="nightly.run",
                target_id=nightly_target["target_id"],
                requested_scope="web",
            )
            with backend.db.transaction() as connection:
                connection.execute(
                    "UPDATE operator_reviews SET expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00Z", expiring["id"]),
                )
            self.assertEqual(backend.get_review(expiring["id"])["state"], "expired")

            expired_approval = backend.create_review(
                command_id="nightly.run",
                target_id=nightly_target["target_id"],
                requested_scope="web",
            )
            expired_approval = backend.transition_review(
                expired_approval["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="web",
            )
            expired_approval = backend.transition_review(
                expired_approval["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="web",
            )
            with backend.db.transaction() as connection:
                connection.execute(
                    "UPDATE operator_reviews SET expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00Z", expired_approval["id"]),
                )
            with self.assertRaisesRegex(ConflictError, "current approved review"):
                backend.submit_job(
                    command_id="nightly.run",
                    confirmation="RUN_REVIEWED_NIGHTLY",
                    parameters={
                        "review_id": expired_approval["id"],
                        "target_id": nightly_target["target_id"],
                    },
                    requested_scope="web",
                )
            self.assertEqual(
                backend._review_row(expired_approval["id"])["state"], "expired"
            )

            nightly_review = backend.create_review(
                command_id="nightly.run",
                target_id=nightly_target["target_id"],
                requested_scope="web",
            )
            nightly_review = backend.transition_review(
                nightly_review["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="web",
            )
            nightly_review = backend.transition_review(
                nightly_review["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="web",
            )

            nightly_calls = 0

            def is_contract_print(argv: object) -> bool:
                return (
                    isinstance(argv, list)
                    and len(argv) >= 3
                    and argv[1] == "discovery/scripts/nightly_contract.py"
                    and argv[2] == "print"
                )

            def contract_print(argv: object) -> subprocess.CompletedProcess:
                return subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(contract_args + "\n").encode(),
                    stderr=b"",
                )

            def nightly_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                nonlocal nightly_calls
                if is_contract_print(args[0]):
                    return contract_print(args[0])
                nightly_calls += 1
                if nightly_calls == 1:
                    self.assertIn("--production-check-only", args[0])
                    self.assertEqual(
                        backend.get_review(nightly_review["id"])["state"], "approved"
                    )
                    return subprocess.CompletedProcess(
                        args=args[0], returncode=0, stdout=b"preflight valid\n", stderr=b""
                    )
                self.assertEqual(
                    backend.get_review(nightly_review["id"])["state"], "consumed"
                )
                with backend.settings.adapter_mutation_lock_path.open("r+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return subprocess.CompletedProcess(
                    args=args[0], returncode=0, stdout=b"production nightly\n", stderr=b""
                )

            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=nightly_run,
            ) as nightly_runner, patch.object(
                backend,
                "_new_verified_nightly_result",
                return_value={
                    "run_id": "20260712-190000",
                    "health": "complete",
                    "report_sha256": "a" * 64,
                    "delivery_mode": "full_delivery",
                },
            ):
                nightly_job = backend.submit_job(
                    command_id="nightly.run",
                    confirmation="RUN_REVIEWED_NIGHTLY",
                    parameters={
                        "review_id": nightly_review["id"],
                        "target_id": nightly_target["target_id"],
                    },
                    requested_scope="web",
                )
                nightly_job = self._wait_for_job(backend, nightly_job["id"])
            self.assertEqual(nightly_job["result_code"], "reviewed_nightly_completed")
            self.assertEqual(nightly_calls, 2)
            nightly_argv = nightly_runner.call_args_list[-1].args[0]
            self.assertEqual(nightly_argv[1], "discovery/scripts/nightly_prompt.py")
            self.assertIn("--require-production-attestation", nightly_argv)
            self.assertIn("--require-live-delivery-contract", nightly_argv)
            self.assertIn("--pipeline-args", nightly_argv)
            pipeline_args = nightly_argv[nightly_argv.index("--pipeline-args") + 1]
            self.assertIn("--execute-track-2-daily-plan", pipeline_args)
            self.assertIn("--target-sends auto", pipeline_args)
            self.assertIn("--execute-sends", pipeline_args)
            self.assertIn("--track-2-send-linkedin", pipeline_args)
            self.assertNotIn("--execute-linkedin-followups", pipeline_args)
            self.assertIs(nightly_runner.call_args_list[-1].kwargs["shell"], False)
            self.assertEqual(nightly_job["preflight_returncode"], 0)
            self.assertTrue(nightly_job["preflight_stdout_sha256"])
            self.assertEqual(nightly_job["result_run_id"], "20260712-190000")
            self.assertEqual(nightly_job["result_health"], "complete")
            self.assertEqual(nightly_job["result_report_sha256"], "a" * 64)
            self.assertEqual(nightly_job["result_delivery_mode"], "full_delivery")

            incomplete_review = backend.create_review(
                command_id="nightly.run",
                target_id=nightly_target["target_id"],
                requested_scope="web",
            )
            incomplete_review = backend.transition_review(
                incomplete_review["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="web",
            )
            incomplete_review = backend.transition_review(
                incomplete_review["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="web",
            )
            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=lambda argv, **kwargs: (
                    contract_print(argv)
                    if is_contract_print(argv)
                    else subprocess.CompletedProcess(
                        args=argv,
                        returncode=0,
                        stdout=b"process complete\n",
                        stderr=b"",
                    )
                ),
            ), patch.object(
                backend,
                "_new_verified_nightly_result",
                return_value={
                    "run_id": "20260712-193000",
                    "health": "attention",
                    "report_sha256": "b" * 64,
                    "delivery_mode": "full_delivery",
                },
            ):
                incomplete_job = backend.submit_job(
                    command_id="nightly.run",
                    confirmation="RUN_REVIEWED_NIGHTLY",
                    parameters={
                        "review_id": incomplete_review["id"],
                        "target_id": nightly_target["target_id"],
                    },
                    requested_scope="web",
                )
                incomplete_job = self._wait_for_job(
                    backend, incomplete_job["id"]
                )
            self.assertEqual(incomplete_job["status"], "failed")
            self.assertEqual(
                incomplete_job["result_code"], "reviewed_nightly_incomplete"
            )
            self.assertEqual(incomplete_job["returncode"], 0)
            self.assertEqual(incomplete_job["result_run_id"], "20260712-193000")

            failed_review = backend.create_review(
                command_id="nightly.run",
                target_id=nightly_target["target_id"],
                requested_scope="web",
            )
            failed_review = backend.transition_review(
                failed_review["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="web",
            )
            failed_review = backend.transition_review(
                failed_review["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="web",
            )
            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=lambda argv, **kwargs: (
                    contract_print(argv)
                    if is_contract_print(argv)
                    else subprocess.CompletedProcess(
                        args=argv,
                        returncode=78,
                        stdout=b"",
                        stderr=b"dirty checkout",
                    )
                ),
            ):
                failed_job = backend.submit_job(
                    command_id="nightly.run",
                    confirmation="RUN_REVIEWED_NIGHTLY",
                    parameters={
                        "review_id": failed_review["id"],
                        "target_id": nightly_target["target_id"],
                    },
                    requested_scope="web",
                )
                failed_job = self._wait_for_job(backend, failed_job["id"])
            self.assertEqual(
                failed_job["result_code"], "reviewed_nightly_preflight_failed"
            )
            self.assertEqual(
                backend.get_review(failed_review["id"])["state"], "approved"
            )

            changed_contract_args = (
                contract_args + " --outreach-resolve-limit 21"
            )
            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(changed_contract_args + "\n").encode(),
                    stderr=b"",
                ),
            ):
                with self.assertRaisesRegex(
                    ConflictError, "approved artifact changed"
                ):
                    backend.submit_job(
                        command_id="nightly.run",
                        confirmation="RUN_REVIEWED_NIGHTLY",
                        parameters={
                            "review_id": failed_review["id"],
                            "target_id": nightly_target["target_id"],
                        },
                        requested_scope="web",
                    )
            self.assertEqual(
                backend.get_review(failed_review["id"])["state"], "stale"
            )

            target = lanes["application.status.applied"]["targets"][0]
            detail = backend.get_review_target_detail(target["target_id"])
            self.assertEqual(detail["job_id"], 42)
            self.assertIsNone(detail["recipient"])
            self.assertIsNone(detail["draft_text"])
            review = backend.create_review(
                command_id="application.status.applied",
                target_id=target["target_id"],
                requested_scope="web",
            )
            self.assertEqual(review["state"], "pending")
            self.assertEqual(len(review["events"]), 1)
            with self.assertRaises(ValidationError):
                backend.transition_review(
                    review["id"],
                    transition="review",
                    confirmation="wrong",
                    requested_scope="web",
                )
            review = backend.transition_review(
                review["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="web",
            )
            self.assertEqual(review["state"], "reviewed")

            resume_file.write_bytes(b"reviewed resume v2")
            with self.assertRaisesRegex(ConflictError, "no longer current"):
                backend.transition_review(
                    review["id"],
                    transition="approve",
                    confirmation="APPROVE_EXACT_TARGET",
                    requested_scope="web",
                )
            self.assertEqual(backend.get_review(review["id"])["state"], "stale")

            refreshed_lanes = {
                lane["command_id"]: lane
                for lane in backend.review_targets()["lanes"]
            }
            refreshed_target = refreshed_lanes[
                "application.status.applied"
            ]["targets"][0]
            self.assertNotEqual(refreshed_target["target_id"], target["target_id"])
            status_target = refreshed_target
            status_review = backend.create_review(
                command_id="application.status.applied",
                target_id=status_target["target_id"],
                requested_scope="local",
            )
            duplicate_review = backend.create_review(
                command_id="application.status.applied",
                target_id=status_target["target_id"],
                requested_scope="local",
            )
            self.assertEqual(duplicate_review["id"], status_review["id"])
            with backend.db.connect() as connection:
                review_indexes = {
                    row["name"]: bool(row["unique"])
                    for row in connection.execute(
                        "PRAGMA index_list(operator_reviews)"
                    ).fetchall()
                }
            self.assertTrue(
                review_indexes.get("idx_operator_reviews_one_active")
            )
            status_review = backend.transition_review(
                status_review["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="local",
            )
            status_review = backend.transition_review(
                status_review["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="local",
            )
            commands = {
                item["command_id"]: item
                for item in backend.capabilities()["commands"]
            }
            self.assertEqual(
                commands["application.status.applied"]["status"], "available"
            )
            self.assertEqual(
                commands["application.assist.fill_to_review"]["status"],
                "unavailable",
            )
            self.assertEqual(
                commands["application.assist.fill_to_review"][
                    "execution_contract"
                ],
                "blocked_final_submit_guard",
            )

            lifecycle_calls = 0
            def lifecycle_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                nonlocal lifecycle_calls
                if is_contract_print(args[0]):
                    return contract_print(args[0])
                lifecycle_calls += 1
                if "--production-check-only" in args[0]:
                    self.assertEqual(
                        backend.get_review(status_review["id"])["state"],
                        "approved",
                    )
                    return subprocess.CompletedProcess(
                        args=args[0], returncode=0, stdout=b"preflight valid\n", stderr=b""
                    )
                self.assertEqual(
                    backend.get_review(status_review["id"])["state"], "consumed"
                )
                return subprocess.CompletedProcess(
                    args=args[0], returncode=0, stdout=b'{"status":"applied"}\n', stderr=b""
                )

            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=lifecycle_run,
            ) as runner:
                lifecycle_job = backend.submit_job(
                    command_id="application.status.applied",
                    confirmation="ARCHIVE_ONE_REVIEWED_APPLICATION_AS_APPLIED",
                    parameters={
                        "review_id": status_review["id"],
                        "target_id": status_target["target_id"],
                    },
                    requested_scope="local",
                )
                lifecycle_job = self._wait_for_job(backend, lifecycle_job["id"])
            self.assertEqual(lifecycle_job["status"], "completed")
            self.assertEqual(lifecycle_calls, 2)
            lifecycle_argvs = [
                call.args[0]
                for call in runner.call_args_list
                if not is_contract_print(call.args[0])
            ]
            self.assertIn(
                "--production-check-only",
                lifecycle_argvs[0],
            )
            self.assertEqual(
                lifecycle_job["result_code"], "application_archived_applied"
            )
            self.assertEqual(
                runner.call_args.args[0],
                [
                    str(resume_python.absolute()),
                    "discovery/scripts/transition_application.py",
                    "--id",
                    "42",
                    "--status",
                    "applied",
                    "--confirm",
                    "APPLY 42",
                    "--external-operator-lock",
                    "--json",
                ],
            )
            self.assertIs(runner.call_args.kwargs["shell"], False)
            self.assertEqual(
                backend.get_review(status_review["id"])["state"], "consumed"
            )
            replay = backend.submit_job
            with self.assertRaises(ConflictError):
                replay(
                    command_id="application.status.applied",
                    confirmation="ARCHIVE_ONE_REVIEWED_APPLICATION_AS_APPLIED",
                    parameters={
                        "review_id": status_review["id"],
                        "target_id": status_target["target_id"],
                    },
                    requested_scope="local",
                )

            preflight_blocked = backend.create_review(
                command_id="application.status.applied",
                target_id=status_target["target_id"],
                requested_scope="local",
            )
            preflight_blocked = backend.transition_review(
                preflight_blocked["id"],
                transition="review",
                confirmation="REVIEW_EXACT_TARGET",
                requested_scope="local",
            )
            preflight_blocked = backend.transition_review(
                preflight_blocked["id"],
                transition="approve",
                confirmation="APPROVE_EXACT_TARGET",
                requested_scope="local",
            )
            with patch(
                "recruiting_companion.operator_backend.subprocess.run",
                side_effect=lambda argv, **kwargs: (
                    contract_print(argv)
                    if is_contract_print(argv)
                    else subprocess.CompletedProcess(
                        args=argv,
                        returncode=78,
                        stdout=b"",
                        stderr=b"dirty release",
                    )
                ),
            ) as blocked_runner:
                blocked_job = backend.submit_job(
                    command_id="application.status.applied",
                    confirmation="ARCHIVE_ONE_REVIEWED_APPLICATION_AS_APPLIED",
                    parameters={
                        "review_id": preflight_blocked["id"],
                        "target_id": status_target["target_id"],
                    },
                    requested_scope="local",
                )
                blocked_job = self._wait_for_job(backend, blocked_job["id"])
            self.assertEqual(
                len(
                    [
                        call
                        for call in blocked_runner.call_args_list
                        if not is_contract_print(call.args[0])
                    ]
                ),
                1,
            )
            self.assertEqual(
                blocked_job["result_code"], "reviewed_action_preflight_failed"
            )
            self.assertEqual(
                backend.get_review(preflight_blocked["id"])["state"],
                "approved",
            )

    def test_reviewed_email_completion_requires_exact_send_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outreach = root / "outreach"
            artifacts = outreach / "artifacts"
            private = root / "private"
            artifacts.mkdir(parents=True)
            private.mkdir()
            draft_path = private / "approved-email.json"
            draft_path.write_text('{"results": []}\n', encoding="utf-8")
            expected = {
                "organization_id": "org-1",
                "contact_id": "contact-1",
                "email": "person@example.test",
                "subject": "Reviewed subject",
                "body": "Reviewed body",
            }
            result_path = artifacts / "track-2-email-send-results-exact.json"

            def completed_for(payload: dict[str, object]) -> subprocess.CompletedProcess:
                result_path.write_text(json.dumps(payload), encoding="utf-8")
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=f"Eligible: 1; held: 0; sent: 1\nArtifact: {result_path}\n".encode(),
                    stderr=b"",
                )

            sent_payload: dict[str, object] = {
                "source_artifact": str(draft_path),
                "execute": True,
                "eligible": 1,
                "held": 0,
                "sent": 1,
                "results": [{**expected, "delivery_status": "sent"}],
            }
            self.assertEqual(
                OperatorBackend._reviewed_email_delivery_evidence(
                    completed_for(sent_payload),
                    outreach_root=outreach,
                    draft_path=draft_path,
                    expected_draft=expected,
                ),
                "sent",
            )

            held_payload = {
                **sent_payload,
                "eligible": 0,
                "held": 1,
                "sent": 0,
                "results": [
                    {**expected, "delivery_status": "cadence_blocked"}
                ],
            }
            self.assertEqual(
                OperatorBackend._reviewed_email_delivery_evidence(
                    completed_for(held_payload),
                    outreach_root=outreach,
                    draft_path=draft_path,
                    expected_draft=expected,
                ),
                "not_sent",
            )

            contradictory_payload = {
                **sent_payload,
                "sent": 0,
                "results": [{**expected, "delivery_status": "sent"}],
            }
            self.assertEqual(
                OperatorBackend._reviewed_email_delivery_evidence(
                    completed_for(contradictory_payload),
                    outreach_root=outreach,
                    draft_path=draft_path,
                    expected_draft=expected,
                ),
                "unknown",
            )

    def test_exact_run_outreach_review_detail_and_pointer_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "resume"
            outreach = root / "outreach"
            runtime = root / "runtime"
            validation = resume / "discovery" / "source_validation"
            artifacts = outreach / "artifacts"
            workspace = outreach / "workspace"
            validation.mkdir(parents=True)
            artifacts.mkdir(parents=True)
            workspace.mkdir()
            reviewed_module = outreach / "src" / "outreach" / "reviewed_linkedin.py"
            reviewed_module.parent.mkdir(parents=True)
            reviewed_module.write_text("# reviewed LinkedIn fixture\n", encoding="utf-8")
            outreach_python = outreach / ".venv" / "bin" / "python"
            outreach_python.parent.mkdir(parents=True)
            outreach_python.symlink_to(sys.executable)
            nightly_wrapper = resume / "discovery" / "scripts" / "nightly_prompt.py"
            nightly_wrapper.parent.mkdir(parents=True)
            nightly_wrapper.write_text("# production preflight fixture\n", encoding="utf-8")
            resume_python = resume / "venv" / "bin" / "python"
            resume_python.parent.mkdir(parents=True)
            resume_python.symlink_to(sys.executable)
            (outreach / "main.py").write_text("# Outreach fixture\n", encoding="utf-8")
            runtime.mkdir()
            queue_parent = resume / "apps" / "Apply queues"
            queue_parent.mkdir(parents=True)
            for path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
                queue_parent / ".current_apply_queue.lock",
            ):
                path.write_text("", encoding="utf-8")
            attestation = root / "attestation.json"
            attestation.write_text("{}", encoding="utf-8")

            invite_path = artifacts / "20260711-invite-pipeline.json"
            invite_path.write_text(
                json.dumps(
                    {
                        "company": "Private Invite Co",
                        "company_mode": True,
                        "results": [
                            {
                                "linkedin_url": "https://www.linkedin.com/in/private-invite",
                                "name": "Private Invite Person",
                                "note": "PRIVATE INVITE NOTE",
                                "score": 91,
                                "note_qc": {"verdict": "send"},
                                "target_company_match": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            followup_path = workspace / "20260711-followups.json"
            followup_path.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "contact_id": "contact-1",
                                "thread_id": "thread-1",
                                "linkedin_url": "https://linkedin.com/in/private-followup",
                                "name": "Private Followup Person",
                                "company": "Private Followup Co",
                                "draft_message": "PRIVATE FOLLOWUP DRAFT",
                                "send_recommendation": "send",
                                "communication_recommendation": "send",
                                "latest_message": "PRIVATE LATEST INBOUND",
                                "message_window": ["context"],
                                "draft_kind": "reply",
                                "source_status": "ready",
                                "thread_url": "https://linkedin.com/messaging/thread/thread-1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            email_path = workspace / "20260711-email-drafts.json"
            email_path.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "organization_id": "org-1",
                                "contact_id": "contact-2",
                                "email": "private@example.test",
                                "subject": "PRIVATE SUBJECT",
                                "body": "PRIVATE EMAIL BODY",
                                "company": "Private Email Co",
                                "name": "Private Email Person",
                                "cadence_action": "initial",
                                "communication_review": {"verdict": "pass"},
                                "craft_review": {"verdict": "pass"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manifest_path = validation / "20260711-120000-daily-engine-manifest.json"

            def write_manifest(email_pointer: str | Path) -> dict[str, object]:
                manifest = {
                    "run_id": "20260711-120000",
                    "track_2_phase_artifacts": [str(invite_path)],
                    "track_2_phase_results": [
                        {
                            "phase": "5_send_linkedin_invites",
                            "runs": [
                                {
                                    "company": "Private Invite Co",
                                    "pipeline_artifact": "artifacts/20260711-invite-pipeline.json",
                                    "effective_min_score": 80,
                                    "status": "ready",
                                    "send_artifact": None,
                                }
                            ],
                        }
                    ],
                    "linkedin_followup_draft_artifacts": [str(followup_path)],
                    "track_2_email_draft_artifacts": [str(email_pointer)],
                }
                content = json.dumps(manifest).encode("utf-8")
                manifest_path.write_bytes(content)
                return {
                    "run_id": "20260711-120000",
                    "evidence": {
                        "daily_manifest": {
                            "path": manifest_path.relative_to(resume).as_posix(),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    },
                }

            settings = Settings(
                data_dir=root / "data",
                user_id="outreach-review",
                resumegen_root=resume,
                outreach_root=outreach,
                runtime_dir=runtime,
                attestation_path=attestation,
                resume_python=resume_python,
                outreach_python=outreach_python,
                allow_reviewed_actions=True,
            )
            backend = OperatorBackend(settings)
            projection = write_manifest(email_path)
            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[projection],
            ):
                records, reasons = backend._outreach_review_target_records()
                self.assertEqual(reasons["outreach.linkedin.send"], [])
                self.assertEqual(reasons["outreach.email.send"], [])
                self.assertEqual(
                    sum(item["command_id"] == "outreach.linkedin.send" for item in records),
                    2,
                )
                email_target = next(
                    item for item in records if item["command_id"] == "outreach.email.send"
                )
                public = backend._public_review_target(email_target)
                self.assertNotIn("PRIVATE EMAIL BODY", json.dumps(public))
                self.assertNotIn("private@example.test", json.dumps(public))
                detail = backend.get_review_target_detail(email_target["target_id"])
                self.assertEqual(detail["recipient"], "Private Email Person <private@example.test>")
                self.assertEqual(detail["subject"], "PRIVATE SUBJECT")
                self.assertEqual(detail["draft_text"], "PRIVATE EMAIL BODY")
                edited = backend.create_review(
                    command_id="outreach.email.send",
                    target_id=email_target["target_id"],
                    requested_scope="web",
                    reviewed_subject="EDITED PRIVATE SUBJECT",
                    reviewed_text="EDITED PRIVATE EMAIL BODY",
                )
                edited_private, _ = backend.get_review_detail(edited["id"])
                self.assertEqual(
                    edited_private["reviewed_subject"], "EDITED PRIVATE SUBJECT"
                )
                self.assertEqual(
                    edited_private["reviewed_text"], "EDITED PRIVATE EMAIL BODY"
                )
                self.assertNotIn(
                    "EDITED PRIVATE EMAIL BODY", json.dumps(backend.list_reviews())
                )
                self.assertNotIn(
                    "EDITED PRIVATE EMAIL BODY",
                    json.dumps(backend.get_review(edited["id"])),
                )
                edited = backend.transition_review(
                    edited["id"],
                    transition="review",
                    confirmation="REVIEW_EXACT_TARGET",
                    requested_scope="web",
                )
                edited = backend.transition_review(
                    edited["id"],
                    transition="approve",
                    confirmation="APPROVE_EXACT_TARGET",
                    requested_scope="web",
                )
                approved_sha = edited["artifact_sha256"]
                updated, updated_target = backend.update_review_content(
                    edited["id"],
                    reviewed_subject="SECOND PRIVATE SUBJECT",
                    reviewed_text="SECOND PRIVATE EMAIL BODY",
                    confirmation="UPDATE_EXACT_REVIEW_CONTENT",
                    requested_scope="web",
                )
                self.assertEqual(updated["state"], "pending")
                self.assertIsNone(updated["approved_at"])
                self.assertNotEqual(updated["artifact_sha256"], approved_sha)
                self.assertEqual(updated["reviewed_subject"], "SECOND PRIVATE SUBJECT")
                self.assertEqual(updated_target["recipient"], detail["recipient"])
                with self.assertRaises(ValidationError):
                    backend.update_review_content(
                        edited["id"],
                        reviewed_subject="THIRD SUBJECT",
                        reviewed_text="THIRD BODY",
                        confirmation="wrong",
                        requested_scope="web",
                    )

                invite_target = next(
                    item
                    for item in records
                    if item["target_type"] == "linkedin_invite"
                )
                linkedin_review = backend.create_review(
                    command_id="outreach.linkedin.send",
                    target_id=invite_target["target_id"],
                    requested_scope="web",
                    reviewed_text="EDITED PRIVATE LINKEDIN NOTE",
                )
                linkedin_review = backend.transition_review(
                    linkedin_review["id"],
                    transition="review",
                    confirmation="REVIEW_EXACT_TARGET",
                    requested_scope="web",
                )

                linkedin_calls: list[list[str]] = []

                def reviewed_linkedin_run(
                    *args: object, **kwargs: object
                ) -> subprocess.CompletedProcess:
                    argv = list(args[0])
                    linkedin_calls.append(argv)
                    if "--production-check-only" in argv:
                        pass
                    elif "preview" in argv:
                        output = Path(argv[argv.index("--output") + 1])
                        output.write_text(
                            json.dumps({"proposal_sha256": "a" * 64}),
                            encoding="utf-8",
                        )
                    elif "approve" in argv:
                        output = Path(argv[argv.index("--approval-file") + 1])
                        output.write_text(
                            json.dumps(
                                {
                                    "proposal_sha256": "a" * 64,
                                    "approval_sha256": "b" * 64,
                                }
                            ),
                            encoding="utf-8",
                        )
                    else:
                        output = Path(argv[argv.index("--receipt-file") + 1])
                        output.write_text(
                            json.dumps(
                                {
                                    "status": "execution_completed",
                                    "reconciliation_required": False,
                                    "approval_sha256": "b" * 64,
                                    "proposal_sha256": "a" * 64,
                                }
                            ),
                            encoding="utf-8",
                        )
                    return subprocess.CompletedProcess(
                        args=argv, returncode=0, stdout=b"private CLI output\n", stderr=b""
                    )

                with patch(
                    "recruiting_companion.operator_backend.subprocess.run",
                    side_effect=reviewed_linkedin_run,
                ):
                    linkedin_review = backend.transition_review(
                        linkedin_review["id"],
                        transition="approve",
                        confirmation="APPROVE_EXACT_TARGET",
                        requested_scope="web",
                    )
                    self.assertTrue(linkedin_review["execution_prepared"])
                    linkedin_job = backend.submit_job(
                        command_id="outreach.linkedin.send",
                        confirmation="SEND_ONE_REVIEWED_LINKEDIN_MESSAGE",
                        parameters={
                            "review_id": linkedin_review["id"],
                            "target_id": invite_target["target_id"],
                        },
                        requested_scope="web",
                    )
                    linkedin_job = self._wait_for_job(
                        backend, linkedin_job["id"]
                    )
                self.assertEqual(
                    linkedin_job["result_code"], "reviewed_linkedin_completed"
                )
                self.assertEqual(len(linkedin_calls), 5)
                self.assertIn("--production-check-only", linkedin_calls[0])
                self.assertIn("preview", linkedin_calls[1])
                self.assertIn("approve", linkedin_calls[2])
                self.assertIn("--production-check-only", linkedin_calls[3])
                self.assertIn("execute", linkedin_calls[4])
                self.assertNotIn("--ledger", linkedin_calls[4])
                self.assertIn("--execute", linkedin_calls[4])
                message_path = Path(
                    linkedin_calls[1][
                        linkedin_calls[1].index("--outgoing-message-file") + 1
                    ]
                )
                self.assertEqual(
                    message_path.read_text(encoding="utf-8"),
                    "EDITED PRIVATE LINKEDIN NOTE",
                )
                self.assertEqual(
                    backend.get_review(linkedin_review["id"])["state"],
                    "consumed",
                )
                self.assertNotIn("private CLI output", json.dumps(linkedin_job))

            outside = root / "pytest-pollution-email.json"
            outside.write_text(email_path.read_text(encoding="utf-8"), encoding="utf-8")
            polluted_projection = write_manifest(outside)
            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[polluted_projection],
            ):
                records, reasons = backend._outreach_review_target_records()
                self.assertFalse(
                    any(item["command_id"] == "outreach.email.send" for item in records)
                )
                self.assertTrue(reasons["outreach.email.send"])

            symlink_path = workspace / "20260711-email-symlink.json"
            symlink_path.symlink_to(outside)
            symlink_projection = write_manifest(symlink_path)
            with patch.object(
                backend.adapter,
                "verified_run_projections",
                return_value=[symlink_projection],
            ):
                records, reasons = backend._outreach_review_target_records()
                self.assertFalse(
                    any(item["command_id"] == "outreach.email.send" for item in records)
                )
                self.assertTrue(reasons["outreach.email.send"])

    @staticmethod
    def _wait_for_job(
        backend: OperatorBackend,
        job_id: str,
        *,
        timeout: float = 3.0,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = backend.get_job(job_id)
            if job["status"] in {"completed", "failed", "blocked"}:
                return job
            time.sleep(0.01)
        raise AssertionError("operator background job did not finish")


class ExistingAdapterTestCase(unittest.TestCase):
    def test_pointer_containment_accepts_case_variant_root_by_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "ConfiguredRoot"
            artifact = root / "reports" / "run-report.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("{}", encoding="utf-8")

            case_variant_root = base / "configuredroot"
            if not case_variant_root.is_dir():
                self.skipTest("test requires a case-insensitive filesystem")

            resolved = ExistingEngineAdapter._resolve_pointer(
                root,
                str(case_variant_root / "reports" / "run-report.json"),
                "report",
            )

            self.assertEqual(resolved, artifact.resolve(strict=True))
            self.assertEqual(
                ExistingEngineAdapter._file_evidence(resolved, root)["path"],
                "reports/run-report.json",
            )

    def test_pointer_containment_rejects_traversal_and_symlink_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "configured-root"
            root.mkdir()
            outside = base / "outside.json"
            outside.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "escapes its configured root"):
                ExistingEngineAdapter._resolve_pointer(
                    root,
                    str(root / ".." / outside.name),
                    "report",
                )

            symlink = root / "linked-report.json"
            symlink.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "escapes its configured root"):
                ExistingEngineAdapter._resolve_pointer(root, str(symlink), "report")

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
            action_path = validation / f"{run_id}-action-queue.json"
            artifacts = {
                validation / f"{run_id}-source-metrics.json": {
                    "run_id": run_id,
                    "run_started_at": "2026-07-11T12:00:00Z",
                    "sources": [],
                    "action_queue": {"artifact": str(action_path)},
                },
                action_path: {
                    "counts": {
                        "scored_application_selected": 99,
                        "application_plus_outreach": 0,
                        "application_only": 2,
                        "outreach_only_today": 0,
                        "relationship_buffer": 0,
                        "follow_up": 3,
                        "skipped_internal": 0,
                    },
                    "source_counts": {"application_only": {"import": 2}},
                    "application_plus_outreach": [],
                    "application_only": [{}, {}],
                    "outreach_only_today": [],
                    "relationship_buffer": [],
                    "follow_up": [{}, {}, {}],
                    "skipped_internal": [],
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
                        "run_id": run_id,
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

            valid_action_queue = artifacts[action_path]
            invalid_queues = {
                "lane_shape": {
                    **valid_action_queue,
                    "application_only": [{}, "not-an-object"],
                },
                "reported_count": {
                    **valid_action_queue,
                    "counts": {
                        **valid_action_queue["counts"],
                        "follow_up": 4,
                    },
                },
            }
            expected_errors = {
                "lane_shape": "must be a list of objects",
                "reported_count": "count does not match its entries",
            }
            for case, invalid_queue in invalid_queues.items():
                with self.subTest(action_queue_validation=case):
                    action_path.write_text(
                        json.dumps(invalid_queue), encoding="utf-8"
                    )
                    rejected = ExistingEngineAdapter(settings).status()
                    self.assertEqual(rejected["verified_run_count"], 0)
                    self.assertTrue(
                        any(
                            expected_errors[case] in reason
                            for reason in rejected["rejections"]
                        )
                    )
            action_path.write_text(
                json.dumps(valid_action_queue), encoding="utf-8"
            )

            queue_root = (
                resume / "apps" / "Apply queues" / "current_apply_queue"
            )
            queue_root.mkdir(parents=True)
            material_folder = queue_root / "jobs" / "demo-role"
            material_folder.mkdir(parents=True)
            for filename in (
                "resume_2026-07-11.docx",
                "cl_2026-07-11.docx",
                "jd.txt",
                "strategy.json",
                "intel.txt",
            ):
                (material_folder / filename).write_text("fixture", encoding="utf-8")
            (queue_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "created_at": "2026-07-11T13:00:00Z",
                        "queue_type": "current_apply_queue",
                        "sources": ["generic_import"],
                        "ready_count": 105,
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
                            "id": f"job-{index + 1}",
                            "company": f"Example Company {index + 1}",
                            "role_title": "Product Manager",
                            "url": f"https://example.test/{private_marker}",
                            "fit_score": 8.4,
                            "priority_score": 91.2,
                            "priority_rank": index + 1,
                            "status": "queued",
                            "queue_bucket": "new",
                            "in_latest_run": True,
                            "folder_path": "jobs/demo-role",
                        }
                        for index in range(105)
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
                    "queue": "unavailable",
                    "adapter_mutation": "free",
                },
            )

            for lock_path in (
                runtime / "nightly_scheduler.lock",
                runtime / "nightly_pipeline.lock",
                resume / "discovery" / ".jobs.lock",
                queue_root.parent / ".current_apply_queue.lock",
            ):
                lock_path.write_text("", encoding="utf-8")

            _write_minimal_xlsx(
                resume / "discovery" / "jobs.xlsx",
                {
                    "Jobs": [
                        ["id", "status", "source", "role_type", "fit_score", "company"],
                        ["1", "applied", "linkedin", "PM", "8.5", private_marker],
                        ["2", "queued", "manual", "Strategy", "7.2", private_marker],
                    ],
                    "Archive": [
                        ["id", "status", "source", "role_type"],
                        ["3", "skipped", "indeed", "Other"],
                    ],
                    "ReviewCache": [
                        ["cache_key", "source", "decision", "category", "company"],
                        ["cache-1", "jobspy_filtered_v1", "Reject", "N/A", private_marker],
                    ],
                },
            )
            _write_minimal_xlsx(
                outreach_workspace / "account_tracker.xlsx",
                {
                    "Account Tracker": [
                        [
                            "Company",
                            "Tier",
                            "Account Stage",
                            "Account Score",
                            "Fit Score",
                            "People Mapped",
                            "Invites Sent",
                            "Accepted",
                            "Replies",
                            "Contact Name",
                        ],
                        [
                            "Example Account",
                            "A",
                            "outreach_active",
                            "85",
                            "8",
                            "3",
                            "2",
                            "1",
                            "1",
                            private_marker,
                        ],
                    ],
                    "Action Queue": [
                        ["Company", "Next Action", "Next Due", "Contact Name"],
                        *[
                            [
                                (
                                    "Example Account"
                                    if index == 0
                                    else f"Action Account {index + 1}"
                                ),
                                "Map contacts on LinkedIn",
                                "2026-07-14",
                                private_marker,
                            ]
                            for index in range(55)
                        ],
                    ],
                },
            )
            story_dir = resume / "docs" / "career_workbench" / "story_engine"
            story_dir.mkdir(parents=True)
            (story_dir / "private-story-name.md").write_text(
                private_marker, encoding="utf-8"
            )
            canonical_story_dir = story_dir / "stories"
            canonical_story_dir.mkdir()
            (canonical_story_dir / "product_iteration.md").write_text(
                private_marker, encoding="utf-8"
            )
            comms_dir = outreach_workspace / "comms_learning"
            comms_dir.mkdir()
            (comms_dir / "outcome_learning.json").write_text(
                json.dumps(
                    {
                        "totals": {
                            "sends": 10,
                            "accepts": 2,
                            "replies": 1,
                            "gold": 1,
                            "silver": 9,
                            "negative": 0,
                        },
                        "private": private_marker,
                    }
                ),
                encoding="utf-8",
            )
            (comms_dir / "outcome_recommendation_review_2026-07-11.json").write_text(
                json.dumps(
                    {
                        "automatic_prompt_changes_applied": False,
                        "policy_changes_applied": False,
                        "private": private_marker,
                    }
                ),
                encoding="utf-8",
            )
            snapshot = ExistingEngineAdapter(settings).snapshot()
            self.assertEqual(snapshot["run_snapshot"]["run_id"], run_id)
            self.assertEqual(
                snapshot["run_snapshot"]["report"]["run_status"],
                "failed_or_incomplete",
            )
            self.assertEqual(
                snapshot["run_snapshot"]["report"]["track_2_status"],
                "partial_failed",
            )
            queue = snapshot["run_snapshot"]["queue"]
            self.assertEqual(queue["decision_total"], 5)
            self.assertEqual(
                queue["decision_total_name"],
                "validated_action_queue_lane_entries",
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
                105,
            )
            self.assertEqual(
                snapshot["current_workspace"]["outreach_counts"]["contacts"],
                1,
            )
            self.assertEqual(
                snapshot["current_workspace"]["application_queue"][
                    "material_flags"
                ],
                {
                    "folders_resolved": 105,
                    "resume_ready": 105,
                    "cover_letter_ready": 105,
                    "job_description_ready": 105,
                    "strategy_ready": 105,
                    "intel_ready": 105,
                },
            )
            self.assertNotIn(private_marker, json.dumps(snapshot))

            operator = OperatorBackend(settings)
            assets = operator.assets()
            self.assertEqual(assets["workbooks"]["status"], "available")
            self.assertEqual(
                assets["workbooks"]["resume_workbook"]["jobs"]["row_count"],
                2,
            )
            self.assertEqual(
                assets["workbooks"]["account_tracker"]["account_count"], 1
            )
            self.assertEqual(
                assets["workbooks"]["account_tracker"]["sheet_row_counts"][
                    "Action Queue"
                ],
                55,
            )
            self.assertEqual(
                assets["current_apply_queue"]["summary"]["material_flags"][
                    "resume_ready"
                ],
                105,
            )
            self.assertEqual(
                assets["current_apply_queue"]["items"][0]["company"],
                "Example Company 1",
            )
            self.assertEqual(
                assets["current_apply_queue"]["items_returned"], 100
            )
            self.assertEqual(assets["current_apply_queue"]["items_total"], 105)
            self.assertTrue(assets["current_apply_queue"]["truncated"])
            self.assertTrue(
                assets["current_apply_queue"]["items"][0]["has_resume"]
            )
            account_actions = assets["workbooks"]["account_tracker"][
                "action_items"
            ]
            self.assertEqual(account_actions[0]["company"], "Example Account")
            self.assertEqual(
                account_actions[0]["next_action"], "Map contacts on LinkedIn"
            )
            self.assertEqual(len(account_actions), 50)
            self.assertEqual(
                assets["workbooks"]["account_tracker"]["action_items_total"],
                55,
            )
            self.assertTrue(
                assets["workbooks"]["account_tracker"][
                    "action_items_truncated"
                ]
            )
            self.assertEqual(
                assets["story_comms"]["stories"]["items"][0]["filename"],
                "product_iteration.md",
            )
            self.assertEqual(
                assets["story_comms"]["outcome_totals"]["sends"], 10
            )
            self.assertEqual(assets["daily_reports"]["count"], 1)
            self.assertEqual(
                assets["source_metrics"]["latest"]["run_id"], run_id
            )
            self.assertNotIn(private_marker, json.dumps(assets))
            overview = operator.overview()
            self.assertIn("assets", overview)
            self.assertIn("capabilities", overview)
            self.assertEqual(overview["recent_jobs"], [])

            mismatched_report = json.loads(report_path.read_text(encoding="utf-8"))
            mismatched_report["run_id"] = "20260711-120001"
            report_path.write_text(
                json.dumps(mismatched_report), encoding="utf-8"
            )
            rejected = ExistingEngineAdapter(settings).status()
            self.assertEqual(rejected["verified_run_count"], 0)
            self.assertTrue(
                any(
                    "report run_id does not match" in reason
                    for reason in rejected["rejections"]
                )
            )


if __name__ == "__main__":
    unittest.main()

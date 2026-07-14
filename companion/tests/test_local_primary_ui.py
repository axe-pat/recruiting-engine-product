from __future__ import annotations

import hashlib
import hmac
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from recruiting_companion.api import (
    LOCAL_UI_COOKIE_NAME,
    LOCAL_UI_HEADER,
    LOCAL_UI_SERVER_HEADER,
    _seal_legacy_static_root,
    _validated_static_root,
    make_server,
)
from recruiting_companion.auth import (
    AuthStateError,
    AuthStateHealthyError,
    TokenStore,
)
from recruiting_companion.config import Settings
from recruiting_companion.main import main
from recruiting_companion.service import CompanionService


def write_static_compatibility(
    root: Path,
    *,
    product_version: str = "1.3.0",
    companion_version: str = "0.3.0",
) -> None:
    (root / "release-compatibility.json").write_text(
        json.dumps(
            {
                "schema": "recruiting_engine.static_compatibility",
                "schema_version": 1,
                "product_version": product_version,
                "compatible_companion_version": companion_version,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    integrity_path = root / "static-integrity.json"
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == integrity_path:
            continue
        content = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    integrity_path.write_text(
        json.dumps(
            {
                "schema": "recruiting_engine.static_integrity",
                "schema_version": 1,
                "files": files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class LocalPrimaryUITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.static_root = self.root / "static-export"
        (self.static_root / "app" / "runs").mkdir(parents=True)
        (self.static_root / "assets").mkdir()
        (self.static_root / "index.html").write_text(
            "<!doctype html><title>Local primary</title>",
            encoding="utf-8",
        )
        (self.static_root / "404.html").write_text(
            "<!doctype html><title>Exported not found</title>",
            encoding="utf-8",
        )
        (self.static_root / "app" / "index.html").write_text(
            "<!doctype html><title>App</title>",
            encoding="utf-8",
        )
        (self.static_root / "app" / "runs" / "index.html").write_text(
            "<!doctype html><title>Runs</title>",
            encoding="utf-8",
        )
        (self.static_root / "assets" / "app.js").write_text(
            "globalThis.localPrimaryLoaded = true;",
            encoding="utf-8",
        )
        write_static_compatibility(self.static_root)
        self.settings = Settings(
            data_dir=self.root / "data",
            user_id="local-ui-test",
            port=8765,
        )
        self.settings.prepare()
        self.now = [1_900_000_000.0]
        self.tokens = TokenStore(
            self.settings.user_dir,
            clock=lambda: self.now[0],
        )
        self.bootstrap = self.tokens.bootstrap()
        self.server = make_server(
            self.settings,
            CompanionService(self.settings),
            self.tokens,
            host="127.0.0.1",
            port=0,
            static_root=self.static_root,
        )
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
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
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=raw, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return response.status, response_body, response_headers

    @staticmethod
    def json_body(body: bytes) -> dict[str, object]:
        return json.loads(body.decode("utf-8"))

    def local_headers(self, cookie: str | None = None) -> dict[str, str]:
        headers = {LOCAL_UI_HEADER: "1", "Origin": self.origin}
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def activate(self, ticket: str) -> tuple[str, bytes, dict[str, str]]:
        status, body, headers = self.request(
            "POST",
            "/api/v1/local-ui/activate",
            body={"ticket": ticket},
            headers=self.local_headers(),
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return cookie, body, headers

    def test_raw_html_and_bootstrap_never_mint_an_unauthenticated_cookie(self) -> None:
        for path in ("/", "/app/", "/app/runs", "/local-activate/"):
            status, body, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertNotIn("Set-Cookie", headers)
            self.assertEqual(headers[LOCAL_UI_SERVER_HEADER], "1")
            self.assertNotIn(
                (self.bootstrap.bearer_token or "").encode("utf-8"),
                body,
            )

        status, body, headers = self.request(
            "GET",
            "/api/v1/local-ui/bootstrap",
            headers=self.local_headers(),
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            self.json_body(body)["error"]["code"],
            "local_ui_activation_required",
        )
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(headers[LOCAL_UI_SERVER_HEADER], "1")

        status, page, _ = self.request("GET", "/local-activate/")
        self.assertEqual(status, 200)
        self.assertIn(b"location.hash", page)
        self.assertIn(b"history.replaceState", page)
        self.assertNotIn(b"location.search", page)

    def test_forged_headers_or_wrong_origin_cannot_activate_without_ticket(self) -> None:
        status, _, preflight_headers = self.request(
            "OPTIONS",
            "/api/v1/local-ui/activate",
            headers={
                "Origin": f"http://127.0.0.1:{self.port + 1}",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": LOCAL_UI_HEADER,
            },
        )
        self.assertEqual(status, 204)
        self.assertNotIn(
            LOCAL_UI_HEADER.lower(),
            preflight_headers["Access-Control-Allow-Headers"].lower(),
        )
        self.assertNotIn("Access-Control-Allow-Credentials", preflight_headers)

        forged = "re_activate_" + "x" * 43
        for headers in (
            self.local_headers(),
            {LOCAL_UI_HEADER: "1", "Origin": f"http://127.0.0.1:{self.port + 1}"},
            {LOCAL_UI_HEADER: "1", "Sec-Fetch-Site": "cross-site"},
        ):
            status, body, response_headers = self.request(
                "POST",
                "/api/v1/local-ui/activate",
                body={"ticket": forged},
                headers=headers,
            )
            self.assertIn(status, {401, 403})
            self.assertNotIn("Set-Cookie", response_headers)
            self.assertNotIn(forged, body.decode("utf-8"))

        real_ticket = self.tokens.issue_local_activation_ticket()
        status, _, _ = self.request(
            "POST",
            "/api/v1/local-ui/activate",
            body={"ticket": real_ticket},
            headers={LOCAL_UI_HEADER: "1", "Origin": f"http://127.0.0.1:{self.port + 1}"},
        )
        self.assertEqual(status, 403)
        # A rejected, wrong-origin request must not consume the real ticket.
        self.activate(real_ticket)

    def test_activation_is_hash_only_single_use_and_cookie_is_restart_stable(self) -> None:
        ticket = self.tokens.issue_local_activation_ticket()
        persisted = self.tokens.state_path.read_text(encoding="utf-8")
        self.assertNotIn(ticket, persisted)
        self.assertEqual(
            set(json.loads(persisted)["local_activation_tickets"][0]),
            {"sha256", "expires_at"},
        )

        restarted = TokenStore(
            self.settings.user_dir,
            clock=lambda: self.now[0],
        )
        self.server.tokens = restarted
        old_unsafe_cookie = "re_ui_" + hmac.new(
            (self.bootstrap.bearer_token or "").encode("utf-8"),
            b"recruiting-engine-local-ui-v1",
            hashlib.sha256,
        ).hexdigest()
        self.assertFalse(restarted.verify_local_ui_credential(old_unsafe_cookie))
        cookie, body, headers = self.activate(ticket)
        set_cookie = headers["Set-Cookie"]
        self.assertRegex(
            cookie,
            rf"^{LOCAL_UI_COOKIE_NAME}=re_ui_[a-f0-9]{{64}}$",
        )
        self.assertNotIn(self.bootstrap.bearer_token or "", cookie)
        self.assertNotIn(ticket, body.decode("utf-8"))
        self.assertIn("Path=/", set_cookie)
        self.assertIn("Max-Age=31536000", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertNotIn("Domain=", set_cookie)

        status, replay_body, replay_headers = self.request(
            "POST",
            "/api/v1/local-ui/activate",
            body={"ticket": ticket},
            headers=self.local_headers(),
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            self.json_body(replay_body)["error"]["code"],
            "invalid_local_activation",
        )
        self.assertNotIn("Set-Cookie", replay_headers)

        status, bootstrap_body, bootstrap_headers = self.request(
            "GET",
            "/api/v1/local-ui/bootstrap",
            headers=self.local_headers(cookie),
        )
        self.assertEqual(status, 200)
        bootstrap_payload = self.json_body(bootstrap_body)
        self.assertTrue(bootstrap_payload["cookie_authenticated"])
        self.assertEqual(bootstrap_payload["product_version"], "1.3.0")
        self.assertEqual(bootstrap_payload["companion_version"], "0.3.0")
        self.assertEqual(
            bootstrap_payload["compatible_companion_version"],
            "0.3.0",
        )
        self.assertIn("Set-Cookie", bootstrap_headers)
        self.assertEqual(bootstrap_headers[LOCAL_UI_SERVER_HEADER], "1")

    def test_ticket_expires_in_at_most_two_minutes(self) -> None:
        self.assertLessEqual(TokenStore.LOCAL_ACTIVATION_SECONDS, 120)
        ticket = self.tokens.issue_local_activation_ticket()
        self.now[0] += TokenStore.LOCAL_ACTIVATION_SECONDS + 1
        status, _, headers = self.request(
            "POST",
            "/api/v1/local-ui/activate",
            body={"ticket": ticket},
            headers=self.local_headers(),
        )
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)
        state = json.loads(self.tokens.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["local_activation_tickets"], [])

    def test_cookie_auth_requires_guard_and_exact_bound_port(self) -> None:
        cookie, _, _ = self.activate(self.tokens.issue_local_activation_ticket())
        status, body, _ = self.request(
            "GET",
            "/api/v1/dashboard",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 403)
        self.assertEqual(
            self.json_body(body)["error"]["code"],
            "local_ui_guard_required",
        )
        status, _, _ = self.request(
            "GET",
            "/api/v1/dashboard",
            headers=self.local_headers(cookie),
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "GET",
            "/api/v1/dashboard",
            headers={
                "Cookie": cookie,
                LOCAL_UI_HEADER: "1",
                "Origin": "http://127.0.0.1:8765",
            },
        )
        self.assertEqual(status, 403)

    def test_cookie_cannot_rotate_or_override_an_invalid_bearer(self) -> None:
        cookie, _, _ = self.activate(self.tokens.issue_local_activation_ticket())
        local_headers = self.local_headers(cookie)
        status, _, _ = self.request(
            "GET",
            "/api/v1/dashboard",
            headers={
                **local_headers,
                "Authorization": f"Bearer {cookie.split('=', 1)[1]}",
            },
        )
        self.assertEqual(status, 401)
        status, body, _ = self.request(
            "POST",
            "/api/v1/auth/rotate",
            body={},
            headers=local_headers,
        )
        self.assertEqual(status, 403)
        self.assertNotIn("bearer_token", body.decode("utf-8"))

    def test_cookie_auth_can_stage_and_queue_reviewed_operator_actions(self) -> None:
        # Regression: cookie scope is "local_ui". Review staging and job submit
        # must accept that scope; otherwise Run E2E dies at "Stage exact content".
        cookie, _, _ = self.activate(self.tokens.issue_local_activation_ticket())
        local_headers = self.local_headers(cookie)
        target_id = "target_" + ("a" * 24)

        status, body, _ = self.request(
            "POST",
            "/api/v1/operator/reviews",
            body={
                "command_id": "nightly.run",
                "target_id": target_id,
                "reviewed_text": "",
                "reviewed_subject": "",
            },
            headers=local_headers,
        )
        payload = self.json_body(body)
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        self.assertNotEqual(
            error.get("message"),
            "requested_scope must be local or web",
            msg=payload,
        )
        self.assertNotIn(
            "requested_scope must be local, local_ui, or web",
            str(error.get("message") or ""),
            msg=payload,
        )
        # Fake target cannot resolve without an installed engine; scope must still pass.
        self.assertEqual(status, 404, msg=payload)
        self.assertEqual(error.get("code"), "not_found")

        status, body, _ = self.request(
            "POST",
            "/api/v1/operator/jobs",
            body={
                "command_id": "production.preflight",
                "confirmation": "RUN_PRODUCTION_PREFLIGHT",
            },
            headers=local_headers,
        )
        payload = self.json_body(body)
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        self.assertNotEqual(
            error.get("message"),
            "requested_scope must be local or web",
            msg=payload,
        )
        self.assertNotIn(
            "requested_scope must be local, local_ui, or web",
            str(error.get("message") or ""),
            msg=payload,
        )
        # Without resume/attestation roots this remains blocked or unavailable — not a scope 422.
        self.assertIn(status, {201, 409, 422}, msg=payload)
        if status == 422:
            self.assertNotIn("requested_scope", str(error.get("message") or ""))

    def test_tickets_and_bearers_are_absent_from_access_logs_and_json(self) -> None:
        ticket = self.tokens.issue_local_activation_ticket()
        capture = io.StringIO()
        with redirect_stderr(capture):
            cookie, body, _ = self.activate(ticket)
            self.request("GET", f"/api/v1/health?ticket={ticket}")
        log = capture.getvalue()
        self.assertNotIn(ticket, log)
        self.assertNotIn(self.bootstrap.bearer_token or "", log)
        self.assertNotIn(ticket, body.decode("utf-8"))
        self.assertNotIn(self.bootstrap.bearer_token or "", body.decode("utf-8"))
        self.assertTrue(cookie.startswith(f"{LOCAL_UI_COOKIE_NAME}=re_ui_"))

    def test_export_assets_head_fallback_and_traversal_are_guarded(self) -> None:
        status, body, headers = self.request("GET", "/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"localPrimaryLoaded", body)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertNotIn("Set-Cookie", headers)

        status, body, headers = self.request("HEAD", "/app/runs/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertNotIn("Set-Cookie", headers)

        status, body, headers = self.request("GET", "/missing-route")
        self.assertEqual(status, 404)
        self.assertIn(b"Exported not found", body)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertNotIn("Set-Cookie", headers)
        status, body, _ = self.request("GET", "/assets/missing.js")
        self.assertEqual(status, 404)
        self.assertNotIn(b"Exported not found", body)

        (self.static_root / "assets" / "unexpected.xml").write_text(
            "<unsafe />",
            encoding="utf-8",
        )
        status, body, _ = self.request("GET", "/assets/unexpected.xml")
        self.assertEqual(status, 415)
        self.assertNotIn(b"<unsafe", body)

        for path in ("/%2e%2e/private.txt", "/assets%5c..%5cprivate.txt"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 400)

        secret = self.root / "secret.txt"
        secret.write_text("must not be served", encoding="utf-8")
        (self.static_root / "assets" / "leak.txt").symlink_to(secret)
        status, body, _ = self.request("GET", "/assets/leak.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(b"must not be served", body)

    def test_post_start_static_mutation_is_never_served(self) -> None:
        asset = self.static_root / "assets" / "app.js"
        asset.write_text("globalThis.unvalidatedReplacement = true;", encoding="utf-8")

        status, body, _ = self.request("GET", "/assets/app.js")

        self.assertEqual(status, 503)
        self.assertNotIn(b"unvalidatedReplacement", body)
        self.assertEqual(
            self.json_body(body)["error"]["code"],
            "static_export_changed",
        )


class AuthRecoveryAndConcurrencyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(
            data_dir=self.root / "data",
            user_id="auth-recovery",
        )
        self.settings.prepare()
        self.now = [1_900_000_000.0]
        self.store = TokenStore(
            self.settings.user_dir,
            clock=lambda: self.now[0],
        )
        self.bootstrap = self.store.bootstrap()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_bearer_requires_explicit_repair_and_clears_all_sessions(self) -> None:
        old_cookie = self.store.local_ui_credential() or ""
        old_bearer = self.bootstrap.bearer_token or ""
        ticket = self.store.issue_local_activation_ticket()
        web = self.store.exchange_pairing_token(
            self.bootstrap.pairing_token or "",
            client_type="web",
        )
        self.assertIsNotNone(web)
        self.store.bearer_path.unlink()
        with self.assertRaises(AuthStateError):
            self.store.issue_local_activation_ticket()

        result = self.store.repair_auth()
        self.assertEqual(result.state_path, self.store.state_path)
        state_text = self.store.state_path.read_text(encoding="utf-8")
        state = json.loads(state_text)
        self.assertEqual(state["web_sessions"], [])
        self.assertEqual(state["local_activation_tickets"], [])
        self.assertNotIn(ticket, state_text)
        self.assertIsNone(self.store.consume_local_activation_ticket(ticket))
        self.assertFalse(self.store.verify_local_ui_credential(old_cookie))
        self.assertFalse(self.store.verify_bearer(old_bearer))
        self.assertNotEqual(
            self.store.bearer_path.read_text(encoding="utf-8").strip(),
            old_bearer,
        )
        with self.assertRaises(AuthStateHealthyError):
            self.store.repair_auth()

    def test_corrupt_state_and_bearer_are_recoverable_without_secret_output(self) -> None:
        self.store.state_path.write_text("{not-json", encoding="utf-8")
        self.store.state_path.chmod(0o600)
        result = self.store.repair_auth()
        self.assertEqual(result.bearer_path, self.store.bearer_path)
        ticket = self.store.issue_local_activation_ticket()
        self.assertTrue(ticket.startswith("re_activate_"))

        self.store.bearer_path.write_text("corrupt\n", encoding="utf-8")
        self.store.bearer_path.chmod(0o600)
        with self.assertRaises(AuthStateError):
            self.store.issue_local_activation_ticket()

        env = {
            "RECRUITING_ENGINE_DATA_DIR": str(self.settings.data_dir),
            "RECRUITING_ENGINE_USER_ID": self.settings.user_id,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result_code = main(["repair-auth"])
        self.assertEqual(result_code, 0)
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertIn(str(self.store.state_path), combined)
        for prefix in ("re_local_", "re_pair_", "re_web_", "re_activate_"):
            self.assertNotIn(prefix, combined)

        before = self.store.state_path.read_bytes()
        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result_code = main(["repair-auth"])
        self.assertEqual(result_code, 1)
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_filesystem_lock_prevents_lost_tickets_across_store_instances(self) -> None:
        stores = [
            TokenStore(
                self.settings.user_dir,
                clock=lambda: self.now[0],
            )
            for _ in range(6)
        ]
        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            tickets = list(
                executor.map(
                    lambda store: store.issue_local_activation_ticket(),
                    stores,
                )
            )
        self.assertEqual(len(set(tickets)), len(tickets))
        state_text = self.store.state_path.read_text(encoding="utf-8")
        state = json.loads(state_text)
        self.assertEqual(len(state["local_activation_tickets"]), len(tickets))
        for ticket in tickets:
            self.assertNotIn(ticket, state_text)
            self.assertIsNotNone(self.store.consume_local_activation_ticket(ticket))
            self.assertIsNone(self.store.consume_local_activation_ticket(ticket))


class StaticExportValidationTestCase(unittest.TestCase):
    @staticmethod
    def build_export(root: Path) -> Path:
        static_root = root / "static-export"
        (static_root / "app").mkdir(parents=True)
        (static_root / "assets").mkdir()
        (static_root / "index.html").write_text("index", encoding="utf-8")
        (static_root / "app" / "index.html").write_text(
            "app",
            encoding="utf-8",
        )
        write_static_compatibility(static_root)
        return static_root

    def test_server_rejects_symlinked_export_entries_before_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            static_root = self.build_export(root)
            outside = root / "outside.js"
            outside.write_text("outside", encoding="utf-8")
            (static_root / "assets" / "linked.js").symlink_to(outside)
            settings = Settings(data_dir=root / "data", user_id="validation")
            settings.prepare()
            tokens = TokenStore(settings.user_dir)
            tokens.bootstrap()
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                make_server(
                    settings,
                    CompanionService(settings),
                    tokens,
                    host="127.0.0.1",
                    port=0,
                    static_root=static_root,
                )

    def test_server_rejects_missing_or_incompatible_release_marker(self) -> None:
        for mode in ("missing", "wrong_companion", "wrong_product"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                static_root = self.build_export(root)
                marker = static_root / "release-compatibility.json"
                if mode == "missing":
                    marker.unlink()
                    expected = "missing file"
                elif mode == "wrong_companion":
                    write_static_compatibility(
                        static_root,
                        companion_version="0.2.0",
                    )
                    expected = "different companion version"
                else:
                    write_static_compatibility(
                        static_root,
                        product_version="1.2.1",
                    )
                    expected = "product version"
                settings = Settings(data_dir=root / "data", user_id="compatibility")
                settings.prepare()
                tokens = TokenStore(settings.user_dir)
                tokens.bootstrap()
                with self.assertRaisesRegex(ValueError, expected):
                    make_server(
                        settings,
                        CompanionService(settings),
                        tokens,
                        host="127.0.0.1",
                        port=0,
                        static_root=static_root,
                    )

    def test_server_rejects_symlinked_release_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            static_root = self.build_export(root)
            marker = static_root / "release-compatibility.json"
            outside = root / "marker.json"
            outside.write_text(marker.read_text(encoding="utf-8"), encoding="utf-8")
            marker.unlink()
            marker.symlink_to(outside)
            settings = Settings(data_dir=root / "data", user_id="marker-symlink")
            settings.prepare()
            tokens = TokenStore(settings.user_dir)
            tokens.bootstrap()
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                make_server(
                    settings,
                    CompanionService(settings),
                    tokens,
                    host="127.0.0.1",
                    port=0,
                    static_root=static_root,
                )

    def test_integrity_inventory_rejects_tampering_and_unlisted_files(self) -> None:
        for mode in ("tampered", "unlisted"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                static_root = self.build_export(root)
                if mode == "tampered":
                    (static_root / "app/index.html").write_text(
                        "bad",
                        encoding="utf-8",
                    )
                    expected = "digest does not match"
                else:
                    (static_root / "assets/new.js").write_text(
                        "unlisted",
                        encoding="utf-8",
                    )
                    expected = "inventory does not match"
                with self.assertRaisesRegex(ValueError, expected):
                    _validated_static_root(static_root)

    def test_stopped_service_legacy_export_can_be_sealed_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            static_root = self.build_export(root)
            integrity = static_root / "static-integrity.json"
            integrity.unlink()
            original = (static_root / "app/index.html").read_bytes()

            _seal_legacy_static_root(static_root)

            self.assertTrue(integrity.is_file())
            self.assertEqual((static_root / "app/index.html").read_bytes(), original)
            _, compatibility, inventory = _validated_static_root(static_root)
            self.assertEqual(compatibility["product_version"], "1.3.0")
            self.assertIn("app/index.html", inventory)


if __name__ == "__main__":
    unittest.main()

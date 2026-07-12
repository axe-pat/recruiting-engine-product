from __future__ import annotations

import json
import re
import traceback
import csv
import io
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .auth import TokenStore
from .config import Settings
from .existing_adapter import ExistingEngineAdapter
from .operator_backend import OperatorBackend
from .service import CompanionService, ServiceError, ValidationError


API_PREFIX = "/api/v1"
RESOURCE_SINGULAR = {
    "jobs": "job",
    "companies": "company",
    "contacts": "contact",
    "applications": "application",
}


class CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        settings: Settings,
        service: CompanionService,
        tokens: TokenStore,
    ):
        super().__init__(address, handler)
        self.settings = settings
        self.service = service
        self.tokens = tokens


class CompanionHandler(BaseHTTPRequestHandler):
    server: CompanionHTTPServer
    server_version = "RecruitingEngineCompanion/0.2"
    sys_version = ""

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        if not self.server.settings.is_host_allowed(
            self.headers.get("Host"), self.server.server_port
        ):
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_host", "Host is not allowed")
            return
        if not self.server.settings.is_origin_allowed(origin):
            self._send_error(HTTPStatus.FORBIDDEN, "origin_not_allowed", "Origin is not allowed")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers(origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Pairing-Token",
        )
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        origin = self.headers.get("Origin")
        try:
            if not self.server.settings.is_host_allowed(
                self.headers.get("Host"), self.server.server_port
            ):
                self._send_error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_host",
                    "Host is not allowed",
                )
                return
            if not self.server.settings.is_origin_allowed(origin):
                self._send_error(
                    HTTPStatus.FORBIDDEN,
                    "origin_not_allowed",
                    "Origin is not allowed",
                )
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if not (path == API_PREFIX or path.startswith(API_PREFIX + "/")):
                self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Route not found")
                return
            route = path[len(API_PREFIX) :] or "/"

            if method == "GET" and route == "/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": __version__,
                        "mode": "local",
                        "auth_required": True,
                    },
                    origin,
                )
                return
            if method == "POST" and route == "/pair":
                body = self._json_body(optional=True)
                pairing = body.get("pairing_token") or self.headers.get(
                    "X-Pairing-Token", ""
                )
                client_type = body.get("client_type", "extension")
                if client_type not in {"extension", "local", "web"}:
                    raise ValidationError(
                        "client_type must be extension, local, or web"
                    )
                exchange = self.server.tokens.exchange_pairing_token(
                    str(pairing),
                    client_type=str(client_type),
                )
                if not exchange:
                    self._send_error(
                        HTTPStatus.UNAUTHORIZED,
                        "invalid_pairing_token",
                        "Pairing token is invalid, expired, or already used",
                        origin,
                    )
                    return
                response: dict[str, Any] = {
                    "bearer_token": exchange.bearer_token,
                    "token_type": "Bearer",
                }
                if exchange.client_type == "web":
                    response.update(
                        {
                            "client_type": "web",
                            "expires_in": exchange.expires_in,
                        }
                    )
                self._send_json(
                    HTTPStatus.OK,
                    response,
                    origin,
                )
                return

            if not self._authenticate(origin):
                return
            if not self._authorize_scope(method, route, origin):
                return
            query = parse_qs(parsed.query)
            self._dispatch_authenticated(method, route, query, origin)
        except ServiceError as error:
            self._send_error(error.status, error.code, error.message, origin)
        except json.JSONDecodeError:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                "Request body is not valid JSON",
                origin,
            )
        except ValueError as error:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "bad_request",
                str(error),
                origin,
            )
        except Exception:
            traceback.print_exc()
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The local companion encountered an unexpected error",
                origin,
            )

    def _dispatch_authenticated(
        self,
        method: str,
        route: str,
        query: dict[str, list[str]],
        origin: str | None,
    ) -> None:
        service = self.server.service
        if route == "/profile":
            if method == "GET":
                self._send_json(HTTPStatus.OK, {"profile": service.get_profile()}, origin)
                return
            if method == "PUT":
                body = self._json_body()
                profile = body.get("profile", body)
                self._send_json(
                    HTTPStatus.OK,
                    {"profile": service.put_profile(profile)},
                    origin,
                )
                return
        if method == "POST" and route == "/auth/rotate":
            if getattr(self, "_auth_scope", None) == "web":
                self._send_error(
                    HTTPStatus.FORBIDDEN,
                    "insufficient_scope",
                    "Web sessions cannot rotate the long-lived local bearer",
                    origin,
                )
                return
            bearer = self.server.tokens.rotate_bearer_token()
            self._send_json(
                HTTPStatus.OK,
                {
                    "bearer_token": bearer,
                    "token_type": "Bearer",
                    "invalidated_previous": True,
                },
                origin,
            )
            return
        if route == "/preferences":
            if method == "GET":
                self._send_json(
                    HTTPStatus.OK,
                    {"preferences": service.get_preferences()},
                    origin,
                )
                return
            if method == "PUT":
                body = self._json_body()
                preferences = body.get("preferences", body)
                self._send_json(
                    HTTPStatus.OK,
                    {"preferences": service.put_preferences(preferences)},
                    origin,
                )
                return
        if method == "GET" and route == "/dashboard":
            self._send_json(
                HTTPStatus.OK,
                {"snapshot": service.dashboard_snapshot()},
                origin,
            )
            return
        if method == "GET" and route == "/existing-engine/status":
            self._send_json(
                HTTPStatus.OK,
                {"existing_engine": ExistingEngineAdapter(self.server.settings).status()},
                origin,
            )
            return
        if method == "GET" and route == "/existing-engine/snapshot":
            self._send_json(
                HTTPStatus.OK,
                {"existing_engine": ExistingEngineAdapter(self.server.settings).snapshot()},
                origin,
            )
            return
        if route.startswith("/operator/"):
            operator = OperatorBackend(self.server.settings)
            if method == "GET" and route == "/operator/overview":
                self._send_json(
                    HTTPStatus.OK,
                    {"operator": operator.overview()},
                    origin,
                )
                return
            if method == "GET" and route == "/operator/capabilities":
                self._send_json(
                    HTTPStatus.OK,
                    {"capabilities": operator.capabilities()},
                    origin,
                )
                return
            if method == "GET" and route == "/operator/assets":
                self._send_json(
                    HTTPStatus.OK,
                    {"assets": operator.assets()},
                    origin,
                )
                return
            report_html_match = re.fullmatch(
                r"/operator/reports/(\d{8}-\d{6})/html", route
            )
            if method == "GET" and report_html_match:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "report": operator.exact_report_html(
                            report_html_match.group(1)
                        )
                    },
                    origin,
                )
                return
            if method == "GET" and route == "/operator/review-targets":
                self._send_json(
                    HTTPStatus.OK,
                    {"review_targets": operator.review_targets()},
                    origin,
                )
                return
            review_target_match = re.fullmatch(
                r"/operator/review-targets/([^/]+)/detail", route
            )
            if method == "GET" and review_target_match:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "review_target": operator.get_review_target_detail(
                            review_target_match.group(1)
                        )
                    },
                    origin,
                )
                return
            if route == "/operator/reviews":
                if method == "GET":
                    items = operator.list_reviews(**self._pagination(query))
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": items, "count": len(items)},
                        origin,
                    )
                    return
                if method == "POST":
                    body = self._json_body()
                    unknown = set(body) - {
                        "command_id",
                        "target_id",
                        "reviewed_subject",
                        "reviewed_text",
                    }
                    if unknown:
                        raise ValidationError(
                            "unsupported operator review fields: "
                            + ", ".join(sorted(unknown))
                        )
                    review = operator.create_review(
                        command_id=str(body.get("command_id") or ""),
                        target_id=str(body.get("target_id") or ""),
                        requested_scope=getattr(self, "_auth_scope", "local"),
                        reviewed_subject=body.get("reviewed_subject"),
                        reviewed_text=body.get("reviewed_text"),
                    )
                    self._send_json(
                        HTTPStatus.CREATED,
                        {"operator_review": review},
                        origin,
                    )
                    return
            review_detail_match = re.fullmatch(
                r"/operator/reviews/([^/]+)/detail", route
            )
            if method == "GET" and review_detail_match:
                review, target = operator.get_review_detail(
                    review_detail_match.group(1)
                )
                self._send_json(
                    HTTPStatus.OK,
                    {"operator_review": review, "review_target": target},
                    origin,
                )
                return
            review_content_match = re.fullmatch(
                r"/operator/reviews/([^/]+)/content", route
            )
            if method == "PUT" and review_content_match:
                body = self._json_body()
                unknown = set(body) - {
                    "reviewed_subject",
                    "reviewed_text",
                    "confirmation",
                }
                if unknown:
                    raise ValidationError(
                        "unsupported operator review content fields: "
                        + ", ".join(sorted(unknown))
                    )
                review, target = operator.update_review_content(
                    review_content_match.group(1),
                    reviewed_subject=body.get("reviewed_subject"),
                    reviewed_text=body.get("reviewed_text"),
                    confirmation=str(body.get("confirmation") or ""),
                    requested_scope=getattr(self, "_auth_scope", "local"),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {"operator_review": review, "review_target": target},
                    origin,
                )
                return
            review_match = re.fullmatch(r"/operator/reviews/([^/]+)", route)
            if method == "GET" and review_match:
                self._send_json(
                    HTTPStatus.OK,
                    {"operator_review": operator.get_review(review_match.group(1))},
                    origin,
                )
                return
            review_transition_match = re.fullmatch(
                r"/operator/reviews/([^/]+)/(review|approve|revoke)", route
            )
            if method == "POST" and review_transition_match:
                body = self._json_body()
                unknown = set(body) - {"confirmation"}
                if unknown:
                    raise ValidationError(
                        "unsupported operator review transition fields: "
                        + ", ".join(sorted(unknown))
                    )
                review = operator.transition_review(
                    review_transition_match.group(1),
                    transition=review_transition_match.group(2),
                    confirmation=str(body.get("confirmation") or ""),
                    requested_scope=getattr(self, "_auth_scope", "local"),
                )
                self._send_json(
                    HTTPStatus.OK,
                    {"operator_review": review},
                    origin,
                )
                return
            if route == "/operator/jobs":
                if method == "GET":
                    items = operator.list_jobs(**self._pagination(query))
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": items, "count": len(items)},
                        origin,
                    )
                    return
                if method == "POST":
                    body = self._json_body()
                    unknown = set(body) - {
                        "command_id",
                        "confirmation",
                        "parameters",
                    }
                    if unknown:
                        raise ValidationError(
                            "unsupported operator job fields: "
                            + ", ".join(sorted(unknown))
                        )
                    job = operator.submit_job(
                        command_id=str(body.get("command_id") or ""),
                        confirmation=str(body.get("confirmation") or ""),
                        parameters=body.get("parameters", {}),
                        requested_scope=getattr(self, "_auth_scope", "local"),
                    )
                    self._send_json(
                        HTTPStatus.CREATED,
                        {"operator_job": job},
                        origin,
                    )
                    return
            operator_job_match = re.fullmatch(r"/operator/jobs/([^/]+)", route)
            if method == "GET" and operator_job_match:
                self._send_json(
                    HTTPStatus.OK,
                    {"operator_job": operator.get_job(operator_job_match.group(1))},
                    origin,
                )
                return
        if route == "/onboarding" and method == "POST":
            payload, uploads = self._structured_body()
            self._send_json(
                HTTPStatus.CREATED,
                {"onboarding": service.onboard(payload, uploads)},
                origin,
            )
            return
        if route == "/documents":
            if method == "GET":
                items = service.list_documents(**self._pagination(query))
                self._send_json(
                    HTTPStatus.OK,
                    {"items": items, "count": len(items)},
                    origin,
                )
                return
            if method == "POST":
                content_type = self.headers.get("Content-Type", "")
                if content_type.startswith("multipart/form-data"):
                    fields, uploads = self._multipart_body()
                    if len(uploads) != 1:
                        raise ValidationError("exactly one document file is required")
                    upload = uploads[0]
                    document = service.add_document(
                        filename=upload["filename"],
                        content=upload["content"],
                        kind=fields.get("kind", upload.get("kind", "other")),
                        media_type=upload["media_type"],
                    )
                else:
                    document = service.add_document_base64(self._json_body())
                self._send_json(
                    HTTPStatus.CREATED,
                    {"document": document},
                    origin,
                )
                return
        if route == "/runs":
            if method == "GET":
                items = service.list_runs(**self._pagination(query))
                self._send_json(
                    HTTPStatus.OK,
                    {"items": items, "count": len(items)},
                    origin,
                )
                return
            if method == "POST":
                body = self._json_body(optional=True)
                run_type = body.pop("type", "portable")
                config = body.pop("config", body)
                if run_type != "portable":
                    raise ValidationError("only type=portable is supported")
                result = service.run_portable(config)
                self._send_json(HTTPStatus.CREATED, result, origin)
                return
        match = re.fullmatch(r"/runs/([^/]+)", route)
        if method == "GET" and match:
            self._send_json(HTTPStatus.OK, service.get_run(match.group(1)), origin)
            return
        match = re.fullmatch(r"/reports/([^/]+)", route)
        if method == "GET" and match:
            self._send_json(
                HTTPStatus.OK,
                {"report": service.get_report(match.group(1))},
                origin,
            )
            return
        if method == "POST" and route == "/intakes":
            self._send_json(
                HTTPStatus.CREATED,
                service.create_intake(self._json_body()),
                origin,
            )
            return
        if method == "POST" and route == "/imports/jobs":
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                fields, uploads = self._multipart_body()
                if len(uploads) != 1:
                    raise ValidationError("exactly one CSV file is required")
                try:
                    text = uploads[0]["content"].decode("utf-8-sig")
                except UnicodeDecodeError as error:
                    raise ValidationError("CSV must use UTF-8 encoding") from error
                reader = csv.DictReader(io.StringIO(text))
                if not reader.fieldnames:
                    raise ValidationError("CSV must contain a header row")
                rows = list(reader)
                source_label = fields.get("source_label", "")
            else:
                body = self._json_body()
                rows = body.get("rows", [])
                source_label = body.get("source_label", "")
            self._send_json(
                HTTPStatus.CREATED,
                {"import": service.import_jobs(rows, source_label=source_label)},
                origin,
            )
            return
        if route == "/outreach":
            if method == "GET":
                items = service.list_outreach(**self._pagination(query))
                self._send_json(
                    HTTPStatus.OK,
                    {"items": items, "count": len(items)},
                    origin,
                )
                return
            if method == "POST":
                self._send_json(
                    HTTPStatus.CREATED,
                    {"outreach": service.create_outreach(self._json_body())},
                    origin,
                )
                return
        match = re.fullmatch(r"/outreach/([^/]+)/approve", route)
        if method == "POST" and match:
            body = self._json_body(optional=True)
            outreach, event = service.transition_outreach(
                match.group(1),
                to_state="approved",
                actor=body.get("actor", "local-user"),
                note=body.get("note", "Explicit approval"),
            )
            self._send_json(
                HTTPStatus.OK,
                {"outreach": outreach, "event": event},
                origin,
            )
            return
        match = re.fullmatch(r"/outreach/([^/]+)", route)
        if match:
            outreach_id = match.group(1)
            if method == "GET":
                self._send_json(
                    HTTPStatus.OK,
                    {"outreach": service.get_outreach(outreach_id)},
                    origin,
                )
                return
            if method == "PATCH":
                body = self._json_body()
                state = body.get("state", body.get("status", ""))
                if (
                    getattr(self, "_auth_scope", None) == "web"
                    and (
                        not isinstance(state, str)
                        or state not in {"draft", "reviewed"}
                    )
                ):
                    self._send_error(
                        HTTPStatus.FORBIDDEN,
                        "insufficient_scope",
                        "Web sessions may only return outreach to draft or mark it reviewed",
                        origin,
                    )
                    return
                outreach, event = service.transition_outreach(
                    outreach_id,
                    to_state=state,
                    actor=body.get("actor", "local-user"),
                    note=body.get("note", ""),
                    reviewed_text=body.get("reviewed_text"),
                    delivery_reference=body.get("delivery_reference", ""),
                    confirmed=body.get("confirmed") is True,
                )
                self._send_json(
                    HTTPStatus.OK,
                    {"outreach": outreach, "event": event},
                    origin,
                )
                return

        resource_match = re.fullmatch(
            r"/(jobs|companies|contacts|applications)(?:/([^/]+))?",
            route,
        )
        if resource_match:
            resource, resource_id = resource_match.groups()
            singular = RESOURCE_SINGULAR[resource]
            if method == "GET" and resource_id:
                self._send_json(
                    HTTPStatus.OK,
                    {singular: service.get_resource(resource, resource_id)},
                    origin,
                )
                return
            if method == "GET" and not resource_id:
                items = service.list_resource(resource, **self._pagination(query))
                self._send_json(
                    HTTPStatus.OK,
                    {"items": items, "count": len(items)},
                    origin,
                )
                return
            if method == "POST" and not resource_id:
                self._send_json(
                    HTTPStatus.CREATED,
                    {singular: service.create_resource(resource, self._json_body())},
                    origin,
                )
                return
            if method == "PATCH" and resource_id:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        singular: service.update_resource(
                            resource,
                            resource_id,
                            self._json_body(),
                        )
                    },
                    origin,
                )
                return

        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Route not found", origin)

    def _authorize_scope(
        self,
        method: str,
        route: str,
        origin: str | None,
    ) -> bool:
        if getattr(self, "_auth_scope", None) != "web":
            return True
        exact_routes = {
            ("GET", "/dashboard"),
            ("GET", "/preferences"),
            ("GET", "/existing-engine/status"),
            ("GET", "/existing-engine/snapshot"),
            ("PUT", "/profile"),
            ("PUT", "/preferences"),
            ("POST", "/documents"),
            ("POST", "/imports/jobs"),
            ("POST", "/runs"),
            ("GET", "/operator/overview"),
            ("GET", "/operator/capabilities"),
            ("GET", "/operator/assets"),
            ("GET", "/operator/review-targets"),
            ("GET", "/operator/reviews"),
            ("POST", "/operator/reviews"),
            ("GET", "/operator/jobs"),
            ("POST", "/operator/jobs"),
        }
        allowed = (method, route) in exact_routes
        if method == "PATCH" and re.fullmatch(r"/outreach/[^/]+", route):
            allowed = True
        if method == "POST" and re.fullmatch(r"/outreach/[^/]+/approve", route):
            allowed = True
        if method == "GET" and re.fullmatch(r"/operator/jobs/[^/]+", route):
            allowed = True
        if method == "GET" and re.fullmatch(
            r"/operator/reports/\d{8}-\d{6}/html", route
        ):
            allowed = True
        if method == "GET" and re.fullmatch(
            r"/operator/review-targets/[^/]+/detail", route
        ):
            allowed = True
        if method == "GET" and re.fullmatch(r"/operator/reviews/[^/]+", route):
            allowed = True
        if method == "GET" and re.fullmatch(
            r"/operator/reviews/[^/]+/detail", route
        ):
            allowed = True
        if method == "PUT" and re.fullmatch(
            r"/operator/reviews/[^/]+/content", route
        ):
            allowed = True
        if method == "POST" and re.fullmatch(
            r"/operator/reviews/[^/]+/(review|approve|revoke)", route
        ):
            allowed = True
        if allowed:
            return True
        self._send_error(
            HTTPStatus.FORBIDDEN,
            "insufficient_scope",
            "Web session is not authorized for this route",
            origin,
        )
        return False

    def _authenticate(self, origin: str | None) -> bool:
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        scope = (
            self.server.tokens.authenticate_token(token.strip())
            if scheme.lower() == "bearer"
            else None
        )
        if scope is None:
            self._send_error(
                HTTPStatus.UNAUTHORIZED,
                "unauthorized",
                "A valid bearer token is required",
                origin,
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        self._auth_scope = scope
        return True

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValidationError("Content-Length must be an integer") from error
        # JSON base64 expands bytes by roughly one third; keep the fallback
        # bounded while allowing the same configured decoded-file ceiling.
        limit = self.server.settings.max_upload_bytes * 2 + 1_000_000
        if length < 0 or length > limit:
            raise ValidationError(f"request body exceeds the {limit}-byte limit")
        return self.rfile.read(length)

    def _json_body(self, *, optional: bool = False) -> dict[str, Any]:
        body = self._read_body()
        if not body and optional:
            return {}
        content_type = self.headers.get("Content-Type", "")
        if body and not content_type.lower().startswith("application/json"):
            raise ValidationError("Content-Type must be application/json")
        value = json.loads(body.decode("utf-8") if body else "{}")
        if not isinstance(value, dict):
            raise ValidationError("JSON body must be an object")
        return value

    def _structured_body(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
            fields, uploads = self._multipart_body()
            payload: dict[str, Any] = {}
            for key, value in fields.items():
                if key in {
                    "profile",
                    "preferences",
                    "companies",
                    "jobs",
                    "contacts",
                    "documents",
                }:
                    payload[key] = json.loads(value)
            document_kind = fields.get("kind", "other")
            for upload in uploads:
                upload["kind"] = document_kind
            return payload, uploads
        return self._json_body(), []

    def _multipart_body(self) -> tuple[dict[str, str], list[dict[str, Any]]]:
        content_type = self.headers.get("Content-Type", "")
        body = self._read_body()
        message = BytesParser(policy=policy.default).parsebytes(
            (
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
                    "ascii"
                )
                + body
            )
        )
        if not message.is_multipart():
            raise ValidationError("multipart body is malformed")
        fields: dict[str, str] = {}
        uploads: list[dict[str, Any]] = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            content = part.get_payload(decode=True) or b""
            if filename:
                uploads.append(
                    {
                        "field": name,
                        "filename": filename,
                        "media_type": part.get_content_type(),
                        "content": content,
                        "kind": name if name != "document" else "other",
                    }
                )
            else:
                fields[name] = content.decode(part.get_content_charset() or "utf-8")
        return fields, uploads

    @staticmethod
    def _pagination(query: dict[str, list[str]]) -> dict[str, int]:
        return {
            "limit": int(query.get("limit", ["100"])[0]),
            "offset": int(query.get("offset", ["0"])[0]),
        }

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        origin: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers(origin)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        status: int,
        code: str,
        message: str,
        origin: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(
            status,
            {"error": {"code": code, "message": message}},
            origin,
            extra_headers=extra_headers,
        )

    def _cors_headers(self, origin: str | None) -> None:
        if origin and self.server.settings.is_origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def log_message(self, format: str, *args: Any) -> None:
        # Keep standard access logging but never log headers or request bodies.
        super().log_message(format, *args)


def make_server(
    settings: Settings,
    service: CompanionService,
    tokens: TokenStore,
    *,
    host: str | None = None,
    port: int | None = None,
) -> CompanionHTTPServer:
    return CompanionHTTPServer(
        (host or settings.host, settings.port if port is None else port),
        CompanionHandler,
        settings=settings,
        service=service,
        tokens=tokens,
    )

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import traceback
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from . import __version__
from .auth import TokenStore
from .config import Settings
from .existing_adapter import ExistingEngineAdapter
from .operator_backend import OperatorBackend
from .service import CompanionService, ServiceError, ValidationError


API_PREFIX = "/api/v1"
LOCAL_UI_BOOTSTRAP_ROUTE = "/local-ui/bootstrap"
LOCAL_UI_ACTIVATION_ROUTE = "/local-ui/activate"
LOCAL_UI_ACTIVATION_PAGE = "/local-activate"
LOCAL_UI_COOKIE_NAME = "recruiting_engine_local_ui"
LOCAL_UI_HEADER = "X-Recruiting-Engine-Local-UI"
LOCAL_UI_HEADER_VALUE = "1"
LOCAL_UI_SERVER_HEADER = "X-Recruiting-Engine-Local-UI-Server"
LOCAL_UI_SERVER_HEADER_VALUE = "1"
LOCAL_UI_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
STATIC_COMPATIBILITY_MARKER = "release-compatibility.json"
STATIC_INTEGRITY_MARKER = "static-integrity.json"
STATIC_PRODUCT_VERSION = "1.3.0"
STATIC_COMPATIBILITY_SCHEMA = "recruiting_engine.static_compatibility"
STATIC_INTEGRITY_SCHEMA = "recruiting_engine.static_integrity"
_STATIC_MAX_FILES = 5_000
_STATIC_MAX_FILE_BYTES = 64 * 1024 * 1024
_STATIC_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".woff2": "font/woff2",
}
_STATIC_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "worker-src 'self'"
)
_LOCAL_ACTIVATION_HTML = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Opening Recruiting Engine</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center;
      background: #07110f; color: #eefaf5; }
    main { width: min(34rem, calc(100vw - 3rem)); padding: 2rem;
      border: 1px solid #24433a; border-radius: 1rem; background: #0d1c18; }
    p { color: #b7cec5; line-height: 1.55; }
  </style>
</head>
<body>
  <main>
    <h1>Opening your local cockpit</h1>
    <p id="status">Establishing a private, device-local session...</p>
  </main>
  <script>
    (() => {
      const status = document.getElementById("status");
      let ticket = location.hash.startsWith("#") ? location.hash.slice(1) : "";
      history.replaceState(null, "", "/local-activate/");
      if (!/^re_activate_[A-Za-z0-9_-]{20,160}$/.test(ticket)) {
        status.textContent = "This activation link is missing or invalid. " +
          "Reopen the cockpit from the local launcher.";
        return;
      }
      fetch("/api/v1/local-ui/activate", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Recruiting-Engine-Local-UI": "1"
        },
        body: JSON.stringify({ ticket })
      }).then((response) => {
        ticket = "";
        if (!response.ok) throw new Error("activation_failed");
        location.replace("/app/");
      }).catch(() => {
        ticket = "";
        status.textContent = "Activation expired or was already used. " +
          "Reopen the cockpit from the local launcher.";
      });
    })();
  </script>
</body>
</html>
"""
RESOURCE_SINGULAR = {
    "jobs": "job",
    "companies": "company",
    "contacts": "contact",
    "applications": "application",
}


def _default_static_root() -> Path:
    return Path(__file__).resolve().parents[2] / "static-export"


def _validated_static_root(
    path: Path,
) -> tuple[Path, dict[str, Any], dict[str, tuple[str, int]]]:
    """Validate the generated export before opening a local UI surface."""
    root = _validated_static_structure(path, require_integrity=True)
    compatibility = _validated_static_compatibility(root)
    inventory = _validated_static_integrity(root)
    return root, compatibility, inventory


def _validated_static_structure(path: Path, *, require_integrity: bool) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ValueError("static-export root must not be a symbolic link")
    try:
        root = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"static-export does not exist: {candidate}") from error
    if not root.is_dir():
        raise ValueError(f"static-export is not a directory: {candidate}")

    required_entries = [
        (Path("index.html"), False),
        (Path("app/index.html"), False),
        (Path("assets"), True),
        (Path(STATIC_COMPATIBILITY_MARKER), False),
    ]
    if require_integrity:
        required_entries.append((Path(STATIC_INTEGRITY_MARKER), False))
    for relative, expected_directory in required_entries:
        required = root / relative
        if required.is_symlink():
            raise ValueError(f"static-export entry must not be a symlink: {relative}")
        if expected_directory and not required.is_dir():
            raise ValueError(f"static-export is missing directory: {relative}")
        if not expected_directory and not required.is_file():
            raise ValueError(f"static-export is missing file: {relative}")

    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ValueError(
                "static-export contains a symbolic link: "
                + entry.relative_to(root).as_posix()
            )
        mode = entry.stat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(
                "static-export contains a non-regular entry: "
                + entry.relative_to(root).as_posix()
            )
        if (
            stat.S_ISREG(mode)
            and entry.name != ".nojekyll"
            and entry.suffix.lower() not in _STATIC_CONTENT_TYPES
        ):
            raise ValueError(
                "static-export contains an unsupported file type: "
                + entry.relative_to(root).as_posix()
            )
    return root


def _validated_legacy_static_root(path: Path) -> Path:
    """Validate a pre-integrity export before sealing it during stopped service."""
    root = _validated_static_structure(path, require_integrity=False)
    _validated_static_compatibility(root)
    return root


def _seal_legacy_static_root(path: Path) -> None:
    """Add integrity evidence to a legacy tree after its server has stopped."""
    root = _validated_legacy_static_root(path)
    marker = root / STATIC_INTEGRITY_MARKER
    if marker.exists() or marker.is_symlink():
        _validated_static_root(root)
        return

    files: list[dict[str, Any]] = []
    total_bytes = 0
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        relative = entry.relative_to(root).as_posix()
        if relative == STATIC_INTEGRITY_MARKER:
            continue
        try:
            descriptor = os.open(
                entry,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > _STATIC_MAX_FILE_BYTES
                ):
                    raise ValueError("legacy static entry is invalid")
                content = stream.read(_STATIC_MAX_FILE_BYTES + 1)
        except OSError as error:
            raise ValueError("legacy static entry is unreadable") from error
        if len(content) != metadata.st_size:
            raise ValueError("legacy static entry changed while sealing")
        total_bytes += len(content)
        if total_bytes > _STATIC_MAX_TOTAL_BYTES or len(files) >= _STATIC_MAX_FILES:
            raise ValueError("legacy static export exceeds integrity bounds")
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    payload = (
        json.dumps(
            {
                "schema": STATIC_INTEGRITY_SCHEMA,
                "schema_version": 1,
                "files": files,
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary = root / f"static-integrity.tmp.{os.getpid()}.json"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        _validated_static_root(root)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validated_static_compatibility(root: Path) -> dict[str, Any]:
    marker = root / STATIC_COMPATIBILITY_MARKER
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(marker, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("static compatibility marker is not regular")
            if metadata.st_size < 2 or metadata.st_size > 4096:
                raise ValueError("static compatibility marker has an invalid size")
            raw = stream.read(4097)
    except OSError as error:
        raise ValueError("static compatibility marker is unreadable") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("static compatibility marker is invalid JSON") from error
    expected_keys = {
        "schema",
        "schema_version",
        "product_version",
        "compatible_companion_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("static compatibility marker has an invalid structure")
    if payload.get("schema") != STATIC_COMPATIBILITY_SCHEMA:
        raise ValueError("static compatibility marker schema is unsupported")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise ValueError("static compatibility marker version is unsupported")
    product_version = payload.get("product_version")
    companion_version = payload.get("compatible_companion_version")
    if product_version != STATIC_PRODUCT_VERSION:
        raise ValueError("static product version does not match this release")
    if companion_version != __version__:
        raise ValueError(
            "static export requires a different companion version"
        )
    return {
        "product_version": product_version,
        "compatible_companion_version": companion_version,
    }


def _validated_static_integrity(root: Path) -> dict[str, tuple[str, int]]:
    marker = root / STATIC_INTEGRITY_MARKER
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(marker, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("static integrity marker is not regular")
            if metadata.st_size < 2 or metadata.st_size > 1024 * 1024:
                raise ValueError("static integrity marker has an invalid size")
            raw = stream.read(1024 * 1024 + 1)
    except OSError as error:
        raise ValueError("static integrity marker is unreadable") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("static integrity marker is invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "schema_version",
        "files",
    }:
        raise ValueError("static integrity marker has an invalid structure")
    if payload.get("schema") != STATIC_INTEGRITY_SCHEMA:
        raise ValueError("static integrity marker schema is unsupported")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise ValueError("static integrity marker version is unsupported")
    files = payload.get("files")
    if not isinstance(files, list) or not (1 <= len(files) <= _STATIC_MAX_FILES):
        raise ValueError("static integrity marker files are invalid")

    declared: dict[str, tuple[str, int]] = {}
    previous_path = ""
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise ValueError("static integrity entry has an invalid structure")
        relative = item.get("path")
        digest = item.get("sha256")
        size_bytes = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative) > 512
            or "\\" in relative
            or "\x00" in relative
        ):
            raise ValueError("static integrity entry has an invalid path")
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or relative_path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or relative == STATIC_INTEGRITY_MARKER
            or relative <= previous_path
        ):
            raise ValueError("static integrity entries are not canonical and sorted")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("static integrity entry has an invalid digest")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
            or size_bytes > _STATIC_MAX_FILE_BYTES
        ):
            raise ValueError("static integrity entry has an invalid size")
        declared[relative] = (digest, size_bytes)
        previous_path = relative

    actual_paths = {
        entry.relative_to(root).as_posix()
        for entry in root.rglob("*")
        if entry.is_file() and entry.name != STATIC_INTEGRITY_MARKER
    }
    if actual_paths != set(declared):
        raise ValueError("static integrity inventory does not match the export tree")

    total_bytes = 0
    inventory: dict[str, tuple[str, int]] = {}
    for relative, expected in declared.items():
        path = root / relative
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("static integrity entry is not regular")
                if metadata.st_size != expected[1]:
                    raise ValueError("static integrity size does not match")
                content = stream.read(_STATIC_MAX_FILE_BYTES + 1)
        except OSError as error:
            raise ValueError("static integrity entry is unreadable") from error
        if len(content) != expected[1] or hashlib.sha256(content).hexdigest() != expected[0]:
            raise ValueError("static integrity digest does not match")
        total_bytes += len(content)
        if total_bytes > _STATIC_MAX_TOTAL_BYTES:
            raise ValueError("static export exceeds the integrity size limit")
        inventory[relative] = expected
    inventory[STATIC_INTEGRITY_MARKER] = (
        hashlib.sha256(raw).hexdigest(),
        len(raw),
    )
    return inventory


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
        static_root: Path,
    ):
        validated_static_root, compatibility, inventory = _validated_static_root(
            static_root
        )
        super().__init__(address, handler)
        self.settings = settings
        self.service = service
        self.tokens = tokens
        self.static_root = validated_static_root
        self.static_inventory = inventory
        self.static_product_version = str(compatibility["product_version"])
        self.static_compatible_companion_version = str(
            compatibility["compatible_companion_version"]
        )
        self.local_ui_enabled = address[0].lower() in _LOOPBACK_HOSTS


class CompanionHandler(BaseHTTPRequestHandler):
    server: CompanionHTTPServer
    server_version = f"RecruitingEngineCompanion/{__version__}"
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
        self._security_headers(static=False)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

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
                if method in {"GET", "HEAD"}:
                    if path == LOCAL_UI_ACTIVATION_PAGE:
                        self._serve_local_activation_page(
                            head_only=method == "HEAD"
                        )
                    else:
                        self._serve_static(
                            parsed.path,
                            head_only=method == "HEAD",
                        )
                else:
                    self._send_error(
                        HTTPStatus.NOT_FOUND,
                        "not_found",
                        "Route not found",
                    )
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
            if method == "GET" and route == LOCAL_UI_BOOTSTRAP_ROUTE:
                if not self.server.local_ui_enabled or not self._is_local_ui_request():
                    self._send_error(
                        HTTPStatus.FORBIDDEN,
                        "local_ui_guard_required",
                        "Local UI bootstrap requires a same-origin loopback request",
                        origin,
                        extra_headers=self._local_ui_server_headers(),
                    )
                    return
                if not self._local_cookie_is_valid():
                    self._send_error(
                        HTTPStatus.UNAUTHORIZED,
                        "local_ui_activation_required",
                        "Open the cockpit with the local launcher to activate this browser",
                        origin,
                        extra_headers=self._local_ui_server_headers(),
                    )
                    return
                credential = self.server.tokens.local_ui_credential()
                if not credential:
                    self._send_error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "local_ui_credential_unavailable",
                        "Local authentication is unavailable; run the explicit auth repair command",
                        origin,
                        extra_headers=self._local_ui_server_headers(),
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "mode": "local_primary",
                        "api_base": API_PREFIX,
                        "cookie_authenticated": self._local_cookie_is_valid(),
                        "version": __version__,
                        "companion_version": __version__,
                        "product_version": self.server.static_product_version,
                        "compatible_companion_version": (
                            self.server.static_compatible_companion_version
                        ),
                    },
                    origin,
                    extra_headers=self._local_ui_server_headers(
                        {"Set-Cookie": self._local_cookie(credential)}
                    ),
                )
                return
            if method == "POST" and route == LOCAL_UI_ACTIVATION_ROUTE:
                if not self.server.local_ui_enabled or not self._is_local_ui_request():
                    self._send_error(
                        HTTPStatus.FORBIDDEN,
                        "local_ui_guard_required",
                        "Local UI activation requires a same-origin loopback request",
                        origin,
                        extra_headers=self._local_ui_server_headers(),
                    )
                    return
                body = self._json_body()
                ticket = body.get("ticket")
                credential = (
                    self.server.tokens.consume_local_activation_ticket(ticket)
                    if isinstance(ticket, str)
                    and re.fullmatch(
                        r"re_activate_[A-Za-z0-9_-]{20,160}",
                        ticket,
                    )
                    else None
                )
                if not credential:
                    self._send_error(
                        HTTPStatus.UNAUTHORIZED,
                        "invalid_local_activation",
                        "Local activation is invalid, expired, or already used",
                        origin,
                        extra_headers=self._local_ui_server_headers(),
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {"activated": True, "redirect": "/app/"},
                    origin,
                    extra_headers=self._local_ui_server_headers(
                        {"Set-Cookie": self._local_cookie(credential)}
                    ),
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
            auth_scope = getattr(self, "_auth_scope", None)
            if auth_scope in {"web", "local_ui"}:
                message = (
                    "The local UI cannot rotate or reveal the long-lived local bearer"
                    if auth_scope == "local_ui"
                    else "Web sessions cannot rotate the long-lived local bearer"
                )
                self._send_error(
                    HTTPStatus.FORBIDDEN,
                    "insufficient_scope",
                    message,
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
            if method == "GET" and route == "/operator/progress":
                self._send_json(
                    HTTPStatus.OK,
                    operator.progress(),
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

    def _serve_local_activation_page(self, *, head_only: bool) -> None:
        if not self.server.local_ui_enabled:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Route not found",
            )
            return
        self._send_static(
            HTTPStatus.OK,
            _LOCAL_ACTIVATION_HTML,
            "text/html; charset=utf-8",
            head_only=head_only,
        )

    def _serve_static(self, request_path: str, *, head_only: bool) -> None:
        if not self.server.local_ui_enabled:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Route not found",
            )
            return
        try:
            decoded_path = unquote(request_path, encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_static_path",
                "Static path is not valid UTF-8",
            )
            return
        if (
            not decoded_path.startswith("/")
            or "\x00" in decoded_path
            or "\\" in decoded_path
        ):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_static_path",
                "Static path is invalid",
            )
            return
        parts = [part for part in decoded_path.split("/") if part]
        if any(part in {".", ".."} or part.startswith(".") for part in parts):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_static_path",
                "Static path contains a forbidden segment",
            )
            return

        root = self.server.static_root
        relative = Path(*parts) if parts else Path()
        allow_document_fallback = (
            not relative.suffix and (not parts or parts[0] != "assets")
        )
        candidate = root / relative
        if not parts or decoded_path.endswith("/") or candidate.is_dir():
            candidate = candidate / "index.html"
        elif not candidate.exists() and not candidate.suffix:
            candidate = candidate / "index.html"

        status = HTTPStatus.OK
        safe_candidate = self._safe_static_file(candidate)
        if safe_candidate is None:
            # Missing asset URLs must never receive HTML.  Clean document routes
            # get the generated export's 404 page with an honest 404 status.
            if not allow_document_fallback:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "not_found",
                    "Static asset not found",
                )
                return
            safe_candidate = self._safe_static_file(root / "404.html")
            status = HTTPStatus.NOT_FOUND
            if safe_candidate is None:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "not_found",
                    "Route not found",
                )
                return

        content_type = _STATIC_CONTENT_TYPES.get(safe_candidate.suffix.lower())
        if content_type is None:
            self._send_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_static_media_type",
                "Static file type is not allowed",
            )
            return
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(safe_candidate, flags)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("static path is not a regular file")
                body = stream.read()
        except OSError:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Static asset not found",
            )
            return

        relative_name = safe_candidate.relative_to(root).as_posix()
        expected_integrity = self.server.static_inventory.get(relative_name)
        if (
            expected_integrity is None
            or len(body) != expected_integrity[1]
            or hashlib.sha256(body).hexdigest() != expected_integrity[0]
        ):
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "static_export_changed",
                "The local UI export changed after validation",
            )
            return

        self._send_static(
            status,
            body,
            content_type,
            head_only=head_only,
        )

    def _safe_static_file(self, candidate: Path) -> Path | None:
        root = self.server.static_root
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError):
            return None
        try:
            if not stat.S_ISREG(resolved.stat().st_mode):
                return None
        except OSError:
            return None
        return resolved

    def _send_static(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        head_only: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._security_headers(static=True)
        self.send_header(
            LOCAL_UI_SERVER_HEADER,
            LOCAL_UI_SERVER_HEADER_VALUE,
        )
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    @staticmethod
    def _local_cookie(credential: str) -> str:
        if not re.fullmatch(r"re_ui_[a-f0-9]{64}", credential):
            raise ValueError("local UI credential has an invalid format")
        return (
            f"{LOCAL_UI_COOKIE_NAME}={credential}; Path=/; "
            f"Max-Age={LOCAL_UI_COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict"
        )

    @staticmethod
    def _local_ui_server_headers(
        additional: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            LOCAL_UI_SERVER_HEADER: LOCAL_UI_SERVER_HEADER_VALUE,
        }
        headers.update(additional or {})
        return headers

    def _local_cookie_token(self) -> str | None:
        values = self.headers.get_all("Cookie") or []
        if not values:
            return None
        raw = "; ".join(values)
        if len(raw) > 8192:
            return None
        occurrences = re.findall(
            rf"(?:^|;\s*){re.escape(LOCAL_UI_COOKIE_NAME)}\s*=",
            raw,
        )
        if len(occurrences) != 1:
            return None
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except CookieError:
            return None
        morsel = parsed.get(LOCAL_UI_COOKIE_NAME)
        if morsel is None:
            return None
        token = morsel.value
        if len(token) > 256 or not re.fullmatch(r"re_ui_[a-f0-9]{64}", token):
            return None
        return token

    def _local_cookie_is_valid(self) -> bool:
        token = self._local_cookie_token()
        return bool(
            token and self.server.tokens.verify_local_ui_credential(token)
        )

    def _is_local_ui_request(self) -> bool:
        if self.headers.get(LOCAL_UI_HEADER) != LOCAL_UI_HEADER_VALUE:
            return False
        host_header = self.headers.get("Host")
        if not self.server.settings.is_host_allowed(
            host_header,
            self.server.server_port,
        ):
            return False

        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if origin:
            return self._matches_request_origin(origin, allow_path=False)
        if fetch_site:
            if fetch_site.lower() != "same-origin":
                return False
            return not referer or self._matches_request_origin(
                referer,
                allow_path=True,
            )
        return bool(
            referer
            and self._matches_request_origin(referer, allow_path=True)
        )

    def _matches_request_origin(self, value: str, *, allow_path: bool) -> bool:
        try:
            request_host = urlsplit(f"//{self.headers.get('Host', '')}")
            candidate = urlsplit(value)
            request_port = request_host.port
            candidate_port = candidate.port
        except ValueError:
            return False
        if (
            candidate.scheme.lower() != "http"
            or candidate.username is not None
            or candidate.password is not None
            or (candidate.hostname or "").lower()
            != (request_host.hostname or "").lower()
            or candidate_port != request_port
            or (candidate.query and not allow_path)
            or candidate.fragment
        ):
            return False
        if allow_path:
            return True
        return candidate.path in {"", "/"}

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
            ("GET", "/operator/progress"),
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
        scope: str | None = None
        if authorization:
            scheme, _, token = authorization.partition(" ")
            scope = (
                self.server.tokens.authenticate_token(token.strip())
                if scheme.lower() == "bearer"
                else None
            )
        else:
            cookie_token = self._local_cookie_token()
            if cookie_token:
                if not self.server.local_ui_enabled or not self._is_local_ui_request():
                    self._send_error(
                        HTTPStatus.FORBIDDEN,
                        "local_ui_guard_required",
                        "Cookie authentication requires a same-origin local UI request",
                        origin,
                    )
                    return False
                if self.server.tokens.verify_local_ui_credential(cookie_token):
                    scope = "local_ui"
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

    def _security_headers(self, *, static: bool) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            _STATIC_CSP
            if static
            else "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        if static:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")

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
        self._security_headers(static=False)
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
            self.send_header(
                "Access-Control-Expose-Headers",
                LOCAL_UI_SERVER_HEADER,
            )
            self.send_header("Vary", "Origin")

    def log_request(
        self,
        code: int | str = "-",
        size: int | str = "-",
    ) -> None:
        # Fragments are never sent over HTTP, and query values are unnecessary
        # for access diagnostics. Log only a redacted path so even a malformed
        # request cannot place auth material in the companion logs.
        safe_path = urlsplit(self.path).path
        safe_path = re.sub(
            r"re_(?:activate|local|pair|web|ui)_[A-Za-z0-9_-]+",
            "[REDACTED]",
            safe_path,
        )
        self.log_message(
            '"%s %s %s" %s %s',
            self.command,
            safe_path,
            self.request_version,
            str(code),
            str(size),
        )

    def log_message(self, format: str, *args: Any) -> None:
        redacted = tuple(
            re.sub(
                r"re_(?:activate|local|pair|web|ui)_[A-Za-z0-9_-]+",
                "[REDACTED]",
                str(argument),
            )
            for argument in args
        )
        super().log_message(format, *redacted)


def make_server(
    settings: Settings,
    service: CompanionService,
    tokens: TokenStore,
    *,
    host: str | None = None,
    port: int | None = None,
    static_root: Path | None = None,
) -> CompanionHTTPServer:
    return CompanionHTTPServer(
        (host or settings.host, settings.port if port is None else port),
        CompanionHandler,
        settings=settings,
        service=service,
        tokens=tokens,
        static_root=static_root or _default_static_root(),
    )

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


_SAFE_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LOCAL_ORIGIN = re.compile(
    r"^http://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$",
    re.IGNORECASE,
)
_CHROME_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    user_id: str = "default"
    host: str = "127.0.0.1"
    port: int = 8765
    max_upload_bytes: int = 10 * 1024 * 1024
    allow_remote_bind: bool = False
    hosted_origin: str = "https://axe-pat.github.io"
    default_mode: str = "portable"
    resumegen_root: Path | None = None
    outreach_root: Path | None = None
    runtime_dir: Path | None = None
    attestation_path: Path | None = None
    resume_python: Path | None = None
    outreach_python: Path | None = None
    allow_live_runs: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(
            os.environ.get(
                "RECRUITING_ENGINE_DATA_DIR",
                "~/.recruiting-engine-companion",
            )
        ).expanduser()
        user_id = os.environ.get("RECRUITING_ENGINE_USER_ID", "default")
        host = os.environ.get("RECRUITING_ENGINE_HOST", "127.0.0.1")
        port = int(os.environ.get("RECRUITING_ENGINE_PORT", "8765"))
        max_upload = int(
            os.environ.get(
                "RECRUITING_ENGINE_MAX_UPLOAD_BYTES",
                str(10 * 1024 * 1024),
            )
        )
        settings = cls(
            data_dir=data_dir,
            user_id=user_id,
            host=host,
            port=port,
            max_upload_bytes=max_upload,
            allow_remote_bind=_env_bool("RECRUITING_ENGINE_ALLOW_REMOTE"),
            hosted_origin=os.environ.get(
                "RECRUITING_ENGINE_HOSTED_ORIGIN",
                "https://axe-pat.github.io",
            ).rstrip("/"),
            default_mode=os.environ.get(
                "RECRUITING_ENGINE_MODE", "portable"
            ).strip().lower(),
            resumegen_root=(
                Path(
                    os.environ.get("RECRUITING_ENGINE_RESUME_ROOT")
                    or os.environ["RESUMEGEN_ROOT"]
                ).expanduser()
                if (
                    os.environ.get("RECRUITING_ENGINE_RESUME_ROOT")
                    or os.environ.get("RESUMEGEN_ROOT")
                )
                else None
            ),
            outreach_root=(
                Path(
                    os.environ.get("RECRUITING_ENGINE_OUTREACH_ROOT")
                    or os.environ["OUTREACH_ROOT"]
                ).expanduser()
                if (
                    os.environ.get("RECRUITING_ENGINE_OUTREACH_ROOT")
                    or os.environ.get("OUTREACH_ROOT")
                )
                else None
            ),
            runtime_dir=(
                Path(os.environ["RECRUITING_ENGINE_RUNTIME_DIR"]).expanduser()
                if os.environ.get("RECRUITING_ENGINE_RUNTIME_DIR")
                else None
            ),
            attestation_path=(
                Path(os.environ["RECRUITING_ENGINE_ATTESTATION_PATH"]).expanduser()
                if os.environ.get("RECRUITING_ENGINE_ATTESTATION_PATH")
                else None
            ),
            resume_python=(
                Path(os.environ["RECRUITING_ENGINE_RESUME_PYTHON"]).expanduser()
                if os.environ.get("RECRUITING_ENGINE_RESUME_PYTHON")
                else None
            ),
            outreach_python=(
                Path(os.environ["RECRUITING_ENGINE_OUTREACH_PYTHON"]).expanduser()
                if os.environ.get("RECRUITING_ENGINE_OUTREACH_PYTHON")
                else None
            ),
            allow_live_runs=_env_bool("RECRUITING_ENGINE_ALLOW_LIVE_RUNS"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _SAFE_USER_ID.fullmatch(self.user_id):
            raise ValueError(
                "RECRUITING_ENGINE_USER_ID must contain only letters, numbers, "
                "underscores, or hyphens (maximum 64 characters)"
            )
        if not (1 <= self.port <= 65535):
            raise ValueError("RECRUITING_ENGINE_PORT must be between 1 and 65535")
        if self.max_upload_bytes < 1:
            raise ValueError("RECRUITING_ENGINE_MAX_UPLOAD_BYTES must be positive")
        if not self.allow_remote_bind and self.host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(
                "Refusing a non-loopback bind. Set RECRUITING_ENGINE_ALLOW_REMOTE=1 "
                "only if a separate TLS/auth boundary is in place."
            )
        if not self.hosted_origin.startswith("https://"):
            raise ValueError("The hosted origin must use HTTPS")
        if self.default_mode not in {"portable", "existing"}:
            raise ValueError(
                "RECRUITING_ENGINE_MODE must be portable or existing"
            )

    @property
    def user_dir(self) -> Path:
        return self.data_dir / "users" / self.user_id

    @property
    def database_path(self) -> Path:
        return self.user_dir / "companion.sqlite3"

    @property
    def documents_dir(self) -> Path:
        return self.user_dir / "documents"

    @property
    def adapter_mutation_lock_path(self) -> Path:
        if self.runtime_dir is not None:
            return self.runtime_dir / "operator_mutation.lock"
        return self.user_dir / "adapter-mutation.lock"

    def prepare(self) -> None:
        self.user_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.documents_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.user_dir.chmod(0o700)
            self.documents_dir.chmod(0o700)
        except OSError:
            pass
        try:
            self.adapter_mutation_lock_path.touch(exist_ok=True, mode=0o600)
            self.adapter_mutation_lock_path.chmod(0o600)
        except OSError:
            pass

    def is_origin_allowed(self, origin: str | None) -> bool:
        if not origin:
            return True
        if origin.rstrip("/") in {
            self.hosted_origin,
            "https://axe-pat.github.io",
        }:
            return True
        if _LOCAL_ORIGIN.fullmatch(origin):
            return True
        return bool(_CHROME_EXTENSION_ORIGIN.fullmatch(origin))

    @staticmethod
    def is_host_allowed(host_header: str | None, bound_port: int) -> bool:
        """Reject non-loopback Host headers to prevent browser DNS rebinding."""
        if not host_header:
            return False
        try:
            parsed = urlsplit(f"//{host_header}")
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        return port == bound_port

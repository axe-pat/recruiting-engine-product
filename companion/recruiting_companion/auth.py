from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _write_private(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


@dataclass(frozen=True, slots=True)
class BootstrapCredentials:
    bearer_token: str | None
    pairing_token: str | None
    bearer_path: Path
    pairing_path: Path


@dataclass(frozen=True, slots=True)
class PairingExchange:
    bearer_token: str
    client_type: str
    expires_in: int | None = None


class TokenStore:
    """Hash-backed bearer auth with a one-time local pairing credential."""

    WEB_SESSION_SECONDS = 30 * 60
    MAX_WEB_SESSIONS = 64

    def __init__(
        self,
        user_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.user_dir = user_dir
        self.state_path = user_dir / "auth.json"
        self.bearer_path = user_dir / "bearer-token.txt"
        self.pairing_path = user_dir / "pairing-token.txt"
        self._lock = Lock()
        self._clock = clock

    def bootstrap(self) -> BootstrapCredentials:
        self.user_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_path.exists():
            return BootstrapCredentials(
                bearer_token=None,
                pairing_token=(
                    self.pairing_path.read_text(encoding="utf-8").strip()
                    if self.pairing_path.exists()
                    else None
                ),
                bearer_path=self.bearer_path,
                pairing_path=self.pairing_path,
            )

        bearer = _new_token("re_local")
        pairing = _new_token("re_pair")
        self._save_state(
            {
                "version": 1,
                "bearer_sha256": _hash_token(bearer),
                "pairing_sha256": _hash_token(pairing),
                "pairing_consumed": False,
                "web_sessions": [],
            }
        )
        _write_private(self.bearer_path, bearer)
        _write_private(self.pairing_path, pairing)
        return BootstrapCredentials(
            bearer_token=bearer,
            pairing_token=pairing,
            bearer_path=self.bearer_path,
            pairing_path=self.pairing_path,
        )

    def verify_bearer(self, token: str) -> bool:
        return self.authenticate_token(token) is not None

    def authenticate_token(self, token: str) -> str | None:
        with self._lock:
            if not token or not self.state_path.exists():
                return None
            state = self._load_state()
            token_hash = _hash_token(token)
            if hmac.compare_digest(
                str(state.get("bearer_sha256", "")),
                token_hash,
            ):
                return "local"
            sessions, changed = self._active_web_sessions(state)
            authenticated = any(
                hmac.compare_digest(str(session.get("sha256", "")), token_hash)
                for session in sessions
            )
            if changed:
                state["web_sessions"] = sessions
                self._save_state(state)
            return "web" if authenticated else None

    def exchange_pairing_token(
        self,
        pairing_token: str,
        *,
        client_type: str = "extension",
    ) -> PairingExchange | None:
        with self._lock:
            return self._exchange_pairing_token(
                pairing_token,
                client_type=client_type,
            )

    def _exchange_pairing_token(
        self,
        pairing_token: str,
        *,
        client_type: str,
    ) -> PairingExchange | None:
        if not pairing_token or not self.state_path.exists():
            return None
        state = self._load_state()
        if state.get("pairing_consumed"):
            return None
        if not hmac.compare_digest(
            str(state.get("pairing_sha256", "")),
            _hash_token(pairing_token),
        ):
            return None

        expires_in: int | None = None
        if client_type == "web":
            bearer = _new_token("re_web")
            expires_in = self.WEB_SESSION_SECONDS
            sessions, _ = self._active_web_sessions(state)
            sessions.append(
                {
                    "sha256": _hash_token(bearer),
                    "expires_at": self._clock() + expires_in,
                }
            )
            state["web_sessions"] = sessions[-self.MAX_WEB_SESSIONS :]
        else:
            bearer = ""
            if self.bearer_path.exists():
                bearer = self.bearer_path.read_text(encoding="utf-8").strip()
            if not bearer or not hmac.compare_digest(
                str(state.get("bearer_sha256", "")), _hash_token(bearer)
            ):
                bearer = _new_token("re_local")
                state["bearer_sha256"] = _hash_token(bearer)
        state["pairing_sha256"] = ""
        state["pairing_consumed"] = True
        self._save_state(state)
        if client_type != "web":
            _write_private(self.bearer_path, bearer)
        try:
            self.pairing_path.unlink()
        except FileNotFoundError:
            pass
        return PairingExchange(
            bearer_token=bearer,
            client_type=client_type,
            expires_in=expires_in,
        )

    def rotate_bearer_token(self) -> str:
        with self._lock:
            return self._rotate_bearer_token()

    def _rotate_bearer_token(self) -> str:
        bearer = _new_token("re_local")
        state = self._load_state()
        state["bearer_sha256"] = _hash_token(bearer)
        state["web_sessions"] = []
        self._save_state(state)
        _write_private(self.bearer_path, bearer)
        return bearer

    def rotate_pairing_token(self) -> str:
        with self._lock:
            return self._rotate_pairing_token()

    def _rotate_pairing_token(self) -> str:
        pairing = _new_token("re_pair")
        state = self._load_state()
        state["pairing_sha256"] = _hash_token(pairing)
        state["pairing_consumed"] = False
        self._save_state(state)
        _write_private(self.pairing_path, pairing)
        return pairing

    def _load_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _active_web_sessions(
        self,
        state: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        raw_sessions = state.get("web_sessions", [])
        if not isinstance(raw_sessions, list):
            return [], True
        now = self._clock()
        active: list[dict[str, Any]] = []
        for session in raw_sessions:
            if not isinstance(session, dict):
                continue
            digest = session.get("sha256")
            expires_at = session.get("expires_at")
            if (
                isinstance(digest, str)
                and digest
                and isinstance(expires_at, (int, float))
                and not isinstance(expires_at, bool)
                and expires_at > now
            ):
                active.append({"sha256": digest, "expires_at": expires_at})
        active = active[-self.MAX_WEB_SESSIONS :]
        return active, active != raw_sessions

    def _save_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.state_path)

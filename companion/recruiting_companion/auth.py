from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _local_ui_credential(bearer: str) -> str:
    digest = hmac.new(
        bearer.encode("utf-8"),
        # v2 deliberately invalidates cookies minted by the earlier unsafe
        # raw-HTML bootstrap. Only the explicit activation flow can establish
        # this credential generation.
        b"recruiting-engine-local-ui-v2",
        hashlib.sha256,
    ).hexdigest()
    return f"re_ui_{digest}"


def _write_private(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


class AuthStateError(RuntimeError):
    """The persisted auth state cannot be trusted without explicit repair."""


class AuthStateHealthyError(RuntimeError):
    """An explicit repair was requested for already-consistent auth state."""


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


@dataclass(frozen=True, slots=True)
class AuthRepairResult:
    state_path: Path
    bearer_path: Path
    pairing_path: Path


class TokenStore:
    """Hash-backed bearer auth with a one-time local pairing credential."""

    # A real nightly cycle can run for several hours. Keep the tab-scoped web
    # session alive for a full operating day so the authenticated cockpit can
    # poll through completion without falling back to a disconnected state.
    WEB_SESSION_SECONDS = 12 * 60 * 60
    MAX_WEB_SESSIONS = 64
    LOCAL_ACTIVATION_SECONDS = 120
    MAX_LOCAL_ACTIVATION_TICKETS = 8

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
        self.lock_path = user_dir / "auth.lock"
        self._lock = Lock()
        self._clock = clock

    def bootstrap(self) -> BootstrapCredentials:
        with self._locked():
            if self.state_path.exists() or self.state_path.is_symlink():
                return BootstrapCredentials(
                    bearer_token=None,
                    pairing_token=self._read_private_token(
                        self.pairing_path,
                        prefix="re_pair_",
                    ),
                    bearer_path=self.bearer_path,
                    pairing_path=self.pairing_path,
                )
            if any(
                path.exists() or path.is_symlink()
                for path in (self.bearer_path, self.pairing_path)
            ):
                raise AuthStateError(
                    "Auth state is incomplete; run the explicit auth repair command"
                )

            bearer = _new_token("re_local")
            pairing = _new_token("re_pair")
            # Write the private files first. If startup is interrupted before the
            # state commit, the next invocation fails closed and requires repair.
            _write_private(self.bearer_path, bearer)
            _write_private(self.pairing_path, pairing)
            self._save_state(
                self._new_state(bearer=bearer, pairing=pairing)
            )
            return BootstrapCredentials(
                bearer_token=bearer,
                pairing_token=pairing,
                bearer_path=self.bearer_path,
                pairing_path=self.pairing_path,
            )

    def verify_bearer(self, token: str) -> bool:
        return self.authenticate_token(token) is not None

    def local_bearer_token(self) -> str | None:
        """Return the persisted local bearer only when it still matches auth state.

        The companion uses this only as server-side key material for its
        loopback UI credential. It must never be placed in an API response body,
        cookie, or otherwise handed to browser JavaScript.
        """
        with self._locked():
            try:
                state = self._load_valid_state()
            except AuthStateError:
                return None
            return self._validated_local_bearer(state)

    def local_ui_credential(self) -> str | None:
        """Derive a restart-stable UI credential without reusing the bearer."""
        bearer = self.local_bearer_token()
        return _local_ui_credential(bearer) if bearer else None

    def verify_local_ui_credential(self, credential: str) -> bool:
        expected = self.local_ui_credential()
        return bool(
            credential
            and expected
            and hmac.compare_digest(expected, credential)
        )

    def authenticate_token(self, token: str) -> str | None:
        with self._locked():
            if not token or not self.state_path.exists():
                return None
            try:
                state = self._load_valid_state()
            except AuthStateError:
                return None
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
        with self._locked():
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
        try:
            state = self._load_valid_state()
        except AuthStateError:
            return None
        local_bearer = self._validated_local_bearer(state)
        if local_bearer is None:
            return None
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
            bearer = local_bearer
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
        with self._locked():
            return self._rotate_bearer_token()

    def _rotate_bearer_token(self) -> str:
        bearer = _new_token("re_local")
        state = self._load_state()
        state["bearer_sha256"] = _hash_token(bearer)
        state["web_sessions"] = []
        state["local_activation_tickets"] = []
        self._save_state(state)
        _write_private(self.bearer_path, bearer)
        return bearer

    def rotate_pairing_token(self) -> str:
        with self._locked():
            return self._rotate_pairing_token()

    def _rotate_pairing_token(self) -> str:
        pairing = _new_token("re_pair")
        state = self._load_state()
        state["pairing_sha256"] = _hash_token(pairing)
        state["pairing_consumed"] = False
        self._save_state(state)
        _write_private(self.pairing_path, pairing)
        return pairing

    def issue_local_activation_ticket(self) -> str:
        """Issue one short-lived ticket after validating the private bearer.

        Only the ticket hash and expiry are persisted. The plaintext ticket is
        returned to the invoking local CLI exactly once.
        """
        with self._locked():
            state = self._load_valid_state()
            if self._validated_local_bearer(state) is None:
                raise AuthStateError(
                    "The private local bearer does not match auth state"
                )
            tickets, _ = self._active_local_activation_tickets(state)
            ticket = _new_token("re_activate")
            tickets.append(
                {
                    "sha256": _hash_token(ticket),
                    "expires_at": self._clock()
                    + self.LOCAL_ACTIVATION_SECONDS,
                }
            )
            state["local_activation_tickets"] = tickets[
                -self.MAX_LOCAL_ACTIVATION_TICKETS :
            ]
            self._save_state(state)
            return ticket

    def consume_local_activation_ticket(self, ticket: str) -> str | None:
        """Consume a single-use activation ticket and return the UI credential."""
        with self._locked():
            if not ticket or not self.state_path.exists():
                return None
            try:
                state = self._load_valid_state()
            except AuthStateError:
                return None
            bearer = self._validated_local_bearer(state)
            if bearer is None:
                return None
            tickets, changed = self._active_local_activation_tickets(state)
            ticket_hash = _hash_token(ticket)
            matched = False
            remaining: list[dict[str, Any]] = []
            for candidate in tickets:
                if not matched and hmac.compare_digest(
                    str(candidate.get("sha256", "")),
                    ticket_hash,
                ):
                    matched = True
                    continue
                remaining.append(candidate)
            if matched or changed:
                state["local_activation_tickets"] = remaining
                self._save_state(state)
            if not matched:
                return None
            return _local_ui_credential(bearer)

    def repair_auth(self) -> AuthRepairResult:
        """Replace inconsistent auth material without printing any credential."""
        with self._locked():
            if self._auth_consistency_error() is None:
                raise AuthStateHealthyError(
                    "Auth state is already consistent; repair was not performed"
                )
            bearer = _new_token("re_local")
            pairing = _new_token("re_pair")
            # This deliberately invalidates all cookies, web sessions, pairing
            # material, and outstanding activation tickets.
            _write_private(self.bearer_path, bearer)
            _write_private(self.pairing_path, pairing)
            self._save_state(self._new_state(bearer=bearer, pairing=pairing))
            return AuthRepairResult(
                state_path=self.state_path,
                bearer_path=self.bearer_path,
                pairing_path=self.pairing_path,
            )

    def _load_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _load_valid_state(self) -> dict[str, Any]:
        if (
            not self.state_path.exists()
            or self.state_path.is_symlink()
            or not self.state_path.is_file()
        ):
            raise AuthStateError("Auth state file is missing or unsafe")
        try:
            if self.state_path.stat().st_mode & 0o077:
                raise AuthStateError("Auth state file permissions are unsafe")
            state = self._load_state()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthStateError("Auth state file is unreadable") from error
        if not isinstance(state, dict):
            raise AuthStateError("Auth state file has an invalid structure")
        version = state.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 1:
            raise AuthStateError("Auth state version is unsupported")
        bearer_hash = state.get("bearer_sha256")
        if not isinstance(bearer_hash, str) or not _is_sha256_digest(bearer_hash):
            raise AuthStateError("Auth state does not contain a valid bearer hash")
        pairing_consumed = state.get("pairing_consumed")
        pairing_hash = state.get("pairing_sha256")
        if not isinstance(pairing_consumed, bool) or not isinstance(
            pairing_hash,
            str,
        ):
            raise AuthStateError("Auth state has invalid pairing metadata")
        if pairing_consumed:
            if pairing_hash:
                raise AuthStateError("Consumed pairing state retains a token hash")
        elif not _is_sha256_digest(pairing_hash):
            raise AuthStateError("Active pairing state has an invalid token hash")
        if not isinstance(state.get("web_sessions"), list):
            raise AuthStateError("Auth state has invalid web session metadata")
        if "local_activation_tickets" in state and not isinstance(
            state["local_activation_tickets"],
            list,
        ):
            raise AuthStateError("Auth state has invalid activation metadata")
        return state

    def _validated_local_bearer(self, state: dict[str, Any]) -> str | None:
        bearer = self._read_private_token(
            self.bearer_path,
            prefix="re_local_",
        )
        if bearer is None or not hmac.compare_digest(
            str(state.get("bearer_sha256", "")),
            _hash_token(bearer),
        ):
            return None
        return bearer

    @staticmethod
    def _read_private_token(path: Path, *, prefix: str) -> str | None:
        if not path.exists() or path.is_symlink() or not path.is_file():
            return None
        try:
            if path.stat().st_mode & 0o077:
                return None
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not token.startswith(prefix) or len(token) > 256:
            return None
        return token

    def _auth_consistency_error(self) -> str | None:
        try:
            state = self._load_valid_state()
        except AuthStateError as error:
            return str(error)
        if self._validated_local_bearer(state) is None:
            return "The private local bearer does not match auth state"
        return None

    @staticmethod
    def _new_state(*, bearer: str, pairing: str) -> dict[str, Any]:
        return {
            "version": 1,
            "bearer_sha256": _hash_token(bearer),
            "pairing_sha256": _hash_token(pairing),
            "pairing_consumed": False,
            "web_sessions": [],
            "local_activation_tickets": [],
        }

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

    def _active_local_activation_tickets(
        self,
        state: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        raw_tickets = state.get("local_activation_tickets", [])
        if not isinstance(raw_tickets, list):
            return [], True
        now = self._clock()
        active: list[dict[str, Any]] = []
        for ticket in raw_tickets:
            if not isinstance(ticket, dict):
                continue
            digest = ticket.get("sha256")
            expires_at = ticket.get("expires_at")
            if (
                isinstance(digest, str)
                and _is_sha256_digest(digest)
                and isinstance(expires_at, (int, float))
                and not isinstance(expires_at, bool)
                and expires_at > now
            ):
                active.append({"sha256": digest, "expires_at": expires_at})
        active = active[-self.MAX_LOCAL_ACTIVATION_TICKETS :]
        return active, active != raw_tickets

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.user_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_path, flags, 0o600)
            try:
                try:
                    os.fchmod(descriptor, 0o600)
                except OSError:
                    pass
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

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


def _is_sha256_digest(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )

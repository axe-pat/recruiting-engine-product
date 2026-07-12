#!/usr/bin/env python3
"""Fail closed when restarting the companion could abandon active work."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import select
import sqlite3
import stat
import sys
import time
from pathlib import Path
from urllib.parse import quote


ACTIVE_EXIT = 75
ERROR_EXIT = 2
_SAFE_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class RestartSafetyError(RuntimeError):
    pass


def _lock_is_busy(path: Path, *, label: str) -> bool:
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise RestartSafetyError(f"{label} lock is missing or unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(
        os,
        "O_CLOEXEC",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RestartSafetyError(f"{label} lock could not be inspected") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RestartSafetyError(f"{label} lock is not a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError as error:
            raise RestartSafetyError(
                f"{label} lock state could not be established"
            ) from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as error:
            raise RestartSafetyError(f"{label} lock could not be released") from error
        return False
    finally:
        os.close(descriptor)


def _open_adapter_exclusive(path: Path, *, timeout_seconds: float) -> int:
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise RestartSafetyError("adapter mutation lock is missing or unsafe")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(
        os,
        "O_CLOEXEC",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RestartSafetyError(
            "adapter mutation lock could not be opened"
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RestartSafetyError("adapter mutation lock is not a regular file")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RestartSafetyError(
                        "adapter mutation interlock acquisition timed out"
                    )
                time.sleep(0.05)
            except OSError as error:
                raise RestartSafetyError(
                    "adapter mutation interlock could not be acquired"
                ) from error
    except Exception:
        os.close(descriptor)
        raise


def _validate_private_fifo(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise RestartSafetyError(f"{label} FIFO path must be absolute")
    try:
        metadata = path.lstat()
        parent = path.parent.stat()
    except OSError as error:
        raise RestartSafetyError(f"{label} FIFO is unavailable") from error
    if not stat.S_ISFIFO(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RestartSafetyError(f"{label} FIFO is unsafe")
    if metadata.st_mode & 0o077:
        raise RestartSafetyError(f"{label} FIFO permissions are unsafe")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise RestartSafetyError(f"{label} FIFO directory is unsafe")


def _open_private_fifo(path: Path, flags: int, *, label: str) -> int:
    _validate_private_fifo(path, label=label)
    try:
        descriptor = os.open(
            path,
            flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise RestartSafetyError(f"{label} FIFO could not be opened") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise RestartSafetyError(f"{label} FIFO changed identity")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _write_ready_signal(path: Path, message: str) -> None:
    descriptor = _open_private_fifo(
        path,
        os.O_WRONLY | os.O_NONBLOCK,
        label="ready",
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(message + "\n")
        stream.flush()


def _wait_for_signal(
    path: Path,
    *,
    label: str,
    timeout_seconds: float,
) -> str | None:
    descriptor = _open_private_fifo(
        path,
        os.O_RDONLY | os.O_NONBLOCK,
        label=label,
    )
    collected = bytearray()
    deadline = time.monotonic() + timeout_seconds
    try:
        while len(collected) <= 64:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            readable, _, _ = select.select(
                [descriptor], [], [], min(1.0, remaining)
            )
            if not readable:
                continue
            try:
                chunk = os.read(descriptor, 64 - len(collected))
            except BlockingIOError:
                continue
            if not chunk:
                # The installer disappeared and closed its FIFO writer. Releasing
                # on EOF prevents an orphan helper from deadlocking future runs.
                return None
            collected.extend(chunk)
            if b"\n" in collected:
                try:
                    return bytes(collected).split(b"\n", 1)[0].decode("ascii")
                except UnicodeDecodeError:
                    return None
        return None
    finally:
        os.close(descriptor)


def _has_active_operator_job(path: Path, *, user_id: str) -> bool:
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_file():
        raise RestartSafetyError("companion database is unsafe")
    try:
        if path.stat().st_mode & 0o077:
            raise RestartSafetyError("companion database permissions are unsafe")
    except OSError as error:
        raise RestartSafetyError("companion database could not be inspected") from error

    database_uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    deadline = time.monotonic() + 1.0
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=0.25)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 250")
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0,
                500,
            )
            table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'operator_jobs'
                LIMIT 1
                """
            ).fetchone()
            if table is None:
                return False
            return _connection_has_active_operator_job(
                connection,
                user_id=user_id,
            )
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise RestartSafetyError(
            "companion operator job state could not be read safely"
        ) from error


def _connection_has_active_operator_job(
    connection: sqlite3.Connection,
    *,
    user_id: str,
) -> bool:
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'operator_jobs'
        LIMIT 1
        """
    ).fetchone()
    if table is None:
        return False
    row = connection.execute(
        """
        SELECT 1 FROM operator_jobs
        WHERE user_id = ? AND status IN ('queued', 'running')
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return row is not None


def _open_database_write_gate(
    path: Path,
    *,
    timeout_seconds: float,
    required: bool,
) -> sqlite3.Connection | None:
    """Hold SQLite's writer slot so a legacy companion cannot admit work."""
    if not path.exists():
        if required:
            raise RestartSafetyError(
                "legacy companion database is missing; writer quiescence is unavailable"
            )
        return None
    if path.is_symlink() or not path.is_file():
        raise RestartSafetyError("companion database is unsafe")
    try:
        if path.stat().st_mode & 0o077:
            raise RestartSafetyError("companion database permissions are unsafe")
    except OSError as error:
        raise RestartSafetyError(
            "companion database could not be inspected"
        ) from error

    database_uri = f"file:{quote(str(path), safe='/')}?mode=rw"
    try:
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=0,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = 0")
    except sqlite3.Error as error:
        raise RestartSafetyError(
            "companion database writer gate could not be opened"
        ) from error

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                connection.execute("BEGIN IMMEDIATE")
                return connection
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).casefold():
                    raise RestartSafetyError(
                        "companion database writer gate could not be acquired"
                    ) from error
                if time.monotonic() >= deadline:
                    raise RestartSafetyError(
                        "companion database writer gate acquisition timed out"
                    ) from error
                time.sleep(0.05)
    except Exception:
        connection.close()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether the operator companion can be restarted safely"
    )
    parser.add_argument("--scheduler-lock", required=True, type=Path)
    parser.add_argument("--pipeline-lock", required=True, type=Path)
    parser.add_argument("--adapter-lock", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--ready-fifo", type=Path)
    parser.add_argument("--service-phase-fifo", type=Path)
    parser.add_argument("--release-fifo", type=Path)
    parser.add_argument("--require-database-gate", action="store_true")
    parser.add_argument("--acquire-timeout", type=float, default=30.0)
    parser.add_argument("--phase-timeout", type=float, default=60.0)
    parser.add_argument("--release-timeout", type=float, default=120.0)
    return parser


def _active_blockers(
    arguments: argparse.Namespace,
    *,
    database_connection: sqlite3.Connection | None = None,
) -> list[str]:
    scheduler_busy = _lock_is_busy(
        arguments.scheduler_lock,
        label="scheduler",
    )
    pipeline_busy = _lock_is_busy(
        arguments.pipeline_lock,
        label="pipeline",
    )
    operator_job_active = (
        _connection_has_active_operator_job(
            database_connection,
            user_id=arguments.user_id,
        )
        if database_connection is not None
        else _has_active_operator_job(
            arguments.database,
            user_id=arguments.user_id,
        )
    )
    blockers: list[str] = []
    if scheduler_busy:
        blockers.append("the nightly scheduler lock is busy")
    if pipeline_busy:
        blockers.append("the nightly pipeline lock is busy")
    if operator_job_active:
        blockers.append("a companion operator job is queued or running")
    return blockers


def _report_blockers(blockers: list[str]) -> None:
    for blocker in blockers:
        print(f"restart blocked: {blocker}", file=sys.stderr)


def _hold_restart_interlock(arguments: argparse.Namespace) -> int:
    if (
        arguments.ready_fifo is None
        or arguments.service_phase_fifo is None
        or arguments.release_fifo is None
    ):
        print(
            "restart safety check failed closed: hold mode requires private FIFOs",
            file=sys.stderr,
        )
        return ERROR_EXIT
    if (
        not (0.1 <= arguments.acquire_timeout <= 120.0)
        or not (1.0 <= arguments.phase_timeout <= 300.0)
        or not (1.0 <= arguments.release_timeout <= 300.0)
    ):
        print(
            "restart safety check failed closed: acquire timeout is invalid",
            file=sys.stderr,
        )
        return ERROR_EXIT
    ready_sent = False
    descriptor: int | None = None
    database_connection: sqlite3.Connection | None = None
    try:
        _validate_private_fifo(arguments.ready_fifo, label="ready")
        _validate_private_fifo(
            arguments.service_phase_fifo,
            label="service phase",
        )
        _validate_private_fifo(arguments.release_fifo, label="release")
        descriptor = _open_adapter_exclusive(
            arguments.adapter_lock,
            timeout_seconds=arguments.acquire_timeout,
        )
        database_connection = _open_database_write_gate(
            arguments.database,
            timeout_seconds=arguments.acquire_timeout,
            required=arguments.require_database_gate,
        )
        blockers = _active_blockers(
            arguments,
            database_connection=database_connection,
        )
        if blockers:
            _report_blockers(blockers)
            _write_ready_signal(arguments.ready_fifo, "blocked")
            return ACTIVE_EXIT
        _write_ready_signal(arguments.ready_fifo, "ready")
        ready_sent = True
        service_phase = _wait_for_signal(
            arguments.service_phase_fifo,
            label="service phase",
            timeout_seconds=arguments.phase_timeout,
        )
        if service_phase not in {"old-service-stopped", "abort"}:
            raise RestartSafetyError(
                "installer service-stop signal was missing or invalid"
            )
        if database_connection is not None:
            if service_phase == "old-service-stopped":
                database_connection.commit()
            else:
                database_connection.rollback()
            database_connection.close()
            database_connection = None
        _write_ready_signal(arguments.ready_fifo, "database-released")
        if service_phase == "abort":
            return ERROR_EXIT
        final_signal = _wait_for_signal(
            arguments.release_fifo,
            label="release",
            timeout_seconds=arguments.release_timeout,
        )
        if final_signal != "release":
            raise RestartSafetyError(
                "installer final release signal was missing or invalid"
            )
        return 0
    except RestartSafetyError as error:
        print(f"restart safety check failed closed: {error}", file=sys.stderr)
        if not ready_sent:
            try:
                _write_ready_signal(arguments.ready_fifo, "error")
            except RestartSafetyError:
                pass
        return ERROR_EXIT
    finally:
        if database_connection is not None:
            try:
                database_connection.rollback()
            except sqlite3.Error:
                pass
            database_connection.close()
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not _SAFE_USER_ID.fullmatch(arguments.user_id):
        print("restart safety check failed closed: user id is invalid", file=sys.stderr)
        return ERROR_EXIT
    if arguments.hold:
        return _hold_restart_interlock(arguments)
    try:
        adapter_busy = _lock_is_busy(
            arguments.adapter_lock,
            label="adapter mutation",
        )
        blockers = _active_blockers(arguments)
    except RestartSafetyError as error:
        print(f"restart safety check failed closed: {error}", file=sys.stderr)
        return ERROR_EXIT

    if adapter_busy:
        blockers.append("the adapter mutation lock is busy")
    if blockers:
        _report_blockers(blockers)
        return ACTIVE_EXIT

    print("restart safety check passed: no active nightly or operator job")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import fcntl
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-operator-restart-safety.py"
)


class RestartSafetyGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.scheduler_lock = self.root / "nightly_scheduler.lock"
        self.pipeline_lock = self.root / "nightly_pipeline.lock"
        self.adapter_lock = self.root / "operator_mutation.lock"
        self.database = self.root / "companion.sqlite3"
        self.scheduler_lock.write_text("private-scheduler-content", encoding="utf-8")
        self.pipeline_lock.write_text("private-pipeline-content", encoding="utf-8")
        self.adapter_lock.write_text("private-adapter-content", encoding="utf-8")
        self.scheduler_lock.chmod(0o600)
        self.pipeline_lock.chmod(0o600)
        self.adapter_lock.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_guard(self, *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--scheduler-lock",
                str(self.scheduler_lock),
                "--pipeline-lock",
                str(self.pipeline_lock),
                "--adapter-lock",
                str(self.adapter_lock),
                "--database",
                str(self.database),
                "--user-id",
                "guard-test",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def hold_command(
        self,
        ready_fifo: Path,
        service_phase_fifo: Path,
        release_fifo: Path,
        *,
        require_database_gate: bool = False,
    ) -> list[str]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--scheduler-lock",
            str(self.scheduler_lock),
            "--pipeline-lock",
            str(self.pipeline_lock),
            "--adapter-lock",
            str(self.adapter_lock),
            "--database",
            str(self.database),
            "--user-id",
            "guard-test",
            "--hold",
            "--ready-fifo",
            str(ready_fifo),
            "--service-phase-fifo",
            str(service_phase_fifo),
            "--release-fifo",
            str(release_fifo),
            "--acquire-timeout",
            "2",
            "--phase-timeout",
            "3",
            "--release-timeout",
            "3",
        ]
        if require_database_gate:
            command.append("--require-database-gate")
        return command

    def private_fifos(self) -> tuple[Path, Path, Path, int, int, int]:
        ready = self.root / "ready.fifo"
        service_phase = self.root / "service-phase.fifo"
        release = self.root / "release.fifo"
        os.mkfifo(ready, 0o600)
        os.mkfifo(service_phase, 0o600)
        os.mkfifo(release, 0o600)
        ready_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
        service_phase_fd = os.open(
            service_phase,
            os.O_RDWR | os.O_NONBLOCK,
        )
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        return (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        )

    @staticmethod
    def read_handshake(descriptor: int, timeout: float = 3.0) -> str:
        readable, _, _ = select.select([descriptor], [], [], timeout)
        if not readable:
            return ""
        return os.read(descriptor, 128).decode("utf-8").strip()

    def initialize_database(self, *statuses: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE operator_jobs (user_id TEXT NOT NULL, status TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO operator_jobs (user_id, status) VALUES (?, ?)",
                [("guard-test", status) for status in statuses],
            )
        self.database.chmod(0o600)

    def test_missing_database_and_terminal_jobs_are_restart_safe(self) -> None:
        result = self.run_guard()
        self.assertEqual(result.returncode, 0)
        self.assertIn("restart safety check passed", result.stdout)

        self.initialize_database("completed", "failed", "blocked")
        result = self.run_guard()
        self.assertEqual(result.returncode, 0)

    def test_each_active_operator_job_state_blocks_restart(self) -> None:
        for status in ("queued", "running"):
            with self.subTest(status=status):
                if self.database.exists():
                    self.database.unlink()
                self.initialize_database(status)
                result = self.run_guard()
                self.assertEqual(result.returncode, 75)
                self.assertIn("operator job is queued or running", result.stderr)
                self.assertNotIn("guard-test", result.stderr)

    def test_busy_advisory_locks_block_without_reading_their_contents(self) -> None:
        with self.scheduler_lock.open("r") as scheduler_handle:
            with self.pipeline_lock.open("r") as pipeline_handle:
                fcntl.flock(scheduler_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(pipeline_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                started = time.monotonic()
                result = self.run_guard()
                elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 75)
        self.assertLess(elapsed, 2.0)
        self.assertIn("scheduler lock is busy", result.stderr)
        self.assertIn("pipeline lock is busy", result.stderr)
        self.assertNotIn("private-scheduler-content", result.stderr)
        self.assertNotIn("private-pipeline-content", result.stderr)

        with self.adapter_lock.open("r+b") as adapter_handle:
            fcntl.flock(adapter_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_guard()
        self.assertEqual(result.returncode, 75)
        self.assertIn("adapter mutation lock is busy", result.stderr)
        self.assertNotIn("private-adapter-content", result.stderr)

    def test_hold_rechecks_scheduler_state_after_exclusive_acquisition(self) -> None:
        (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        ) = self.private_fifos()
        process: subprocess.Popen[str] | None = None
        try:
            with self.adapter_lock.open("r+b") as adapter_handle:
                fcntl.flock(adapter_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(
                    self.hold_command(ready, service_phase, release),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                with self.scheduler_lock.open("r+b") as scheduler_handle:
                    fcntl.flock(
                        scheduler_handle,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    fcntl.flock(adapter_handle, fcntl.LOCK_UN)
                    self.assertEqual(self.read_handshake(ready_fd), "blocked")
                    stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 75)
            self.assertEqual(stdout, "")
            self.assertIn("scheduler lock is busy", stderr)
        finally:
            os.close(ready_fd)
            os.close(service_phase_fd)
            os.close(release_fd)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

    def test_hold_keeps_adapter_exclusive_until_release_signal(self) -> None:
        (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        ) = self.private_fifos()
        holder = subprocess.Popen(
            self.hold_command(ready, service_phase, release),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        waiter: subprocess.Popen[str] | None = None
        try:
            self.assertEqual(self.read_handshake(ready_fd), "ready")
            waiter = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl,sys; "
                        "h=open(sys.argv[1],'r+b'); "
                        "fcntl.flock(h.fileno(),fcntl.LOCK_EX); "
                        "print('acquired',flush=True)"
                    ),
                    str(self.adapter_lock),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            readable, _, _ = select.select([waiter.stdout], [], [], 0.2)
            self.assertEqual(readable, [], "exclusive interlock released before signal")
            os.write(service_phase_fd, b"old-service-stopped\n")
            self.assertEqual(self.read_handshake(ready_fd), "database-released")
            readable, _, _ = select.select([waiter.stdout], [], [], 0.2)
            self.assertEqual(
                readable,
                [],
                "adapter interlock released with the legacy database gate",
            )
            os.write(release_fd, b"release\n")
            holder_stdout, holder_stderr = holder.communicate(timeout=3)
            self.assertEqual(holder.returncode, 0, holder_stderr)
            self.assertEqual(holder_stdout, "")
            waiter_stdout, waiter_stderr = waiter.communicate(timeout=3)
            self.assertEqual(waiter.returncode, 0, waiter_stderr)
            self.assertEqual(waiter_stdout.strip(), "acquired")
        finally:
            os.close(ready_fd)
            os.close(service_phase_fd)
            os.close(release_fd)
            for process in (holder, waiter):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()

    def test_hold_blocks_a_legacy_writer_until_old_service_stops(self) -> None:
        self.initialize_database("completed")
        (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        ) = self.private_fifos()
        holder = subprocess.Popen(
            self.hold_command(
                ready,
                service_phase,
                release,
                require_database_gate=True,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(self.read_handshake(ready_fd), "ready")
            legacy_writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sqlite3,sys; "
                        "c=sqlite3.connect(sys.argv[1],timeout=0.2); "
                        "\ntry:\n"
                        " c.execute(\"INSERT INTO operator_jobs "
                        "(user_id,status) VALUES ('guard-test','queued')\"); "
                        "c.commit(); print('inserted')\n"
                        "except sqlite3.OperationalError:\n print('blocked')"
                    ),
                    str(self.database),
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            self.assertEqual(legacy_writer.returncode, 0, legacy_writer.stderr)
            self.assertEqual(legacy_writer.stdout.strip(), "blocked")
            with sqlite3.connect(self.database) as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM operator_jobs WHERE status = 'queued'"
                ).fetchone()[0]
            self.assertEqual(active, 0)

            os.write(service_phase_fd, b"old-service-stopped\n")
            self.assertEqual(self.read_handshake(ready_fd), "database-released")
            with sqlite3.connect(self.database, timeout=0.2) as connection:
                connection.execute(
                    "INSERT INTO operator_jobs (user_id,status) VALUES (?,?)",
                    ("guard-test", "completed"),
                )
            os.write(release_fd, b"release\n")
            _, stderr = holder.communicate(timeout=3)
            self.assertEqual(holder.returncode, 0, stderr)
        finally:
            os.close(ready_fd)
            os.close(service_phase_fd)
            os.close(release_fd)
            if holder.poll() is None:
                holder.kill()
                holder.wait()

    def test_required_legacy_database_gate_fails_closed_when_missing(self) -> None:
        (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        ) = self.private_fifos()
        holder = subprocess.Popen(
            self.hold_command(
                ready,
                service_phase,
                release,
                require_database_gate=True,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(self.read_handshake(ready_fd), "error")
            _, stderr = holder.communicate(timeout=3)
            self.assertEqual(holder.returncode, 2)
            self.assertIn("writer quiescence is unavailable", stderr)
        finally:
            os.close(ready_fd)
            os.close(service_phase_fd)
            os.close(release_fd)
            if holder.poll() is None:
                holder.kill()
                holder.wait()

    def test_abort_rolls_back_database_gate_and_releases_adapter(self) -> None:
        self.initialize_database("completed")
        (
            ready,
            service_phase,
            release,
            ready_fd,
            service_phase_fd,
            release_fd,
        ) = self.private_fifos()
        holder = subprocess.Popen(
            self.hold_command(
                ready,
                service_phase,
                release,
                require_database_gate=True,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(self.read_handshake(ready_fd), "ready")
            os.write(service_phase_fd, b"abort\n")
            self.assertEqual(self.read_handshake(ready_fd), "database-released")
            _, stderr = holder.communicate(timeout=3)
            self.assertEqual(holder.returncode, 2, stderr)
            with self.adapter_lock.open("r+b") as adapter_handle:
                fcntl.flock(
                    adapter_handle,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            with sqlite3.connect(self.database, timeout=0.2) as connection:
                connection.execute(
                    "INSERT INTO operator_jobs (user_id,status) VALUES (?,?)",
                    ("guard-test", "completed"),
                )
        finally:
            os.close(ready_fd)
            os.close(service_phase_fd)
            os.close(release_fd)
            if holder.poll() is None:
                holder.kill()
                holder.wait()

    def test_unsafe_or_unreadable_state_fails_closed_and_bounded(self) -> None:
        self.database.write_bytes(b"not a sqlite database")
        self.database.chmod(0o600)
        started = time.monotonic()
        result = self.run_guard()
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 2)
        self.assertLess(elapsed, 2.0)
        self.assertIn("failed closed", result.stderr)
        self.assertNotIn("not a sqlite database", result.stderr)

        self.database.unlink()
        outside = self.root / "outside.lock"
        outside.write_text("private-outside-content", encoding="utf-8")
        self.scheduler_lock.unlink()
        self.scheduler_lock.symlink_to(outside)
        result = self.run_guard()
        self.assertEqual(result.returncode, 2)
        self.assertIn("scheduler lock is missing or unsafe", result.stderr)
        self.assertNotIn("private-outside-content", result.stderr)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Render the operator companion LaunchAgent without accepting secret fields."""

from __future__ import annotations

import argparse
import plistlib
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


MANAGED_BY = "Recruiting Engine Product operator companion installer"
ALLOWED_ENVIRONMENT = {
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "RECRUITING_ENGINE_MODE",
    "RECRUITING_ENGINE_ALLOW_REMOTE",
    "RECRUITING_ENGINE_ALLOW_LIVE_RUNS",
    "RECRUITING_ENGINE_RESUME_ROOT",
    "RECRUITING_ENGINE_OUTREACH_ROOT",
    "RECRUITING_ENGINE_RUNTIME_DIR",
    "RECRUITING_ENGINE_ATTESTATION_PATH",
    "RECRUITING_ENGINE_RESUME_PYTHON",
    "RECRUITING_ENGINE_OUTREACH_PYTHON",
    "RECRUITING_ENGINE_COMPANION_PYTHON",
    "RECRUITING_ENGINE_DATA_DIR",
    "RECRUITING_ENGINE_HOST",
    "RECRUITING_ENGINE_PORT",
    "RECRUITING_ENGINE_USER_ID",
    "RECRUITING_ENGINE_MAX_UPLOAD_BYTES",
    "RECRUITING_ENGINE_HOSTED_ORIGIN",
    "RECRUITING_ENGINE_SCHEDULER_LABEL",
}
FORBIDDEN_NAME = re.compile(r"(?:TOKEN|PASSWORD|SECRET|BEARER|COOKIE|SMTP)", re.I)
FORBIDDEN_VALUE = re.compile(r"(?:re_(?:pair|local|web)_[A-Za-z0-9_-]+|Bearer\s+\S+)", re.I)
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PATH_ENVIRONMENT = {
    "PYTHONPATH",
    "RECRUITING_ENGINE_RESUME_ROOT",
    "RECRUITING_ENGINE_OUTREACH_ROOT",
    "RECRUITING_ENGINE_RUNTIME_DIR",
    "RECRUITING_ENGINE_ATTESTATION_PATH",
    "RECRUITING_ENGINE_RESUME_PYTHON",
    "RECRUITING_ENGINE_OUTREACH_PYTHON",
    "RECRUITING_ENGINE_COMPANION_PYTHON",
    "RECRUITING_ENGINE_DATA_DIR",
}


def parse_environment(values: list[str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key:
            raise ValueError(f"environment value must be KEY=VALUE: {value!r}")
        if key not in ALLOWED_ENVIRONMENT or FORBIDDEN_NAME.search(key):
            raise ValueError(f"environment key is not in the non-secret allowlist: {key}")
        if "\n" in raw or "\r" in raw:
            raise ValueError(f"environment value contains a line break: {key}")
        if FORBIDDEN_VALUE.search(raw):
            raise ValueError(f"environment value looks like a credential: {key}")
        environment[key] = raw
    return environment


def require_absolute(label: str, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {value}")
    return str(path)


def validate_environment(environment: dict[str, str]) -> None:
    missing = sorted(ALLOWED_ENVIRONMENT - set(environment))
    if missing:
        raise ValueError(f"required environment keys are missing: {', '.join(missing)}")
    for key in sorted(PATH_ENVIRONMENT):
        require_absolute(key, environment[key])
    if environment.get("RECRUITING_ENGINE_MODE") != "existing":
        raise ValueError("LaunchAgent mode must be existing")
    if environment.get("RECRUITING_ENGINE_ALLOW_REMOTE") != "0":
        raise ValueError("remote binding must remain disabled")
    if environment.get("RECRUITING_ENGINE_ALLOW_LIVE_RUNS") != "0":
        raise ValueError("production execution must remain disabled")
    if environment.get("RECRUITING_ENGINE_HOST") not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("companion host must be loopback-only")
    origin = environment.get("RECRUITING_ENGINE_HOSTED_ORIGIN", "")
    if urlsplit(origin).scheme != "https":
        raise ValueError("hosted origin must use HTTPS")
    try:
        port = int(environment["RECRUITING_ENGINE_PORT"])
        max_upload = int(environment["RECRUITING_ENGINE_MAX_UPLOAD_BYTES"])
    except ValueError as error:
        raise ValueError("port and upload limit must be integers") from error
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if max_upload < 1:
        raise ValueError("upload limit must be positive")
    if not USER_ID.fullmatch(environment["RECRUITING_ENGINE_USER_ID"]):
        raise ValueError("user id contains unsupported characters")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--start-script", required=True)
    parser.add_argument("--log-directory", required=True)
    parser.add_argument("--env", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not LABEL.fullmatch(args.label):
        raise ValueError("LaunchAgent label contains unsupported characters")
    working_directory = require_absolute("working directory", args.working_directory)
    start_script = require_absolute("start script", args.start_script)
    log_directory = require_absolute("log directory", args.log_directory)
    environment = parse_environment(args.env)
    validate_environment(environment)

    payload = {
        "Label": args.label,
        "ProgramArguments": ["/bin/bash", start_script],
        "WorkingDirectory": working_directory,
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 15,
        "ProcessType": "Background",
        "StandardOutPath": str(Path(log_directory) / "operator-companion.out.log"),
        "StandardErrorPath": str(Path(log_directory) / "operator-companion.err.log"),
        "ManagedBy": MANAGED_BY,
    }
    plistlib.dump(payload, sys.stdout.buffer, fmt=plistlib.FMT_XML, sort_keys=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"operator companion plist: {error}", file=sys.stderr)
        raise SystemExit(2) from error

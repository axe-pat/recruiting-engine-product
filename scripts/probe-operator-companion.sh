#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

run_preflight=0
quiet=0

usage() {
  cat <<'EOF'
Usage: scripts/probe-operator-companion.sh [--production-preflight] [--quiet]

Checks the local operator-companion paths without creating data, tokens, logs,
or LaunchAgent files. --production-preflight additionally runs the upstream
zero-mutation attestation check.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --production-preflight) run_preflight=1 ;;
    --quiet) quiet=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

operator_resolve_config

failures=0

pass() {
  if [ "${quiet}" -eq 0 ]; then
    printf 'ok  %s\n' "$1"
  fi
}

fail() {
  printf 'ERR %s\n' "$1" >&2
  failures=$((failures + 1))
}

check_directory() {
  if [ -d "$2" ] && [ -r "$2" ]; then
    pass "$1: $2"
  else
    fail "$1 is missing or unreadable: $2"
  fi
}

check_file() {
  if [ -f "$2" ] && [ -r "$2" ]; then
    pass "$1: $2"
  else
    fail "$1 is missing or unreadable: $2"
  fi
}

check_python() {
  if [ ! -x "$2" ]; then
    fail "$1 is not executable: $2"
  elif ! operator_python_is_supported "$2"; then
    fail "$1 must be Python 3.11 or newer: $2"
  else
    pass "$1: $2"
  fi
}

if [ "$(uname -s)" = "Darwin" ]; then
  pass "macOS host"
else
  fail "the LaunchAgent setup requires macOS"
fi

check_directory "product companion package" "${OPERATOR_PRODUCT_ROOT}/companion/recruiting_companion"
check_file "companion entrypoint" "${OPERATOR_PRODUCT_ROOT}/companion/recruiting_companion/__main__.py"

check_directory "ResumeGenerator root" "${RECRUITING_ENGINE_RESUME_ROOT}"
check_file "nightly scheduler" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/scripts/nightly_prompt.py"
check_file "nightly pipeline" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/scripts/run_nightly_pipeline.py"
check_file "Daily Engine" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/scripts/run_daily_engine.py"
check_directory "run-evidence directory" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/source_validation"
check_file "application workbook" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/jobs.xlsx"
check_file "application workbook lock" "${RECRUITING_ENGINE_RESUME_ROOT}/discovery/.jobs.lock"

check_directory "Outreach root" "${RECRUITING_ENGINE_OUTREACH_ROOT}"
check_file "Outreach entrypoint" "${RECRUITING_ENGINE_OUTREACH_ROOT}/main.py"
check_directory "Outreach workspace" "${RECRUITING_ENGINE_OUTREACH_ROOT}/workspace"
for table in organizations opportunities contacts touchpoints sources; do
  check_file "Outreach ${table} table" "${RECRUITING_ENGINE_OUTREACH_ROOT}/workspace/${table}.csv"
done
check_directory "Outreach reports" "${RECRUITING_ENGINE_OUTREACH_ROOT}/workspace/reports"
check_file "invite reservation ledger" "${RECRUITING_ENGINE_OUTREACH_ROOT}/workspace/linkedin_invite_send_reservations.json"

check_directory "production runtime directory" "${RECRUITING_ENGINE_RUNTIME_DIR}"
check_file "scheduler lock" "${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_scheduler.lock"
check_file "pipeline lock" "${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_pipeline.lock"
check_file "production attestation" "${RECRUITING_ENGINE_ATTESTATION_PATH}"

check_python "companion Python" "${RECRUITING_ENGINE_COMPANION_PYTHON}"
check_python "ResumeGenerator Python" "${RECRUITING_ENGINE_RESUME_PYTHON}"
check_python "Outreach Python" "${RECRUITING_ENGINE_OUTREACH_PYTHON}"

if ! operator_is_loopback "${RECRUITING_ENGINE_HOST}"; then
  fail "companion bind is not loopback-only: ${RECRUITING_ENGINE_HOST}"
else
  pass "loopback bind: ${RECRUITING_ENGINE_HOST}:${RECRUITING_ENGINE_PORT}"
fi

if [ "${failures}" -eq 0 ] && [ "${run_preflight}" -eq 1 ]; then
  if [ "${quiet}" -eq 0 ]; then
    printf 'run production preflight (zero mutation)\n'
  fi
  if (
    cd "${RECRUITING_ENGINE_RESUME_ROOT}"
    "${RECRUITING_ENGINE_RESUME_PYTHON}" \
      discovery/scripts/nightly_prompt.py \
      --production-check-only \
      --production-attestation "${RECRUITING_ENGINE_ATTESTATION_PATH}"
  ); then
    pass "production attestation accepted"
  else
    fail "production preflight rejected the configured release"
  fi
fi

if [ "${failures}" -ne 0 ]; then
  printf 'Operator companion prerequisites failed: %s issue(s).\n' "${failures}" >&2
  exit 1
fi

if [ "${quiet}" -eq 0 ]; then
  printf 'Operator companion prerequisites are ready.\n'
fi

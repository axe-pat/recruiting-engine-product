#!/bin/bash

# Shared, non-secret configuration for the macOS operator companion scripts.
# This file is sourced; callers are responsible for enabling strict shell mode.

OPERATOR_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OPERATOR_PRODUCT_ROOT="$(CDPATH= cd -- "${OPERATOR_SCRIPT_DIR}/.." && pwd -P)"
OPERATOR_WORKSPACE_ROOT="$(CDPATH= cd -- "${OPERATOR_PRODUCT_ROOT}/.." && pwd -P)"

operator_die() {
  printf 'operator companion: %s\n' "$*" >&2
  return 1
}

operator_expand_home() {
  case "$1" in
    "~") printf '%s\n' "${HOME}" ;;
    "~/"*) printf '%s/%s\n' "${HOME}" "${1#\~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

operator_require_absolute() {
  case "$2" in
    /*) return 0 ;;
    *) operator_die "$1 must be an absolute path (received: $2)" ;;
  esac
}

operator_is_loopback() {
  case "$1" in
    127.0.0.1|localhost|::1) return 0 ;;
    *) return 1 ;;
  esac
}

operator_python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

operator_pick_companion_python() {
  if [ -n "${RECRUITING_ENGINE_COMPANION_PYTHON:-}" ]; then
    operator_expand_home "${RECRUITING_ENGINE_COMPANION_PYTHON}"
    return
  fi

  local candidate
  # Prefer an interpreter outside the Desktop checkouts. launchd can execute
  # this binary even when macOS protects scripts and virtualenvs on Desktop;
  # PYTHONPATH still points at the live product checkout.
  candidate="$(command -v python3 2>/dev/null || true)"
  if [ -n "${candidate}" ] && [ -x "${candidate}" ] && operator_python_is_supported "${candidate}"; then
    printf '%s\n' "${candidate}"
    return
  fi

  for candidate in \
    "${RECRUITING_ENGINE_RESUME_ROOT}/venv/bin/python" \
    "${RECRUITING_ENGINE_OUTREACH_ROOT}/.venv/bin/python"
  do
    if [ -x "${candidate}" ] && operator_python_is_supported "${candidate}"; then
      printf '%s\n' "${candidate}"
      return
    fi
  done

  printf '%s\n' "python3"
}

operator_resolve_config() {
  local resume_default="${OPERATOR_WORKSPACE_ROOT}/ResumeGenerator v1"
  local outreach_default="${OPERATOR_WORKSPACE_ROOT}/Outreach"

  RECRUITING_ENGINE_RESUME_ROOT="$(operator_expand_home "${RECRUITING_ENGINE_RESUME_ROOT:-${RESUMEGEN_ROOT:-${resume_default}}}")"
  RECRUITING_ENGINE_OUTREACH_ROOT="$(operator_expand_home "${RECRUITING_ENGINE_OUTREACH_ROOT:-${OUTREACH_ROOT:-${outreach_default}}}")"
  RECRUITING_ENGINE_RUNTIME_DIR="$(operator_expand_home "${RECRUITING_ENGINE_RUNTIME_DIR:-${HOME}/Library/Application Support/ResumeGenerator}")"
  RECRUITING_ENGINE_ATTESTATION_PATH="$(operator_expand_home "${RECRUITING_ENGINE_ATTESTATION_PATH:-${RECRUITING_ENGINE_RUNTIME_DIR}/production_release.json}")"
  RECRUITING_ENGINE_RESUME_PYTHON="$(operator_expand_home "${RECRUITING_ENGINE_RESUME_PYTHON:-${RECRUITING_ENGINE_RESUME_ROOT}/venv/bin/python}")"
  RECRUITING_ENGINE_OUTREACH_PYTHON="$(operator_expand_home "${RECRUITING_ENGINE_OUTREACH_PYTHON:-${RECRUITING_ENGINE_OUTREACH_ROOT}/.venv/bin/python}")"
  RECRUITING_ENGINE_DATA_DIR="$(operator_expand_home "${RECRUITING_ENGINE_DATA_DIR:-${HOME}/.recruiting-engine-companion}")"
  RECRUITING_ENGINE_OPERATOR_LOG_DIR="$(operator_expand_home "${RECRUITING_ENGINE_OPERATOR_LOG_DIR:-${HOME}/Library/Logs/RecruitingEngine}")"

  operator_require_absolute RECRUITING_ENGINE_RESUME_ROOT "${RECRUITING_ENGINE_RESUME_ROOT}"
  operator_require_absolute RECRUITING_ENGINE_OUTREACH_ROOT "${RECRUITING_ENGINE_OUTREACH_ROOT}"
  operator_require_absolute RECRUITING_ENGINE_RUNTIME_DIR "${RECRUITING_ENGINE_RUNTIME_DIR}"
  operator_require_absolute RECRUITING_ENGINE_ATTESTATION_PATH "${RECRUITING_ENGINE_ATTESTATION_PATH}"
  operator_require_absolute RECRUITING_ENGINE_RESUME_PYTHON "${RECRUITING_ENGINE_RESUME_PYTHON}"
  operator_require_absolute RECRUITING_ENGINE_OUTREACH_PYTHON "${RECRUITING_ENGINE_OUTREACH_PYTHON}"
  operator_require_absolute RECRUITING_ENGINE_DATA_DIR "${RECRUITING_ENGINE_DATA_DIR}"
  operator_require_absolute RECRUITING_ENGINE_OPERATOR_LOG_DIR "${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"

  RECRUITING_ENGINE_COMPANION_PYTHON="$(operator_pick_companion_python)"
  RECRUITING_ENGINE_COMPANION_PYTHON="$(operator_expand_home "${RECRUITING_ENGINE_COMPANION_PYTHON}")"
  operator_require_absolute RECRUITING_ENGINE_COMPANION_PYTHON "${RECRUITING_ENGINE_COMPANION_PYTHON}"

  RECRUITING_ENGINE_HOST="${RECRUITING_ENGINE_HOST:-127.0.0.1}"
  if ! operator_is_loopback "${RECRUITING_ENGINE_HOST}"; then
    operator_die "RECRUITING_ENGINE_HOST must be loopback-only"
    return 1
  fi
  RECRUITING_ENGINE_PORT="${RECRUITING_ENGINE_PORT:-8765}"
  case "${RECRUITING_ENGINE_PORT}" in
    ''|*[!0-9]*) operator_die "RECRUITING_ENGINE_PORT must be an integer"; return 1 ;;
  esac
  if [ "${RECRUITING_ENGINE_PORT}" -lt 1 ] || [ "${RECRUITING_ENGINE_PORT}" -gt 65535 ]; then
    operator_die "RECRUITING_ENGINE_PORT must be between 1 and 65535"
    return 1
  fi

  RECRUITING_ENGINE_USER_ID="${RECRUITING_ENGINE_USER_ID:-default}"
  RECRUITING_ENGINE_MAX_UPLOAD_BYTES="${RECRUITING_ENGINE_MAX_UPLOAD_BYTES:-10485760}"
  RECRUITING_ENGINE_HOSTED_ORIGIN="${RECRUITING_ENGINE_HOSTED_ORIGIN:-https://axe-pat.github.io}"
  RECRUITING_ENGINE_SCHEDULER_LABEL="${RECRUITING_ENGINE_SCHEDULER_LABEL:-com.akshat.resumegenerator.nightly}"
  RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL="${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL:-com.axepat.recruitingengine.operator-companion}"
  case "${RECRUITING_ENGINE_USER_ID}" in
    ''|*[!A-Za-z0-9_-]*) operator_die "RECRUITING_ENGINE_USER_ID contains unsupported characters"; return 1 ;;
  esac
  case "${RECRUITING_ENGINE_USER_ID}" in
    [A-Za-z0-9]*) ;;
    *) operator_die "RECRUITING_ENGINE_USER_ID must start with a letter or number"; return 1 ;;
  esac
  if [ "${#RECRUITING_ENGINE_USER_ID}" -gt 64 ]; then
    operator_die "RECRUITING_ENGINE_USER_ID must not exceed 64 characters"
    return 1
  fi
  case "${RECRUITING_ENGINE_MAX_UPLOAD_BYTES}" in
    ''|*[!0-9]*) operator_die "RECRUITING_ENGINE_MAX_UPLOAD_BYTES must be a positive integer"; return 1 ;;
  esac
  if [ "${RECRUITING_ENGINE_MAX_UPLOAD_BYTES}" -lt 1 ]; then
    operator_die "RECRUITING_ENGINE_MAX_UPLOAD_BYTES must be positive"
    return 1
  fi
  case "${RECRUITING_ENGINE_HOSTED_ORIGIN}" in
    https://*) ;;
    *) operator_die "RECRUITING_ENGINE_HOSTED_ORIGIN must use HTTPS"; return 1 ;;
  esac
  case "${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL}" in
    ''|*[!A-Za-z0-9._-]*) operator_die "RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL contains unsupported characters"; return 1 ;;
  esac

  # Operator mode is intentionally read-only with respect to production execution.
  RECRUITING_ENGINE_MODE="existing"
  RECRUITING_ENGINE_ALLOW_REMOTE="0"
  RECRUITING_ENGINE_ALLOW_LIVE_RUNS="0"
  PYTHONPATH="${OPERATOR_PRODUCT_ROOT}/companion"
  PYTHONUNBUFFERED="1"

  export \
    PYTHONPATH \
    PYTHONUNBUFFERED \
    RECRUITING_ENGINE_MODE \
    RECRUITING_ENGINE_ALLOW_REMOTE \
    RECRUITING_ENGINE_ALLOW_LIVE_RUNS \
    RECRUITING_ENGINE_RESUME_ROOT \
    RECRUITING_ENGINE_OUTREACH_ROOT \
    RECRUITING_ENGINE_RUNTIME_DIR \
    RECRUITING_ENGINE_ATTESTATION_PATH \
    RECRUITING_ENGINE_RESUME_PYTHON \
    RECRUITING_ENGINE_OUTREACH_PYTHON \
    RECRUITING_ENGINE_COMPANION_PYTHON \
    RECRUITING_ENGINE_DATA_DIR \
    RECRUITING_ENGINE_HOST \
    RECRUITING_ENGINE_PORT \
    RECRUITING_ENGINE_USER_ID \
    RECRUITING_ENGINE_MAX_UPLOAD_BYTES \
    RECRUITING_ENGINE_HOSTED_ORIGIN \
    RECRUITING_ENGINE_SCHEDULER_LABEL \
    RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL \
    RECRUITING_ENGINE_OPERATOR_LOG_DIR
}

operator_print_config() {
  printf '%s\n' \
    "Mode: existing (production execution disabled)" \
    "Bind: ${RECRUITING_ENGINE_HOST}:${RECRUITING_ENGINE_PORT}" \
    "Product root: ${OPERATOR_PRODUCT_ROOT}" \
    "Resume engine: ${RECRUITING_ENGINE_RESUME_ROOT}" \
    "Outreach engine: ${RECRUITING_ENGINE_OUTREACH_ROOT}" \
    "Runtime locks: ${RECRUITING_ENGINE_RUNTIME_DIR}" \
    "Production attestation: ${RECRUITING_ENGINE_ATTESTATION_PATH}" \
    "Companion data: ${RECRUITING_ENGINE_DATA_DIR}" \
    "Companion Python: ${RECRUITING_ENGINE_COMPANION_PYTHON}" \
    "LaunchAgent label: ${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL}" \
    "Logs: ${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"
}

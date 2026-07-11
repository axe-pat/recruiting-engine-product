#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

dry_run=0

usage() {
  cat <<'EOF'
Usage: scripts/uninstall-operator-companion-launch-agent.sh [--dry-run]

Stops and removes only the managed LaunchAgent plist. Companion data, pairing
state, and logs are retained so reinstalling is reversible.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

operator_resolve_config
label="${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL}"
case "${label}" in
  ''|*[!A-Za-z0-9._-]*) printf 'Invalid LaunchAgent label: %s\n' "${label}" >&2; exit 2 ;;
esac

plist_path="${HOME}/Library/LaunchAgents/${label}.plist"
service="gui/$(id -u)/${label}"
managed_by="Recruiting Engine Product operator companion installer"
is_loaded=0
if /bin/launchctl print "${service}" >/dev/null 2>&1; then
  is_loaded=1
fi

if [ -f "${plist_path}" ]; then
  existing_manager="$(/usr/bin/plutil -extract ManagedBy raw -o - "${plist_path}" 2>/dev/null || true)"
  if [ "${existing_manager}" != "${managed_by}" ]; then
    printf 'Refusing to remove an unmanaged LaunchAgent: %s\n' "${plist_path}" >&2
    exit 1
  fi
elif [ "${is_loaded}" -eq 1 ]; then
  printf 'Refusing to stop a loaded service without its managed plist: %s\n' "${service}" >&2
  exit 1
fi

if [ "${dry_run}" -eq 1 ]; then
  printf '%s\n' \
    "Would stop service if loaded: ${service}" \
    "Would remove managed plist if present: ${plist_path}" \
    "Would retain companion data: ${RECRUITING_ENGINE_DATA_DIR}" \
    "Would retain logs: ${RECRUITING_ENGINE_OPERATOR_LOG_DIR}" \
    "Dry run made no filesystem or launchctl changes."
  exit 0
fi

if [ "${is_loaded}" -eq 1 ]; then
  /bin/launchctl bootout "${service}"
fi
if [ -f "${plist_path}" ]; then
  rm "${plist_path}"
fi

printf '%s\n' \
  "Removed operator companion LaunchAgent: ${service}" \
  "Retained companion data: ${RECRUITING_ENGINE_DATA_DIR}" \
  "Retained logs: ${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"

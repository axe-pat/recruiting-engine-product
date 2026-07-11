#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

dry_run=0
run_preflight=0

usage() {
  cat <<'EOF'
Usage: scripts/install-operator-companion-launch-agent.sh
       [--dry-run] [--production-preflight]

Installs and starts a per-user macOS LaunchAgent. --dry-run validates the exact
plist without creating directories, writing files, or calling launchctl.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --production-preflight) run_preflight=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ "$(uname -s)" != "Darwin" ]; then
  printf 'The operator companion LaunchAgent requires macOS.\n' >&2
  exit 1
fi

operator_resolve_config

probe_args=(--quiet)
if [ "${run_preflight}" -eq 1 ]; then
  probe_args+=(--production-preflight)
fi
"${SCRIPT_DIR}/probe-operator-companion.sh" "${probe_args[@]}"

label="${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL}"
case "${label}" in
  ''|*[!A-Za-z0-9._-]*) printf 'Invalid LaunchAgent label: %s\n' "${label}" >&2; exit 2 ;;
esac

launch_agents_dir="${HOME}/Library/LaunchAgents"
plist_path="${launch_agents_dir}/${label}.plist"
domain="gui/$(id -u)"
service="${domain}/${label}"
renderer="${SCRIPT_DIR}/render-operator-launch-agent.py"
start_script="${SCRIPT_DIR}/start-operator-companion.sh"
managed_by="Recruiting Engine Product operator companion installer"

was_loaded=0
if /bin/launchctl print "${service}" >/dev/null 2>&1; then
  was_loaded=1
fi
if [ -f "${plist_path}" ]; then
  existing_manager="$(/usr/bin/plutil -extract ManagedBy raw -o - "${plist_path}" 2>/dev/null || true)"
  if [ "${existing_manager}" != "${managed_by}" ]; then
    printf 'Refusing to replace an unmanaged LaunchAgent: %s\n' "${plist_path}" >&2
    exit 1
  fi
elif [ "${was_loaded}" -eq 1 ]; then
  printf 'Refusing to replace a loaded service without its managed plist: %s\n' "${service}" >&2
  exit 1
fi

render_command=(
  "${RECRUITING_ENGINE_COMPANION_PYTHON}" "${renderer}"
  --label "${label}"
  --working-directory "${OPERATOR_PRODUCT_ROOT}"
  --start-script "${start_script}"
  --log-directory "${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"
  --env "PYTHONPATH=${PYTHONPATH}"
  --env "PYTHONUNBUFFERED=${PYTHONUNBUFFERED}"
  --env "RECRUITING_ENGINE_MODE=${RECRUITING_ENGINE_MODE}"
  --env "RECRUITING_ENGINE_ALLOW_REMOTE=${RECRUITING_ENGINE_ALLOW_REMOTE}"
  --env "RECRUITING_ENGINE_ALLOW_LIVE_RUNS=${RECRUITING_ENGINE_ALLOW_LIVE_RUNS}"
  --env "RECRUITING_ENGINE_RESUME_ROOT=${RECRUITING_ENGINE_RESUME_ROOT}"
  --env "RECRUITING_ENGINE_OUTREACH_ROOT=${RECRUITING_ENGINE_OUTREACH_ROOT}"
  --env "RECRUITING_ENGINE_RUNTIME_DIR=${RECRUITING_ENGINE_RUNTIME_DIR}"
  --env "RECRUITING_ENGINE_ATTESTATION_PATH=${RECRUITING_ENGINE_ATTESTATION_PATH}"
  --env "RECRUITING_ENGINE_RESUME_PYTHON=${RECRUITING_ENGINE_RESUME_PYTHON}"
  --env "RECRUITING_ENGINE_OUTREACH_PYTHON=${RECRUITING_ENGINE_OUTREACH_PYTHON}"
  --env "RECRUITING_ENGINE_COMPANION_PYTHON=${RECRUITING_ENGINE_COMPANION_PYTHON}"
  --env "RECRUITING_ENGINE_DATA_DIR=${RECRUITING_ENGINE_DATA_DIR}"
  --env "RECRUITING_ENGINE_HOST=${RECRUITING_ENGINE_HOST}"
  --env "RECRUITING_ENGINE_PORT=${RECRUITING_ENGINE_PORT}"
  --env "RECRUITING_ENGINE_USER_ID=${RECRUITING_ENGINE_USER_ID}"
  --env "RECRUITING_ENGINE_MAX_UPLOAD_BYTES=${RECRUITING_ENGINE_MAX_UPLOAD_BYTES}"
  --env "RECRUITING_ENGINE_HOSTED_ORIGIN=${RECRUITING_ENGINE_HOSTED_ORIGIN}"
  --env "RECRUITING_ENGINE_SCHEDULER_LABEL=${RECRUITING_ENGINE_SCHEDULER_LABEL}"
)

if [ "${dry_run}" -eq 1 ]; then
  plist_xml="$("${render_command[@]}")"
  printf '%s\n' "${plist_xml}" | /usr/bin/plutil -lint - >/dev/null
  operator_print_config
  printf '%s\n' \
    "LaunchAgent plist validated: ${plist_path}" \
    "Service would be loaded as: ${service}" \
    "Dry run made no filesystem or launchctl changes."
  exit 0
fi

mkdir -p "${launch_agents_dir}" "${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"

temporary_plist="$(mktemp "${launch_agents_dir}/.${label}.plist.XXXXXX")"
backup_plist=""
cleanup() {
  if [ -n "${temporary_plist}" ]; then
    rm -f "${temporary_plist}"
  fi
  if [ -n "${backup_plist}" ]; then
    rm -f "${backup_plist}"
  fi
}
trap cleanup EXIT

"${render_command[@]}" >"${temporary_plist}"
/usr/bin/plutil -lint "${temporary_plist}" >/dev/null
chmod 600 "${temporary_plist}"

if [ -f "${plist_path}" ]; then
  backup_plist="${plist_path}.backup.$$"
  cp -p "${plist_path}" "${backup_plist}"
fi
if [ "${was_loaded}" -eq 1 ]; then
  /bin/launchctl bootout "${service}"
fi

mv "${temporary_plist}" "${plist_path}"
temporary_plist=""

if ! /bin/launchctl bootstrap "${domain}" "${plist_path}"; then
  rm -f "${plist_path}"
  if [ -n "${backup_plist}" ] && [ -f "${backup_plist}" ]; then
    mv "${backup_plist}" "${plist_path}"
    backup_plist=""
    if [ "${was_loaded}" -eq 1 ]; then
      /bin/launchctl bootstrap "${domain}" "${plist_path}" || true
    fi
  fi
  printf 'LaunchAgent bootstrap failed; the previous managed plist was restored when available.\n' >&2
  exit 1
fi

/bin/launchctl enable "${service}"
/bin/launchctl kickstart -k "${service}"

if [ -n "${backup_plist}" ]; then
  rm -f "${backup_plist}"
  backup_plist=""
fi

printf '%s\n' \
  "Installed operator companion: ${plist_path}" \
  "Service: ${service}" \
  "Logs: ${RECRUITING_ENGINE_OPERATOR_LOG_DIR}" \
  "Companion data was preserved at: ${RECRUITING_ENGINE_DATA_DIR}"

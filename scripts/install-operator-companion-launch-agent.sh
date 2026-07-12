#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

dry_run=0
run_preflight=0
force_restart_active=0

usage() {
  cat <<'EOF'
Usage: scripts/install-operator-companion-launch-agent.sh
       [--dry-run] [--production-preflight] [--force-restart-active]

Installs and starts a per-user macOS LaunchAgent. --dry-run validates the exact
plist without creating directories, writing files, or calling launchctl.
Restart is refused while nightly locks or operator jobs are active unless the
explicit --force-restart-active override is supplied.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --production-preflight) run_preflight=1 ;;
    --force-restart-active) force_restart_active=1 ;;
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

static_live_root="${OPERATOR_PRODUCT_ROOT}/static-export"
static_staged_root="${OPERATOR_PRODUCT_ROOT}/static-export.staged"
staged_static_available=0
if [ -d "${static_staged_root}" ] && [ ! -L "${static_staged_root}" ]; then
  staged_static_available=1
fi

label="${RECRUITING_ENGINE_OPERATOR_LAUNCH_LABEL}"
case "${label}" in
  ''|*[!A-Za-z0-9._-]*) printf 'Invalid LaunchAgent label: %s\n' "${label}" >&2; exit 2 ;;
esac

launch_agents_dir="${HOME}/Library/LaunchAgents"
plist_path="${launch_agents_dir}/${label}.plist"
domain="gui/$(id -u)"
service="${domain}/${label}"
renderer="${SCRIPT_DIR}/render-operator-launch-agent.py"
restart_guard="${SCRIPT_DIR}/check-operator-restart-safety.py"
managed_by="Recruiting Engine Product operator companion installer"
companion_database="${RECRUITING_ENGINE_DATA_DIR}/users/${RECRUITING_ENGINE_USER_ID}/companion.sqlite3"

restart_guard_command=(
  "${RECRUITING_ENGINE_COMPANION_PYTHON}" "${restart_guard}"
  --scheduler-lock "${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_scheduler.lock"
  --pipeline-lock "${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_pipeline.lock"
  --adapter-lock "${RECRUITING_ENGINE_RUNTIME_DIR}/operator_mutation.lock"
  --database "${companion_database}"
  --user-id "${RECRUITING_ENGINE_USER_ID}"
)

restart_interlock_dir=""
restart_interlock_pid=""
restart_interlock_ready_fd_open=0
restart_interlock_release_fd_open=0
restart_interlock_service_phase_fd_open=0
restart_interlock_database_released=0
old_service_stopped=0
static_backup_root=""
static_promoted=0
static_rejected_root=""

validate_static_root() {
  PYTHONPATH="${OPERATOR_PRODUCT_ROOT}/companion" \
    "${RECRUITING_ENGINE_COMPANION_PYTHON}" -c \
    'from pathlib import Path; from recruiting_companion.api import _validated_static_root; _validated_static_root(Path(__import__("sys").argv[1]))' \
    "$1" >/dev/null 2>&1
}

validate_legacy_static_root() {
  PYTHONPATH="${OPERATOR_PRODUCT_ROOT}/companion" \
    "${RECRUITING_ENGINE_COMPANION_PYTHON}" -c \
    'from pathlib import Path; from recruiting_companion.api import _validated_legacy_static_root; _validated_legacy_static_root(Path(__import__("sys").argv[1]))' \
    "$1" >/dev/null 2>&1
}

seal_legacy_static_root() {
  PYTHONPATH="${OPERATOR_PRODUCT_ROOT}/companion" \
    "${RECRUITING_ENGINE_COMPANION_PYTHON}" -c \
    'from pathlib import Path; from recruiting_companion.api import _seal_legacy_static_root; _seal_legacy_static_root(Path(__import__("sys").argv[1]))' \
    "$1" >/dev/null 2>&1
}

rollback_static_promotion() {
  if [ "${static_promoted}" -eq 0 ]; then
    return 0
  fi
  if [ -e "${static_live_root}" ] || [ -L "${static_live_root}" ]; then
    if [ ! -e "${static_staged_root}" ] && [ ! -L "${static_staged_root}" ]; then
      if ! mv "${static_live_root}" "${static_staged_root}"; then
        return 1
      fi
    else
      static_rejected_root="${OPERATOR_PRODUCT_ROOT}/.static-export.rejected.$$"
      if ! mv "${static_live_root}" "${static_rejected_root}"; then
        return 1
      fi
    fi
  fi
  if [ -n "${static_backup_root}" ]; then
    if ! mv "${static_backup_root}" "${static_live_root}"; then
      return 1
    fi
    static_backup_root=""
  fi
  static_promoted=0
}

promote_staged_static() {
  local live_requires_seal=0
  if [ "${staged_static_available}" -eq 0 ]; then
    return 0
  fi
  if [ ! -d "${static_staged_root}" ] || [ -L "${static_staged_root}" ]; then
    printf 'The validated staged static export changed before promotion.\n' >&2
    return 1
  fi
  if ! validate_static_root "${static_staged_root}"; then
    printf 'The staged static export failed its final integrity check.\n' >&2
    return 1
  fi
  static_backup_root="${OPERATOR_PRODUCT_ROOT}/.static-export.rollback.$$"
  if [ -e "${static_backup_root}" ] || [ -L "${static_backup_root}" ]; then
    printf 'Static export rollback path already exists: %s\n' \
      "${static_backup_root}" >&2
    return 1
  fi
  if [ -e "${static_live_root}" ] || [ -L "${static_live_root}" ]; then
    if [ ! -d "${static_live_root}" ] || [ -L "${static_live_root}" ]; then
      printf 'The active static export is unsafe to retain for rollback.\n' >&2
      static_backup_root=""
      return 1
    fi
    if ! validate_static_root "${static_live_root}"; then
      if validate_legacy_static_root "${static_live_root}"; then
        live_requires_seal=1
      else
        printf 'The active static export is unsafe to retain for rollback.\n' >&2
        static_backup_root=""
        return 1
      fi
    fi
    if ! mv "${static_live_root}" "${static_backup_root}"; then
      static_backup_root=""
      return 1
    fi
    if [ "${live_requires_seal}" -eq 1 ] \
      && ! seal_legacy_static_root "${static_backup_root}"; then
      mv "${static_backup_root}" "${static_live_root}" || true
      static_backup_root=""
      printf 'The legacy static export could not be sealed for rollback.\n' >&2
      return 1
    fi
  else
    static_backup_root=""
  fi
  if ! mv "${static_staged_root}" "${static_live_root}"; then
    if [ -n "${static_backup_root}" ]; then
      mv "${static_backup_root}" "${static_live_root}" || true
      static_backup_root=""
    fi
    return 1
  fi
  static_promoted=1
  if ! validate_static_root "${static_live_root}"; then
    printf 'Promoted static export failed closed; restoring the previous generation.\n' >&2
    rollback_static_promotion || true
    return 1
  fi
}

finalize_static_promotion() {
  if [ -n "${static_backup_root}" ]; then
    rm -rf "${static_backup_root}"
    static_backup_root=""
  fi
  if [ -n "${static_rejected_root}" ]; then
    rm -rf "${static_rejected_root}"
    static_rejected_root=""
  fi
  static_promoted=0
}

check_restart_safety() {
  if ! "${restart_guard_command[@]}"; then
    printf '%s\n' \
      "Refusing to restart the operator companion while active work may be abandoned." \
      "Wait for the nightly/operator job to finish, or rerun with --force-restart-active only after explicit review." >&2
    return 1
  fi
}

release_restart_interlock() {
  local helper_status=0
  if [ "${restart_interlock_service_phase_fd_open}" -eq 1 ]; then
    if [ "${restart_interlock_database_released}" -eq 0 ]; then
      printf 'abort\n' >&10 || true
    fi
    exec 10>&-
    restart_interlock_service_phase_fd_open=0
  fi
  if [ "${restart_interlock_release_fd_open}" -eq 1 ]; then
    printf 'release\n' >&9 || true
    exec 9>&-
    restart_interlock_release_fd_open=0
  fi
  if [ -n "${restart_interlock_pid}" ]; then
    if wait "${restart_interlock_pid}"; then
      helper_status=0
    else
      helper_status=$?
    fi
    restart_interlock_pid=""
  fi
  if [ "${restart_interlock_ready_fd_open}" -eq 1 ]; then
    exec 8>&-
    restart_interlock_ready_fd_open=0
  fi
  if [ -n "${restart_interlock_dir}" ]; then
    rm -rf "${restart_interlock_dir}"
    restart_interlock_dir=""
  fi
  return "${helper_status}"
}

release_legacy_database_gate() {
  local handshake=""
  if [ "${restart_interlock_database_released}" -eq 1 ]; then
    return 0
  fi
  if [ "${restart_interlock_service_phase_fd_open}" -ne 1 ]; then
    printf 'Restart interlock service phase is unavailable.\n' >&2
    return 1
  fi
  printf 'old-service-stopped\n' >&10
  exec 10>&-
  restart_interlock_service_phase_fd_open=0
  if ! IFS= read -r -t 35 handshake <&8; then
    printf 'Legacy database writer gate was not released within the bounded timeout.\n' >&2
    return 1
  fi
  if [ "${handshake}" != "database-released" ]; then
    printf 'Restart interlock failed closed while releasing the legacy database writer gate.\n' >&2
    return 1
  fi
  restart_interlock_database_released=1
}

start_restart_interlock() {
  local ready_fifo service_phase_fifo release_fifo handshake
  local -a hold_command
  restart_interlock_dir="$(
    mktemp -d "${TMPDIR:-/tmp}/recruiting-engine-restart.XXXXXX"
  )"
  chmod 700 "${restart_interlock_dir}"
  ready_fifo="${restart_interlock_dir}/ready.fifo"
  service_phase_fifo="${restart_interlock_dir}/service-phase.fifo"
  release_fifo="${restart_interlock_dir}/release.fifo"
  mkfifo "${ready_fifo}" "${service_phase_fifo}" "${release_fifo}"
  chmod 600 "${ready_fifo}" "${service_phase_fifo}" "${release_fifo}"
  exec 8<>"${ready_fifo}"
  restart_interlock_ready_fd_open=1
  exec 9<>"${release_fifo}"
  restart_interlock_release_fd_open=1
  exec 10<>"${service_phase_fifo}"
  restart_interlock_service_phase_fd_open=1

  hold_command=(
    "${restart_guard_command[@]}"
    --hold
    --ready-fifo "${ready_fifo}"
    --service-phase-fifo "${service_phase_fifo}"
    --release-fifo "${release_fifo}"
    --acquire-timeout 30
    --phase-timeout 45
    --release-timeout 120
  )
  if [ "${was_loaded}" -eq 1 ]; then
    hold_command+=(--require-database-gate)
  fi
  "${hold_command[@]}" 8>&- 9>&- 10>&- &
  restart_interlock_pid=$!
  handshake=""
  if ! IFS= read -r -t 35 handshake <&8; then
    printf 'Restart interlock did not become ready within the bounded timeout.\n' >&2
    release_restart_interlock || true
    return 1
  fi
  if [ "${handshake}" != "ready" ]; then
    printf 'Restart interlock failed closed before service shutdown.\n' >&2
    release_restart_interlock || true
    return 1
  fi
}

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
  --working-directory "${HOME}"
  --python "${RECRUITING_ENGINE_COMPANION_PYTHON}"
  --log-directory "${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"
  --env "PYTHONPATH=${PYTHONPATH}"
  --env "PYTHONUNBUFFERED=${PYTHONUNBUFFERED}"
  --env "RECRUITING_ENGINE_MODE=${RECRUITING_ENGINE_MODE}"
  --env "RECRUITING_ENGINE_ALLOW_REMOTE=${RECRUITING_ENGINE_ALLOW_REMOTE}"
  --env "RECRUITING_ENGINE_ALLOW_LIVE_RUNS=${RECRUITING_ENGINE_ALLOW_LIVE_RUNS}"
  --env "RECRUITING_ENGINE_ALLOW_REVIEWED_ACTIONS=${RECRUITING_ENGINE_ALLOW_REVIEWED_ACTIONS}"
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
  restart_guard_description="enforced"
  if [ "${force_restart_active}" -eq 1 ]; then
    restart_guard_description="explicitly bypassed"
  fi
  operator_print_config
  printf '%s\n' \
    "LaunchAgent plist validated: ${plist_path}" \
    "Service would be loaded as: ${service}" \
    "Active-work restart guard would be ${restart_guard_description}." \
    "Dry run made no filesystem or launchctl changes."
  exit 0
fi

if [ "${force_restart_active}" -eq 1 ]; then
  printf 'WARNING: explicit --force-restart-active override enabled.\n' >&2
else
  check_restart_safety
fi

mkdir -p "${launch_agents_dir}" "${RECRUITING_ENGINE_OPERATOR_LOG_DIR}"

temporary_plist="$(mktemp "${launch_agents_dir}/.${label}.plist.XXXXXX")"
backup_plist=""
replacement_started=0
install_succeeded=0
cleanup() {
  local static_rollback_ready=1
  local static_rollback_permitted=1
  # A successful bootout must be acknowledged before an old plist can be
  # restored. If bootout never completed, aborting the helper preserves the
  # still-running service and rolls back the SQLite writer transaction.
  if [ "${was_loaded}" -eq 1 ] && [ "${old_service_stopped}" -eq 0 ] \
    && ! /bin/launchctl print "${service}" >/dev/null 2>&1; then
    old_service_stopped=1
  fi
  if [ -n "${restart_interlock_pid}" ] && [ "${restart_interlock_database_released}" -eq 0 ]; then
    if [ "${old_service_stopped}" -eq 1 ]; then
      if ! release_legacy_database_gate; then
        # Do not attempt to restore a service while the helper may still own
        # SQLite's writer slot. Its bounded wait plus final release guarantees
        # both gates are gone before rollback bootstrap proceeds.
        release_restart_interlock || true
      fi
    else
      release_restart_interlock || true
    fi
  fi
  if [ "${replacement_started}" -eq 1 ] && [ "${install_succeeded}" -eq 0 ]; then
    if /bin/launchctl print "${service}" >/dev/null 2>&1; then
      /bin/launchctl bootout "${service}" >/dev/null 2>&1 || true
    fi
    if /bin/launchctl print "${service}" >/dev/null 2>&1; then
      printf 'Rollback could not stop the replacement service; leaving its plist and the previous backup untouched.\n' >&2
      static_rollback_permitted=0
    else
      if [ -n "${backup_plist}" ] && [ -f "${backup_plist}" ]; then
        rm -f "${plist_path}" || true
        if mv "${backup_plist}" "${plist_path}"; then
          backup_plist=""
        fi
      else
        rm -f "${plist_path}" || true
      fi
    fi
  fi
  if [ "${install_succeeded}" -eq 0 ] \
    && [ "${static_rollback_permitted}" -eq 1 ]; then
    if ! rollback_static_promotion; then
      static_rollback_ready=0
      printf 'Static export rollback failed; the old service will remain stopped.\n' >&2
    fi
  elif [ "${install_succeeded}" -eq 0 ] \
    && [ "${static_rollback_permitted}" -eq 0 ]; then
    static_rollback_ready=0
    printf 'The replacement service is still loaded; retaining its validated static export and rollback evidence.\n' >&2
  fi
  if [ "${was_loaded}" -eq 1 ] && [ "${old_service_stopped}" -eq 1 ] \
    && [ "${install_succeeded}" -eq 0 ] && [ -f "${plist_path}" ] \
    && [ "${static_rollback_ready}" -eq 1 ] \
    && ! /bin/launchctl print "${service}" >/dev/null 2>&1; then
    /bin/launchctl bootstrap "${domain}" "${plist_path}" >/dev/null 2>&1 || true
  fi
  if [ -n "${temporary_plist}" ]; then
    rm -f "${temporary_plist}"
  fi
  if [ -n "${backup_plist}" ] && [ "${install_succeeded}" -eq 1 ]; then
    rm -f "${backup_plist}"
    backup_plist=""
  elif [ -n "${backup_plist}" ] && [ -f "${backup_plist}" ]; then
    printf 'Previous LaunchAgent backup retained after rollback failure: %s\n' \
      "${backup_plist}" >&2
  fi
  release_restart_interlock || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

"${render_command[@]}" >"${temporary_plist}"
/usr/bin/plutil -lint "${temporary_plist}" >/dev/null
chmod 600 "${temporary_plist}"

# Acquire the cross-process mutation interlock before any loaded service can be
# stopped. The helper rechecks locks and queued/running jobs while holding it;
# job admission uses a shared lock, and the upstream scheduler uses this same
# exclusive gate, so neither can cross the check-to-bootout boundary.
if [ "${force_restart_active}" -eq 0 ]; then
  start_restart_interlock
fi

if [ "${was_loaded}" -eq 1 ]; then
  /bin/launchctl bootout "${service}"
fi
old_service_stopped=1

# The old process can no longer admit SQLite jobs. Release only the database
# writer transaction and wait for its acknowledgement before starting a new
# process; the helper continues to hold the adapter lock exclusively.
if [ "${force_restart_active}" -eq 0 ]; then
  release_legacy_database_gate
fi

# The old service is now unable to serve or admit work. Only here may a fully
# validated pending UI generation replace the live directory. A second
# validation after the rename prevents an exporter race from promoting an
# unverified tree; any later installation failure restores the prior generation.
promote_staged_static

replacement_started=1
if [ -f "${plist_path}" ]; then
  backup_plist="${plist_path}.backup.$$"
  cp -p "${plist_path}" "${backup_plist}"
fi

mv "${temporary_plist}" "${plist_path}"
temporary_plist=""

if ! /bin/launchctl bootstrap "${domain}" "${plist_path}"; then
  rm -f "${plist_path}"
  if [ -n "${backup_plist}" ] && [ -f "${backup_plist}" ]; then
    mv "${backup_plist}" "${plist_path}"
    backup_plist=""
    static_rollback_ready=1
    if ! rollback_static_promotion; then
      static_rollback_ready=0
      printf 'Static export rollback failed; the previous service remains stopped.\n' >&2
    fi
    if [ "${was_loaded}" -eq 1 ] && [ "${static_rollback_ready}" -eq 1 ]; then
      /bin/launchctl bootstrap "${domain}" "${plist_path}" || true
    fi
  fi
  replacement_started=0
  printf 'LaunchAgent bootstrap failed; the previous managed plist was restored when available.\n' >&2
  exit 1
fi

/bin/launchctl enable "${service}"
install_succeeded=1

if [ "${force_restart_active}" -eq 0 ]; then
  if ! release_restart_interlock; then
    printf 'Restart interlock release failed after the companion was installed.\n' >&2
    exit 1
  fi
fi

if [ -n "${backup_plist}" ]; then
  rm -f "${backup_plist}"
  backup_plist=""
fi
finalize_static_promotion

printf '%s\n' \
  "Installed operator companion: ${plist_path}" \
  "Service: ${service}" \
  "Primary UI: http://${RECRUITING_ENGINE_HOST}:${RECRUITING_ENGINE_PORT}/app/" \
  "Open/activate: ${OPERATOR_PRODUCT_ROOT}/scripts/open-operator-cockpit.sh" \
  "Logs: ${RECRUITING_ENGINE_OPERATOR_LOG_DIR}" \
  "Companion data was preserved at: ${RECRUITING_ENGINE_DATA_DIR}"

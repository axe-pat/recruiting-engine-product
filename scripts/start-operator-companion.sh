#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

command_name="serve"
dry_run=0
run_preflight=0

usage() {
  cat <<'EOF'
Usage: scripts/start-operator-companion.sh [serve|show-pairing|rotate-pairing]
       [--dry-run] [--production-preflight]

Starts the loopback companion in existing/operator mode. --dry-run validates
and prints only non-secret configuration; it creates no companion data or token.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    serve|show-pairing|rotate-pairing) command_name="$1" ;;
    --dry-run) dry_run=1 ;;
    --production-preflight) run_preflight=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

operator_resolve_config

probe_args=(--quiet)
if [ "${run_preflight}" -eq 1 ]; then
  probe_args+=(--production-preflight)
fi
"${SCRIPT_DIR}/probe-operator-companion.sh" "${probe_args[@]}"

if [ "${dry_run}" -eq 1 ]; then
  operator_print_config
  printf 'Command: %s -m recruiting_companion %s\n' \
    "${RECRUITING_ENGINE_COMPANION_PYTHON}" "${command_name}"
  exit 0
fi

operator_print_config
exec "${RECRUITING_ENGINE_COMPANION_PYTHON}" -m recruiting_companion "${command_name}"

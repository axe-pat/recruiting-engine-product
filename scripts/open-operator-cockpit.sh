#!/bin/bash
set -euo pipefail
# Never allow an inherited or caller-supplied xtrace mode to print the ticket.
set +x
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=operator-companion-common.sh
. "${SCRIPT_DIR}/operator-companion-common.sh"

operator_resolve_config

case "${RECRUITING_ENGINE_HOST}" in
  ::1) url_host="[::1]" ;;
  *) url_host="${RECRUITING_ENGINE_HOST}" ;;
esac
base_url="http://${url_host}:${RECRUITING_ENGINE_PORT}"

if ! /usr/bin/curl --fail --silent --show-error --max-time 3 \
  "${base_url}/api/v1/health" >/dev/null 2>&1; then
  operator_die "the local companion is not responding at ${base_url}"
  exit 1
fi

activation_ticket=""
activation_url=""
trap 'activation_ticket=""; activation_url=""' EXIT HUP INT TERM

if ! activation_ticket="$(
  "${RECRUITING_ENGINE_COMPANION_PYTHON}" \
    -m recruiting_companion issue-local-activation
)"; then
  operator_die "could not issue a local activation; follow the auth repair guidance above"
  exit 1
fi

if [[ ! "${activation_ticket}" =~ ^re_activate_[A-Za-z0-9_-]{20,160}$ ]]; then
  operator_die "the companion returned an invalid local activation"
  exit 1
fi

# The secret remains in the URL fragment. Browsers do not send fragments in
# HTTP requests or referrers, and the activation page clears it from history
# before exchanging it for the host-only HttpOnly cookie.
activation_url="${base_url}/local-activate/#${activation_ticket}"
if ! /usr/bin/open "${activation_url}" >/dev/null 2>&1; then
  operator_die "macOS could not open the local cockpit"
  exit 1
fi

activation_ticket=""
activation_url=""
printf 'Opened the primary local cockpit at %s/app/\n' "${base_url}"

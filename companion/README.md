# Recruiting Engine local companion

The companion is a dependency-free Python 3.11+ loopback application server. It
serves the primary local UI and API from one origin, and also supports the hosted
product surface and Chrome extension. Portable mode starts with an empty,
per-user SQLite database. Existing-engine mode projects bounded evidence from
configured private repositories without copying them into the product database.

## Start it

Build and stage the generated UI from the product repository root:

```bash
npm install
npm run export:static
```

For the production local surface, run the LaunchAgent installer; it promotes the
validated stage while the old service is stopped, or as the first-install
generation when no service exists:

```bash
scripts/install-operator-companion-launch-agent.sh --production-preflight
```

For a foreground development server, first use that same installer workflow to
create the promoted `static-export/`, stop the LaunchAgent, and then start the
companion:

```bash
export PYTHONPATH="$PWD/companion"
export RECRUITING_ENGINE_DATA_DIR="$HOME/.recruiting-engine-companion"
python3 -m recruiting_companion serve
```

Defaults:

- bind: `127.0.0.1:8765`;
- user: `default`;
- data: `~/.recruiting-engine-companion/users/default/`;
- primary UI: `http://127.0.0.1:8765/app/`;
- API: `http://127.0.0.1:8765/api/v1`.

The exporter never mutates the served `static-export/`; it assembles and checks
`static-export.staged/`. Startup validates the promoted tree before binding. It
rejects a missing export, symlink, non-regular entry, unsupported file type,
missing required route, incompatible release marker, or incomplete/tampered
integrity inventory. Each static response rechecks the exact bytes against the
startup inventory, so a post-start change returns `503` without serving the
changed content. Rebuild and reinstall after web source changes; do not restart
an installed companion while one of its process-local workers or an upstream
nightly is active.

The LaunchAgent installer is also safe when the process being replaced predates
the current shared job-admission lock. Its helper holds both the adapter lock and
SQLite writer slot while it performs the final active-job check. After the old
service stops, SQLite is released and acknowledged before the new service starts;
the adapter lock remains held through bootstrap and enable.

The first start creates `pairing-token.txt` and `bearer-token.txt` with mode
`0600`. The primary local UI does not consume the pairing token. Use
`scripts/open-operator-cockpit.sh` once per browser profile: it issues a
two-minute, single-use local activation ticket without printing it and opens a
same-origin fragment URL. Every
`re_pair_...` value remains one-time for hosted/extension clients. Default or
extension pairing returns the existing shared `re_local_...` bearer without
invalidating an already paired extension. Hosted-web pairing uses `client_type:
"web"` and returns a separate `re_web_...` session that expires after 12 hours,
long enough to follow one full nightly cycle. Only its hash and expiry are
persisted; the long-lived local bearer is neither returned nor rotated by web
pairing.

### Primary local UI authentication

The canonical operator URL is `http://127.0.0.1:8765/app/`. Static HTML never
mints a privileged cookie. The explicit launcher reads and validates the private
bearer state, stores only a hash and expiry for a single-use activation ticket,
and opens `/local-activate/#re_activate_...`. Fragments are not sent in HTTP
requests or referrers. The minimal activation page removes the fragment from
history before posting the ticket to the same-origin activation endpoint.

A successful exchange sets a host-only `recruiting_engine_local_ui` cookie with
`Path=/`, a one-year maximum age, `HttpOnly`, and `SameSite=Strict`. The cookie
contains an HMAC-derived `re_ui_...` credential, never the `re_local_...` bearer,
and persists across restarts while that private bearer remains unchanged. The
activation release uses a new derivation generation, so any cookie previously
minted by a raw HTML response is intentionally invalid.

Same-origin API calls send the cookie with credentials mode `same-origin` and
the `X-Recruiting-Engine-Local-UI: 1` guard header. The server additionally
requires a matching loopback Host plus same-origin Origin, Referer, or browser
fetch-site evidence. The cookie is rejected cross-origin, cannot be presented as
a bearer, and cannot call credential rotation. Guarded
`GET /api/v1/local-ui/bootstrap` requires an already-valid cookie, reports only
the local mode, relative API base, authentication state, and version, and may
refresh that cookie. An unauthenticated bootstrap returns `401` plus the safe
`X-Recruiting-Engine-Local-UI-Server: 1` detection header and no cookie.

If `auth.json` and the private bearer ever become inconsistent, activation fails
closed. `python3 -m recruiting_companion repair-auth` is the only repair path;
it runs only for inconsistent state, invalidates bearer/pairing/web/ticket
material, and reports file paths without printing new secrets.

`POST /api/v1/auth/rotate` is the explicit global revocation operation. Only the long-lived local bearer may call it. Rotation returns a replacement local bearer and invalidates the previous local bearer plus every outstanding web session.

Generate a new one-time pairing code:

```bash
python3 -m recruiting_companion rotate-pairing
```

## Stable API contract

Public:

- `GET /api/v1/health`
- `GET /local-activate/` (the ticket stays in the browser fragment)
- `POST /api/v1/local-ui/activate` with a short-lived ticket plus the explicit
  same-origin local guard
- `GET /api/v1/local-ui/bootstrap` with an already-valid guarded local cookie
- `POST /api/v1/pair` with `{"pairing_token":"re_pair_..."}` for an extension/local bearer, or `{"pairing_token":"re_pair_...","client_type":"web"}` for a short web session

Every other route requires either the guarded same-origin local UI cookie,
`Authorization: Bearer re_local_...`, or an unexpired `re_web_...` session. The
local UI and long-lived local bearer keep the full local operator API except that
the UI credential cannot rotate authentication secrets. Web sessions are
constrained by a server-side hosted-UI allowlist; possessing a valid web token is
not enough to access raw resource routes.

Default/extension pairing response:

```json
{"bearer_token":"re_local_...","token_type":"Bearer"}
```

Hosted-web pairing response:

```json
{
  "bearer_token": "re_web_...",
  "token_type": "Bearer",
  "client_type": "web",
  "expires_in": 43200
}
```

Protected routes:

- `POST /api/v1/auth/rotate`
- `GET|PUT /api/v1/profile`
- `GET|PUT /api/v1/preferences`
- `POST /api/v1/onboarding` (JSON or multipart)
- `GET|POST /api/v1/documents` (multipart preferred; base64 JSON supported)
- `GET /api/v1/dashboard`
- `GET|POST /api/v1/runs`; `GET /api/v1/runs/{id}`
- `GET /api/v1/reports/{id}`
- `GET|POST /api/v1/jobs|companies|contacts|applications`
- `GET|PATCH /api/v1/jobs|companies|contacts|applications/{id}`
- `GET|POST /api/v1/outreach`; `GET|PATCH /api/v1/outreach/{id}`
- `POST /api/v1/outreach/{id}/approve`
- `POST /api/v1/intakes`
- `POST /api/v1/imports/jobs` (UTF-8 CSV multipart or JSON rows)
- `GET /api/v1/operator/progress` for the lightweight exact-run polling surface
- `GET /api/v1/existing-engine/status`
- `GET /api/v1/existing-engine/snapshot`
- `GET /api/v1/operator/overview`
- `GET /api/v1/operator/capabilities`
- `GET /api/v1/operator/assets`
- `GET /api/v1/operator/jobs`; `GET /api/v1/operator/jobs/{id}`
- `POST /api/v1/operator/jobs`

The exact `re_web_` allowlist is:

- `GET` dashboard, preferences, existing-engine status/snapshot, and the operator overview/capabilities/assets/jobs projections;
- `PUT` profile and preferences;
- `POST` documents, job imports, portable runs, and fixed operator jobs;
- `PATCH /outreach/{id}` only to `draft` or `reviewed`;
- `POST /outreach/{id}/approve` for the explicit approval step.

Web sessions receive `403 insufficient_scope` for profile reads, document listing, onboarding, run/report detail, all raw jobs/companies/contacts/applications/outreach reads or writes, credential rotation, and outreach `sent`, `replied`, `cancelled`, or `failed` transitions. Those remain local/extension-only.

Collections return `{"items": [...], "count": n}`. Individual resources use a named wrapper such as `{"job": {...}}`. Errors use `{"error":{"code":"...","message":"..."}}`.

### Dashboard presentation contract

`GET /api/v1/dashboard` is the hosted UI's minimized read model. It avoids fetching full jobs, companies, or contacts:

- `application_items` contains only `id`, `company`, `role`, `status`, and `updated_at`;
- `outreach_items` contains only `id`, `company`, `recipient`, `channel`, `state`, the reviewed text when available (otherwise the draft text) as `text`, and `updated_at`;
- `action_queue` contains the complete latest portable-run queue up to that run's server-owned cap (maximum 200), rather than a display-only ten-item slice;
- `latest_report` exposes explicit `input_counts` and `output_counts` without returning the stored report payload;
- `recent_reports` exposes report ID, run ID, kind, creation time, run status, a generated aggregate summary, and output counts.
- `presentation_meta.applications` and `.outreach` each expose `total`, `returned`, and `truncated`, so a bounded DTO list is never mistaken for the complete database.

The presentation DTOs intentionally omit job descriptions, contact email/profile fields, contact notes, document content, delivery references, and unrelated database columns. The bearer-protected resource endpoints remain available for focused local editing flows.

### Portable job import

JSON:

```json
{
  "source_label": "handshake_export",
  "rows": [
    {
      "company": "Example company",
      "title": "Product role",
      "location": "Remote",
      "url": "https://example.invalid/job/1",
      "status": "intake",
      "fit_score": 8.1,
      "role_family": "Product"
    }
  ]
}
```

CSV accepts the same headers and common aliases (`company_name`, `job_title`, `job_url`, `role_type`). Imports are capped at 5,000 rows and deduplicated first by URL, then by normalized company/title/location. The response reports `imported`, `skipped`, and row-level validation errors. A source label describes the user's import; portable mode does not claim to execute any third-party job source.

### Reviewed outreach lifecycle

New outreach always starts in `draft`. Legal transitions are:

```text
draft → reviewed → approved → sent → replied
  └──────────────→ cancelled
approved → failed → reviewed
```

Review requires non-empty final text and an actor. Approval requires a prior review plus an approved/active contact with a confirmed local identity. Recording `sent` requires `confirmed: true` plus an external delivery reference. This portable resource lifecycle never sends a message; the existing-engine operator surface has separate exact-target executors described below.

### Portable run

`POST /api/v1/runs` with `{"type":"portable","config":{"min_fit_score":7,"limit":50}}` creates a deterministic queue and report from the current user's local database. It performs no scrape, model call, application, browser action, or send. Missing fit evidence becomes a review item instead of an inferred score.

### Existing-engine operator surface

`GET /api/v1/operator/overview` is the authenticated cockpit read model. It combines capability state, sanitized installed-engine assets, recent review state, and the ten most recent audited operator jobs. It never includes raw recipient or draft detail. `GET /api/v1/operator/review-targets/<opaque-id>/detail` is the authenticated, no-store endpoint for one selected private review target.

`GET /api/v1/operator/progress` is the authenticated high-frequency read path.
Its top-level object contains exactly `schema_version`, `generated_at`,
`current_run_progress`, and the ten most recent audited `recent_jobs`. It does
not build capabilities, workbooks, apply-queue rows, stories, review targets,
report history, or the next-run plan. The progress adapter reads mutable active
evidence only while the exact pipeline lock is owned; when idle, it verifies at
most the newest terminal run as its fallback.

Current-snapshot projections use a noninterfering `stable-at-capture` ledger. The companion timestamps capture before probing all five producer locks, requires every probe to report `free`, fingerprints bounded mutable input trees/files, reads stable content hashes, probes all locks again, and revalidates every identity/hash before returning anything mutable. Any lock or fingerprint change discards the entire mutable bundle together. The companion deliberately never owns an upstream lock during a read: scheduler, workbook, and queue writers use nonblocking exclusive acquisition, so a UI refresh must not make production work skip or fail. This proves a stable bounded capture, not a cross-repository transaction. The projections include:

- aggregate ResumeGenerator job/archive/review-cache workbook counts;
- aggregate account-tracker counts plus at most 50 minimized Action Queue rows containing only company, tier, stage, an allowlisted action category, due date, and scores;
- at most 100 current apply-queue rows containing only job ID, company, role, scores, rank, allowlisted status/bucket, and per-item material-presence flags;
- story/corpus inventory counts plus at most 50 curated Markdown filenames and titles from the story engine, story sources, and story bank—never document contents or private interview-prep text;
- communication outcome totals and recommendation/review counts without messages, contacts, or rationale text.

Operator assets schema `1.1` adds three primary-UI projections:

- `current_run_progress` selects an exact active scheduler/pipeline attempt when
  both locks prove it is running, or the newest fully verified terminal run when
  idle. If scheduler state records a newer completed actual-pipeline attempt whose
  exact summary/report chain does not verify, the projection instead returns a
  noncurrent `attention` result bound to that attempt's run ID and timestamps; it
  exposes only a generic verification/exit reason and minimized scheduler-state
  evidence. Active progress may use only the scheduler attempt, timestamped run log,
  exact active-run manifest/action queue, and allowlisted aggregate LinkedIn
  progress/scoring fields. Raw logs, searches, cards, URLs, and result rows are
  never returned;
- `next_run_plan` returns at most 30 evidence-derived items based on the latest
  verified terminal run and the current durable review ledger. It marks itself
  `partial` while a newer run is active and rebases after terminal verification;
- `account_tracker` returns a nontransactional `stable-at-capture` aggregate with due, tier, stage, action,
  activity, people, and score summaries plus the guarded
  `open.account_tracker` action. The browser cannot provide a workbook path.

Clients poll `GET /api/v1/operator/progress` while either a scheduled run or
companion operator job is active, reserving the full overview for broader
refreshes. Progress is tied to the exact
run ID and bound artifacts. A visible Playwright or Chrome process is never
sufficient evidence by itself. The upstream engine marks its dedicated LinkedIn
Chrome process with a private per-run owner value and closes only that owned
process during terminal cleanup; the companion does not expose the marker or
inspect unrelated personal browser windows. The active UI presents this as an
upstream cleanup contract, not proof of a browser PID or completed cleanup.
The idle progress endpoint sorts summary candidates newest-first and stops after
the first fully verified projection; it does not rescan the complete run history
on each four-second poll. The full overview/history may still scan all runs.

Every bounded collection returns its limit, total/returned counts, and `truncated` state. Workbook hashes and paths are repository-relative. No contact name, email address, message body, URL, arbitrary workbook cell, absolute path, or raw document content is returned.

`assets.source_metrics` is retained as the compatibility field name, but its
display rows are the exact Daily Engine manifest's `source_families` aggregates
and carry the manifest evidence hash. The separately pointed source-metrics file
must still validate as part of the complete run chain; its richer raw object is
not substituted into the overview. Failed/timed-out source states receive only
generic error markers, never upstream error text. Daily reports and these source
rows are always run-scoped and appear only after the summary, manifest,
source/action pointers, and exact Outreach report all verify. A mutable `latest`
file is never substituted.

Report history returns at most 20 rows but carries the full verified-run total
from the same adapter status scan, so `items_total` and `truncated` remain honest.
The durable review ledger likewise computes `review_counts` across all rows
after expiry; only `recent_reviews` is capped at 25 and publishes its own
returned/total/truncated metadata.

`POST /api/v1/operator/jobs` accepts only `command_id`, `confirmation`, and `parameters`. Every capability publishes its exact JSON parameter schema with `additional_properties: false`. Most commands require `{}`; selected queue actions use exactly a numeric `job_id`. Consequential actions require exactly an opaque `review_id` plus `target_id`, both issued by the companion.

The guarded registry recognizes these commands:

- `production.preflight` runs the fixed check-only argv configured below;
- `accounts.refresh` rebuilds the fixed `workspace/account_tracker.xlsx` from the installed Outreach workspace;
- `reports.daily.refresh` rebuilds a report only for the newest fully verified run, passing the exact summary `created_at`, summary path, and run ID through the fixed `--since`, `--nightly-summary`, and `--run-id` trio;
- `reports.sources.refresh` passes that run's exact source-metrics path and run ID to the role-surface report builder;
- `reports.cadence.refresh`, `reports.outcomes.refresh`, and `communications.lab.refresh` run their fixed local artifact builders;
- `outreach.plan.preview` runs only `build-track-2-daily-plan` with fixed bounded budgets and zero email drafts. It has no execution or delivery flag;
- `application.resume.generate` accepts one current-queue numeric job ID and runs only resume-only, budget-mode, serial generation with a bounded inner and outer timeout. Its confirmation phrase explicitly acknowledges model cost;
- `application.apply_packet.build` builds one local review packet and never invokes the rtrvr runner or `--live`;
- `open.account_tracker`, `open.current_apply_queue`, `open.latest_report`, `open.story_workbench`, and `open.communication_review` call `/usr/bin/open` with one server-owned, allowlisted path;
- `open.application_folder` accepts one current-queue numeric job ID and opens only that row's validated folder;
- `application.status.applied` and `application.status.closed` run the fixed artifact-preserving lifecycle transition for one approved exact job. Closed maps to upstream `not-applied`; no caller status is accepted;
- `application.assist.fill_to_review` remains a visible but non-executable lane. The installed rtrvr runner has only a prompt-level `stop_before_submit` instruction and no tool-enforced Submit interceptor, so the companion refuses live browser execution;
- `nightly.run` runs one reviewed off-cycle production cycle through the attested scheduler wrapper after a second production preflight. It loads ResumeGenerator's fixed `nightly_contract.py print` output, binds the contract script and exact argv into review, and reuses that output for execution; exact email sends remain recipient-reviewed;
- `outreach.linkedin.send` materializes an exact preview/approval and invokes the replay-protected one-record executor. Only an exact completed receipt is success; blocked, unknown, or missing receipts require reconciliation;
- `outreach.email.send` writes one private reviewed draft and approval row and invokes the fixed SMTP command with `--limit 1 --execute`. Only a bound result artifact reporting exactly one sent row is success; exit zero alone is insufficient;
- legacy generic `outreach.send` remains forbidden because it names neither channel nor recipient.

Every executable action requires its exact capability-specific confirmation phrase, re-checks availability and lock state, rejects symlinks and out-of-root targets, and uses an argument vector with `shell=False`. Immediately before any reviewed LinkedIn, SMTP, or application-lifecycle approval is consumed, the companion reruns the fixed production-attestation preflight; a dirty or changed upstream release leaves the approval unconsumed. Local-write/model actions create an audited job immediately and run in a daemon worker. Except for any separately documented nightly lock order, the worker holds the shared runtime `operator_mutation.lock` for the subprocess, requires every upstream lock to be free at start, and enforces a command-specific timeout. The persistent audit row records only validated identifiers, command/status/scope/timestamps, the argv hash, lock states, return code, and hashes/line counts for output. It never stores or returns stdout/stderr.

Consequential review is a durable `pending → reviewed → approved → consumed` state machine with explicit revoke, stale, and expired states. Review and approval use separate typed phrases. Targets expire after 24 hours, bind at most one item, and are re-hashed before every transition and execution. LinkedIn/email subject and body edits are accepted only through the dedicated selected-review endpoint; an edit resets the state to pending and rebinds the content hash. Approval is consumed before spawn, so an uncertain result is reconciled instead of retried. Exact-run invite, follow-up, and email source pointers are accepted only through the verified Daily Engine manifest, within configured roots, with source SHA binding and no symlink or mutable `latest` alias. The API accepts no caller path, flag, environment override, shell text, recipient, model, or limit.

Queue rows expose the same per-item action status, reason, confirmation phrase, and server-generated `{job_id}` parameters used by the registry. A queue row with a nonnumeric ID cannot trigger an application action. Resume generation is unavailable when a safe folder/job description is missing or a resume already exists; folder opening is unavailable when the folder is missing or unsafe.

## Security boundary

- The server refuses non-loopback binds unless an explicit override is set.
- Every request must use a loopback `Host` header with the actual bound port, preventing DNS-rebinding access.
- CORS allows the configured hosted origin, loopback development origins, and valid Chrome extension origins. It never uses a wildcard or permits the local UI cookie as a cross-origin credential. Cookie authentication is same-origin loopback only.
- Generated static routes are no-store, deny framing, restrict content types and paths, and receive a restrictive Content Security Policy. Raw HTML never establishes a privileged cookie and never contains the local bearer or activation ticket.
- Responses are `no-store` and `nosniff`.
- Upload names are reduced to a safe basename; document API responses omit storage paths.
- The long-lived local token hash, active web-session hashes/expiries, and at
  most eight active local-activation hashes/expiries live in `auth.json`.
  Activation tickets expire after at most two minutes and are consumed once.
  Plaintext web sessions exist only in the pairing response; plaintext local
  activation exists only in the launcher/browser handoff. Auth state changes
  use a cross-process mode-`0600` file lock. Local token files and document
  files are best-effort `0600`.
- Each `RECRUITING_ENGINE_USER_ID` has a separate database and document directory.
- When a runtime directory is configured, `Settings.prepare()` uses its persistent mode-`0600` `operator_mutation.lock`; portable mode without a runtime directory falls back to the per-user companion directory. Existence is not a busy signal—advisory ownership determines `free` versus `busy`.

The extension/local bearer is a shared local-device secret, not a multi-user
identity system. Hosted pages receive only short-lived web sessions. The primary
local UI receives only its derived HttpOnly cookie. Local rotation revokes local
and web bearer sessions and changes the credential from which the next local UI
cookie is derived. Do not expose the companion directly to a network or the
public internet.

## Optional existing-engine operator mode

The portable companion works without either private engine. The guarded existing-engine operator surface can be enabled with:

```bash
export RECRUITING_ENGINE_RESUME_ROOT="/path/to/resume-engine"
export RECRUITING_ENGINE_OUTREACH_ROOT="/path/to/outreach-engine"
export RECRUITING_ENGINE_RUNTIME_DIR="/path/to/runtime-lock-directory"
export RECRUITING_ENGINE_ATTESTATION_PATH="/path/to/release-attestation.json"
export RECRUITING_ENGINE_RESUME_PYTHON="/path/to/resume-engine/venv/bin/python"
export RECRUITING_ENGINE_OUTREACH_PYTHON="/path/to/outreach-engine/.venv/bin/python"
export RECRUITING_ENGINE_MODE="existing"
```

`RECRUITING_ENGINE_MODE` is validated as `portable` or `existing`. It supplies the initial preference only when that user has no persisted preference row; a user's later mode choice survives companion restarts and environment changes.

The shorter `RESUMEGEN_ROOT` and `OUTREACH_ROOT` names are accepted as compatibility aliases. The adapter follows the actual upstream contract: the summary supplies run identity, terminal state, failures, the exact Daily Engine manifest, and the authoritative run-report pointer. It then validates manifest schema/version, typed source and delivery fields, readable source/action pointers, and the report's run ID, summary, and window binding. It does not invent schema or run-ID requirements for source/action payloads that do not promise them. It rejects `latest`/`current` aliases and pointers that leave configured roots. The attestation is a readable preflight file, not a hash embedded in run artifacts. Generic live-pipeline mode remains disabled even if `RECRUITING_ENGINE_ALLOW_LIVE_RUNS=1` is present. The separate reviewed-action gate may run only the fingerprinted production-nightly contract described above. A zero process exit becomes a completed operator job only when exactly one new exact-run evidence chain verifies as healthy and proves `full_delivery`.

The snapshot endpoint projects only aggregate source, queue, stage, workspace, and action counts from the latest verified run. Before a run is accepted, each of the six action-queue lanes—application-plus-outreach, application-only, outreach-only-today, relationship buffer, follow-up, and skipped-internal—must be a list of objects, and its reported count must exactly match the list length. `decision_total_parts` and `decision_total` are derived from those validated lengths and labeled `validated_action_queue_lane_entries`; the adapter does not claim that identities are exclusive across lanes. Overlapping diagnostic/scoring counts remain visible under `counts` but are not added to the validated lane-entry total. A missing, malformed, or contradictory lane rejects the run evidence chain.

A separately labeled `current_workspace` section reads the current application-queue manifest/priority file and Outreach CSV row counts only when scheduler, pipeline, workbook, current-queue refresh, and shared operator locks are all positively observed as `free`. A held `.current_apply_queue.lock` returns `busy` and suppresses current rows and selected-job actions. `unavailable` and `not_configured` are fail-closed states, not permission to read mutable files. The snapshot never returns current queue rows, company names, contacts, URLs, messages, or document text, and it never blends current counts into run-scoped evidence.

## Tests

No third-party package is needed:

```bash
PYTHONPATH=companion python3 -m unittest discover -s companion/tests -v
python3 -m compileall -q companion/recruiting_companion companion/tests
```

## Current limitations

- One companion process serves one configured local user; this is not a hosted multi-tenant service.
- The long-lived bearer is shared across paired extension/local clients; named device tokens are not implemented. The primary UI is device-local and cookie-authenticated; hosted web sessions remain hash-only, tab-scoped in the UI, and expire after 12 hours.
- Documents can be uploaded and cataloged, but this release does not parse resume content or run models.
- Portable runs use imported scores and explicit states. They do not calculate semantic fit.
- The existing-engine bridge exposes aggregate/minimized projections plus only the fixed, confirmed actions listed above; it deliberately fails closed on legacy artifacts without the exact attested manifest contract.
- Background operator workers are process-local and do not resume after a companion restart; their audit rows remain for diagnosis.
- HTTPS pages may face browser private-network/mixed-content restrictions when calling loopback HTTP directly. This does not affect the canonical same-origin loopback UI; the Chrome extension remains the preferred bridge for a hosted page where those restrictions apply.

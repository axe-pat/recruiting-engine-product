# Recruiting Engine local companion

The companion is a dependency-free Python 3.11+ loopback API for the hosted product surface and Chrome extension. It starts with an empty, per-user SQLite database and never copies data from the private source repositories.

## Start it

From the product repository root:

```bash
export PYTHONPATH="$PWD/companion"
export RECRUITING_ENGINE_DATA_DIR="$HOME/.recruiting-engine-companion"
python3 -m recruiting_companion serve
```

Defaults:

- bind: `127.0.0.1:8765`;
- user: `default`;
- data: `~/.recruiting-engine-companion/users/default/`;
- API: `http://127.0.0.1:8765/api/v1`.

The first start creates `pairing-token.txt` and `bearer-token.txt` with mode `0600`. Every `re_pair_...` value is one-time. Default/extension pairing returns the existing shared `re_local_...` bearer without invalidating an already paired extension. Hosted-web pairing uses `client_type: "web"` and returns a separate `re_web_...` session that expires after 12 hours, long enough to follow one full nightly cycle. Only its hash and expiry are persisted; the long-lived local bearer is neither returned nor rotated by web pairing.

`POST /api/v1/auth/rotate` is the explicit global revocation operation. Only the long-lived local bearer may call it. Rotation returns a replacement local bearer and invalidates the previous local bearer plus every outstanding web session.

Generate a new one-time pairing code:

```bash
python3 -m recruiting_companion rotate-pairing
```

## Stable API contract

Public:

- `GET /api/v1/health`
- `POST /api/v1/pair` with `{"pairing_token":"re_pair_..."}` for an extension/local bearer, or `{"pairing_token":"re_pair_...","client_type":"web"}` for a short web session

Every other route requires `Authorization: Bearer re_local_...` or an unexpired `re_web_...` session. The long-lived local bearer keeps the full local/extension API. Web sessions are constrained by a server-side hosted-UI allowlist; possessing a valid web token is not enough to access raw resource routes.

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
  "expires_in": 1800
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

Review requires non-empty final text and an actor. Approval requires a prior review plus an approved/active contact with a confirmed local identity. Recording `sent` requires `confirmed: true` plus an external delivery reference. The companion never sends a message; it only records a separately confirmed delivery.

### Portable run

`POST /api/v1/runs` with `{"type":"portable","config":{"min_fit_score":7,"limit":50}}` creates a deterministic queue and report from the current user's local database. It performs no scrape, model call, application, browser action, or send. Missing fit evidence becomes a review item instead of an inferred score.

### Existing-engine operator surface

`GET /api/v1/operator/overview` is the paired cockpit read model. It combines capability state, sanitized installed-engine assets, recent review state, and the ten most recent audited operator jobs. It never includes raw recipient or draft detail. `GET /api/v1/operator/review-targets/<opaque-id>/detail` is the authenticated, no-store endpoint for one selected private review target.

Current-snapshot projections require scheduler, pipeline, workbook, current-queue refresh, and shared operator locks to all be positively observed as free. The queue lock is the producer's exact `apps/Apply queues/.current_apply_queue.lock`; the refresh script holds it exclusively across its workbook probe, applied-PDF sync, staging build, validation, and atomic directory swap. They include:

- aggregate ResumeGenerator job/archive/review-cache workbook counts;
- aggregate account-tracker counts plus at most 50 minimized Action Queue rows containing only company, tier, stage, an allowlisted action category, due date, and scores;
- at most 100 current apply-queue rows containing only job ID, company, role, scores, rank, allowlisted status/bucket, and per-item material-presence flags;
- story/corpus inventory counts plus at most 50 curated Markdown filenames and titles from the story engine, story sources, and story bank—never document contents or private interview-prep text;
- communication outcome totals and recommendation/review counts without messages, contacts, or rationale text.

Every bounded collection returns its limit, total/returned counts, and `truncated` state. Workbook hashes and paths are repository-relative. No contact name, email address, message body, URL, arbitrary workbook cell, absolute path, or raw document content is returned.

Daily reports and source metrics are different: they are always run-scoped and appear only after the nightly summary, exact Daily Engine manifest, source/action pointers, and exact Outreach report all pass the full evidence chain. A mutable `latest` file is never substituted. Failed/timed-out source states receive generic error markers; raw upstream error text is not projected.

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
- `nightly.run` runs one reviewed prepare/generate cycle through the attested scheduler wrapper after a second production preflight. Its fixed arguments set legacy sends to zero and omit every direct email/LinkedIn delivery flag;
- `outreach.linkedin.send` materializes an exact preview/approval and invokes the replay-protected one-record executor. Only an exact completed receipt is success; blocked, unknown, or missing receipts require reconciliation;
- `outreach.email.send` writes one private reviewed draft and approval row and invokes the fixed SMTP command with `--limit 1 --execute`. Only a bound result artifact reporting exactly one sent row is success; exit zero alone is insufficient;
- legacy generic `outreach.send` remains forbidden because it names neither channel nor recipient.

Every executable action requires its exact capability-specific confirmation phrase, re-checks availability and lock state, rejects symlinks and out-of-root targets, and uses an argument vector with `shell=False`. Immediately before any reviewed LinkedIn, SMTP, or application-lifecycle approval is consumed, the companion reruns the fixed production-attestation preflight; a dirty or changed upstream release leaves the approval unconsumed. Local-write/model actions create an audited job immediately and run in a daemon worker. Except for any separately documented nightly lock order, the worker holds the shared runtime `operator_mutation.lock` for the subprocess, requires every upstream lock to be free at start, and enforces a command-specific timeout. The persistent audit row records only validated identifiers, command/status/scope/timestamps, the argv hash, lock states, return code, and hashes/line counts for output. It never stores or returns stdout/stderr.

Consequential review is a durable `pending → reviewed → approved → consumed` state machine with explicit revoke, stale, and expired states. Review and approval use separate typed phrases. Targets expire after 24 hours, bind at most one item, and are re-hashed before every transition and execution. LinkedIn/email subject and body edits are accepted only through the dedicated selected-review endpoint; an edit resets the state to pending and rebinds the content hash. Approval is consumed before spawn, so an uncertain result is reconciled instead of retried. Exact-run invite, follow-up, and email source pointers are accepted only through the verified Daily Engine manifest, within configured roots, with source SHA binding and no symlink or mutable `latest` alias. The API accepts no caller path, flag, environment override, shell text, recipient, model, or limit.

Queue rows expose the same per-item action status, reason, confirmation phrase, and server-generated `{job_id}` parameters used by the registry. A queue row with a nonnumeric ID cannot trigger an application action. Resume generation is unavailable when a safe folder/job description is missing or a resume already exists; folder opening is unavailable when the folder is missing or unsafe.

## Security boundary

- The server refuses non-loopback binds unless an explicit override is set.
- Every request must use a loopback `Host` header with the actual bound port, preventing DNS-rebinding access.
- CORS allows the configured hosted origin, loopback development origins, and valid Chrome extension origins. It never uses a wildcard and does not use credential cookies.
- Responses are `no-store` and `nosniff`.
- Upload names are reduced to a safe basename; document API responses omit storage paths.
- The long-lived local token hash and active web-session hashes/expiries live in `auth.json`; plaintext web sessions exist only in the pairing response. Local token files and document files are best-effort `0600`.
- Each `RECRUITING_ENGINE_USER_ID` has a separate database and document directory.
- When a runtime directory is configured, `Settings.prepare()` uses its persistent mode-`0600` `operator_mutation.lock`; portable mode without a runtime directory falls back to the per-user companion directory. Existence is not a busy signal—advisory ownership determines `free` versus `busy`.

The extension/local bearer is a shared local-device secret, not a multi-user identity system. Hosted pages receive only short-lived web sessions. Local rotation revokes every local and web client. Do not expose the companion directly to a network or the public internet.

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

The shorter `RESUMEGEN_ROOT` and `OUTREACH_ROOT` names are accepted as compatibility aliases. The adapter follows the actual upstream contract: the summary supplies run identity, terminal state, failures, the exact Daily Engine manifest, and the authoritative run-report pointer. It then validates manifest schema/version, typed source and delivery fields, readable source/action pointers, and the report's run ID, summary, and window binding. It does not invent schema or run-ID requirements for source/action payloads that do not promise them. It rejects `latest`/`current` aliases and pointers that leave configured roots. The attestation is a readable preflight file, not a hash embedded in run artifacts. Generic live-pipeline mode remains disabled even if `RECRUITING_ENGINE_ALLOW_LIVE_RUNS=1` is present. The separate reviewed-action gate may run only the fingerprinted no-delivery nightly contract described above.

The snapshot endpoint projects only aggregate source, queue, stage, workspace, and action counts from the latest verified run. Its queue `decision_total` is the sum of six mutually exclusive decision lanes only: application-plus-outreach, application-only, outreach-only-today, relationship buffer, follow-up, and skipped-internal. The response includes `decision_total_name` and every `decision_total_parts` value; overlapping diagnostic/scoring counts remain visible under `counts` but are not added to the total.

A separately labeled `current_workspace` section reads the current application-queue manifest/priority file and Outreach CSV row counts only when scheduler, pipeline, workbook, current-queue refresh, and shared operator locks are all positively observed as `free`. A held `.current_apply_queue.lock` returns `busy` and suppresses current rows and selected-job actions. `unavailable` and `not_configured` are fail-closed states, not permission to read mutable files. The snapshot never returns current queue rows, company names, contacts, URLs, messages, or document text, and it never blends current counts into run-scoped evidence.

## Tests

No third-party package is needed:

```bash
PYTHONPATH=companion python3 -m unittest discover -s companion/tests -v
python3 -m compileall -q companion/recruiting_companion companion/tests
```

## Current limitations

- One companion process serves one configured local user; this is not a hosted multi-tenant service.
- The long-lived bearer is shared across paired extension/local clients; named device tokens are not implemented. Hosted web sessions are hash-only, tab-scoped in the UI, and expire after 12 hours.
- Documents can be uploaded and cataloged, but this release does not parse resume content or run models.
- Portable runs use imported scores and explicit states. They do not calculate semantic fit.
- The existing-engine bridge exposes aggregate/minimized projections plus only the fixed, confirmed actions listed above; it deliberately fails closed on legacy artifacts without the exact attested manifest contract.
- Background operator workers are process-local and do not resume after a companion restart; their audit rows remain for diagnosis.
- HTTPS pages may face browser private-network/mixed-content restrictions when calling loopback HTTP directly; the Chrome extension is the preferred bridge where those restrictions apply.

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

The first start creates `pairing-token.txt` and `bearer-token.txt` with mode `0600`. Every `re_pair_...` value is one-time. Default/extension pairing returns the existing shared `re_local_...` bearer without invalidating an already paired extension. Hosted-web pairing uses `client_type: "web"` and returns a separate `re_web_...` session that expires after 30 minutes. Only its hash and expiry are persisted; the long-lived local bearer is neither returned nor rotated by web pairing.

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

The exact `re_web_` allowlist is:

- `GET` dashboard, preferences, and existing-engine status/snapshot;
- `PUT` profile and preferences;
- `POST` documents, job imports, and portable runs;
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

## Security boundary

- The server refuses non-loopback binds unless an explicit override is set.
- Every request must use a loopback `Host` header with the actual bound port, preventing DNS-rebinding access.
- CORS allows the configured hosted origin, loopback development origins, and valid Chrome extension origins. It never uses a wildcard and does not use credential cookies.
- Responses are `no-store` and `nosniff`.
- Upload names are reduced to a safe basename; document API responses omit storage paths.
- The long-lived local token hash and active web-session hashes/expiries live in `auth.json`; plaintext web sessions exist only in the pairing response. Local token files and document files are best-effort `0600`.
- Each `RECRUITING_ENGINE_USER_ID` has a separate database and document directory.
- `Settings.prepare()` creates a persistent mode-`0600` companion mutation-lock file. Its existence is not a busy signal; advisory ownership determines `free` versus `busy`.

The extension/local bearer is a shared local-device secret, not a multi-user identity system. Hosted pages receive only short-lived web sessions. Local rotation revokes every local and web client. Do not expose the companion directly to a network or the public internet.

## Optional existing-engine status

The portable companion works without either private engine. A read-only verification status can be enabled with:

```bash
export RECRUITING_ENGINE_RESUME_ROOT="/path/to/resume-engine"
export RECRUITING_ENGINE_OUTREACH_ROOT="/path/to/outreach-engine"
export RECRUITING_ENGINE_RUNTIME_DIR="/path/to/runtime-lock-directory"
export RECRUITING_ENGINE_ATTESTATION_PATH="/path/to/release-attestation.json"
```

The shorter `RESUMEGEN_ROOT` and `OUTREACH_ROOT` names are accepted as compatibility aliases. The adapter follows the actual upstream contract: the summary supplies run identity, terminal state, failures, the exact Daily Engine manifest, and the authoritative run-report pointer. It then validates manifest schema/version, typed source and delivery fields, readable source/action pointers, and the report's summary/window binding. It does not invent schema or run-ID requirements for source/action/report payloads that do not promise them. It rejects `latest`/`current` aliases and pointers that leave configured roots. The attestation is a readable preflight file, not a hash embedded in run artifacts. The companion never invokes a live pipeline—even if `RECRUITING_ENGINE_ALLOW_LIVE_RUNS=1` is present.

The snapshot endpoint projects only aggregate source, queue, stage, workspace, and action counts from the latest verified run. Its queue `decision_total` is the sum of six mutually exclusive decision lanes only: application-plus-outreach, application-only, outreach-only-today, relationship buffer, follow-up, and skipped-internal. The response includes `decision_total_name` and every `decision_total_parts` value; overlapping diagnostic/scoring counts remain visible under `counts` but are not added to the total.

A separately labeled `current_workspace` section reads the current application-queue manifest/priority file and Outreach CSV row counts only when scheduler, pipeline, workbook, and companion adapter-mutation locks are all positively observed as `free`. `unavailable` and `not_configured` are fail-closed states, not permission to read mutable files. The snapshot never returns current queue rows, company names, contacts, URLs, messages, or document text, and it never blends current counts into run-scoped evidence.

## Tests

No third-party package is needed:

```bash
PYTHONPATH=companion python3 -m unittest discover -s companion/tests -v
python3 -m compileall -q companion/recruiting_companion companion/tests
```

## Current limitations

- One companion process serves one configured local user; this is not a hosted multi-tenant service.
- The long-lived bearer is shared across paired extension/local clients; named device tokens are not implemented. Hosted web sessions are hash-only and expire after 30 minutes.
- Documents can be uploaded and cataloged, but this release does not parse resume content or run models.
- Portable runs use imported scores and explicit states. They do not calculate semantic fit.
- The existing-engine bridge exposes only read-only aggregate status/snapshots and deliberately fails closed on legacy artifacts without the exact attested manifest contract.
- HTTPS pages may face browser private-network/mixed-content restrictions when calling loopback HTTP directly; the Chrome extension is the preferred bridge where those restrictions apply.

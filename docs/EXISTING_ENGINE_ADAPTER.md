# Existing-engine adapter

## Purpose

The existing-engine adapter is a local, read-first bridge between the product
companion and an already installed recruiting engine. It does not replace the
engine's discovery, scoring, review, generation, or delivery logic. It exposes
normalized status and run evidence while keeping private operating data on the
user's machine.

The current single-user product serves its primary UI and API from the same
loopback companion at `http://127.0.0.1:8765/app/`. The adapter remains the
source-normalization layer; the separate operator backend owns fixed mutations,
review/approval, audits, progress/plan projections, and desktop-open actions.
The hosted build continues to use the same minimized adapter contracts after an
explicit pairing exchange.

This document defines the public integration contract. It intentionally contains
no production artifact payloads, contacts, messages, credentials, browser state,
or machine-specific paths.

## Configuration

The names below configure the adapter. They do not change the upstream engine's
own configuration.

| Variable | Purpose |
|---|---|
| `RECRUITING_ENGINE_MODE` | Set to `existing` for this adapter. |
| `RECRUITING_ENGINE_RESUME_ROOT` | Root of the application/resume engine repository. |
| `RECRUITING_ENGINE_OUTREACH_ROOT` | Root of the outreach engine repository. |
| `RECRUITING_ENGINE_RESUME_PYTHON` | Python interpreter for the application/resume engine. |
| `RECRUITING_ENGINE_OUTREACH_PYTHON` | Python interpreter for the outreach engine. |
| `RECRUITING_ENGINE_RUNTIME_DIR` | Directory containing the upstream scheduler and pipeline lock files. |
| `RECRUITING_ENGINE_ATTESTATION_PATH` | Tested-release attestation used by production preflight. |
| `RECRUITING_ENGINE_SCHEDULER_LABEL` | Optional platform scheduler label for status display only. |
| `RECRUITING_ENGINE_DATA_DIR` | Companion-owned state, cache, and adapter-lock directory. |

All values must be configured locally. Public clients receive capability states
and repository-relative evidence tokens, never the configured absolute paths.

## Expected upstream surfaces

Paths in this section are relative to the configured roots.

Application/resume engine:

- `discovery/scripts/nightly_prompt.py`
- `discovery/scripts/run_nightly_pipeline.py`
- `discovery/scripts/run_daily_engine.py`
- `discovery/source_validation/`
- `discovery/jobs.xlsx`
- `discovery/.jobs.lock`
- `apps/Apply queues/current_apply_queue/manifest.json`
- `apps/Apply queues/current_apply_queue/priority_order.json`
- allowlisted, timestamped active-run logs and LinkedIn aggregate
  progress/scoring artifacts used only while the exact scheduler/pipeline
  attempt is active

Outreach engine:

- `main.py`
- `workspace/organizations.csv`
- `workspace/opportunities.csv`
- `workspace/contacts.csv`
- `workspace/touchpoints.csv`
- `workspace/sources.csv`
- `workspace/reports/`
- `workspace/linkedin_invite_send_reservations.json`

The adapter must report a missing surface as `not_configured` or `unavailable`.
It must not manufacture an empty successful run.

## Production entrypoint

The platform scheduler should invoke `discovery/scripts/nightly_prompt.py`, with
production-attestation enforcement, rather than calling
`run_nightly_pipeline.py` directly. The scheduler owns due-state and same-day
replay prevention; the pipeline owns the run lock and report finalization.

The only command in the zero-mutation executable allowlist is production
preflight:

```bash
"${RECRUITING_ENGINE_RESUME_PYTHON}" \
  discovery/scripts/nightly_prompt.py \
  --production-check-only \
  --production-attestation "${RECRUITING_ENGINE_ATTESTATION_PATH}"
```

Run it with `RECRUITING_ENGINE_RESUME_ROOT` as the working directory and as an
argument vector, never through a shell. It validates repository access, protected
code cleanliness, tested revisions, and attested test evidence. It does not read
or mutate scheduler due-state and cannot start the pipeline.

## Core zero-mutation allowlist

The adapter's read-first core is deny-by-default. These operations require no
separate human authorization flow:

| Command ID | Behavior |
|---|---|
| `engine.capabilities.read` | Validate configured surfaces and return capability states. |
| `engine.status.read` | Inspect lock ownership and scheduler metadata without starting work. |
| `run.list` | List validated nightly summaries. |
| `run.read` | Read one summary and only its exact evidence pointers. |
| `snapshot.read` | Build a normalized in-memory snapshot from validated evidence. |
| `production.preflight` | Run the fixed check-only command shown above. |

The adapter may hash readable evidence files and return repository-relative path
tokens. It must not return raw message text, email addresses, profile URLs,
credentials, environment values, or arbitrary file contents.

Report builders, queue builders, workbook exports, and source captures are not
zero-mutation operations: even when they do not send externally, they write local
artifacts or use browser/network state. The current product exposes only a named,
fixed-argument subset through the reviewed operator backend described in
[OPERATOR_COCKPIT.md](OPERATOR_COCKPIT.md). They are not added to this raw
adapter allowlist.

## Human-gated operator boundary

The following remain forbidden to the raw adapter and to caller-supplied command
input:

- an arbitrary command, path, environment override, or CLI flag;
- a forced nightly run or direct Daily Engine/pipeline invocation outside the
  exact reviewed `nightly.run` contract;
- any flag containing `--execute`, `--execute-sends`, `--send-linkedin`,
  `--generate`, `--force`, or `--promote-approved`;
- unreviewed, multi-target, or caller-defined LinkedIn invites, follow-ups,
  inbox replies, or other browser delivery;
- unreviewed or caller-defined SMTP delivery, even when a draft exists;
- live browser capture, account mapping, or contact-information research;
- relationship imports, company promotion, or blind tracker status changes;
- disabling the Track 2 outer timeout;
- automatic retry after a timeout or an uncertain delivery result.

The implemented privileged operator API uses named server-owned commands,
bounded limits, exact review and approval where consequential, an explicit typed
confirmation, a valid production preflight, and the lock discipline below. It
never accepts raw shell input. The reviewed production nightly is bound to the
canonical upstream argument vector and can include bounded application-queue and
Track 2 LinkedIn delivery; email remains separately recipient-reviewed. Final
application submission remains outside the cockpit.

Email delivery has an additional content-bound gate: the exact recipient,
subject, and body must be approved in a review artifact; the approval must be
bound to that artifact; credentials must be ready; and the batch must remain
bounded. Draft creation is not send authorization.

## Lock discipline

Lock files persist, so existence is not evidence that work is running. The
adapter must attempt a non-blocking advisory lock and immediately release it to
determine whether another process owns the lock.

1. The scheduler lock is `${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_scheduler.lock`.
   The upstream scheduler holds it across due-state evaluation and the complete
   pipeline invocation.
2. The pipeline lock is `${RECRUITING_ENGINE_RUNTIME_DIR}/nightly_pipeline.lock`.
   A second direct pipeline invocation is a conflict, not another run.
3. Workbook writers coordinate through `discovery/.jobs.lock` and may wait for a
   bounded period.
4. Invite delivery has a dedicated reservation-ledger lock and an atomic ledger.
   Uncertain sends require reconciliation and block automatic retry.
5. Outreach CSV tables do not provide a global transaction lock. The adapter
   must therefore hold one companion-owned mutation lock for every future
   operation that could write either engine.

Completed, immutable run artifacts may be read while a new run is active. The
dedicated active-progress projection may also read only the exact scheduler
attempt, timestamped run-log prefix, active-run manifest/action pointer, and
allowlisted aggregate progress/scoring artifacts while the scheduler and
pipeline locks prove that attempt is active. A live workspace CSV snapshot must
wait until the scheduler, pipeline, workbook, current-queue, and adapter mutation
locks are free. If they are not, return the last run-scoped report, mark mutable
workspace projections `busy`, and keep active progress separate.

An exit code of zero from a scheduler check is not proof that a run happened; it
may mean not due or already attempted. Run success comes only from the evidence
contract in [RUN_EVIDENCE_CONTRACT.md](RUN_EVIDENCE_CONTRACT.md).

The exact action-queue pointer is also a structural verification boundary. Each
of `application_plus_outreach`, `application_only`, `outreach_only_today`,
`relationship_buffer`, `follow_up`, and `skipped_internal` must be an array of
objects, and the matching value under `counts` must be a non-negative integer
equal to that array's length. The adapter derives `decision_total_parts` and
`decision_total` from the validated lengths and labels the total
`validated_action_queue_lane_entries`. It does not infer cross-lane identity
exclusivity. Any missing, malformed, or contradictory lane rejects the terminal
run; active progress simply omits the invalid action-queue projection.

## Normalized local API

The companion binds to loopback by default and authenticates every protected
request. The canonical UI is served from the same origin and uses a host-only,
restart-stable HttpOnly local cookie plus an explicit same-origin request header;
it does not repeatedly pair or expose the local bearer to JavaScript. Hosted
clients use an expiring web bearer after one-time pairing. Both receive
aggregates and status, not direct filesystem access.

The implemented routes are rooted at `/api/v1`, including:

- `GET /api/v1/local-ui/bootstrap` for guarded, cookie-authenticated local UI
  bootstrap and safe server detection;
- `GET /api/v1/existing-engine/status` and `/snapshot` for normalized state;
- `GET /api/v1/operator/overview` for capabilities, assets, reviews, jobs,
  progress, the next-run plan, and the account-tracker surface;
- `GET /api/v1/operator/progress` for lightweight high-frequency exact-run and
  recent-job polling without building the full overview;
- `GET /api/v1/operator/reports/<run-id>/html` for one verified exact report;
- fixed `/api/v1/operator/jobs` and `/api/v1/operator/reviews` workflows for the
  named operator contracts.

The abbreviated `/v1/...` objects below describe the adapter's normalized
conceptual schema; the HTTP implementation uses the `/api/v1/...` prefix.

### `GET /v1/engine/capabilities`

```json
{
  "schemaVersion": "1.0",
  "mode": "existing",
  "dataClass": "local-private",
  "productionGuard": "valid",
  "roots": {
    "resumeEngine": "configured",
    "outreachEngine": "configured"
  },
  "mutationsEnabled": false,
  "allowedCommands": [
    "engine.capabilities.read",
    "engine.status.read",
    "run.list",
    "run.read",
    "snapshot.read",
    "production.preflight"
  ]
}
```

### `GET /v1/engine/status`

```json
{
  "schemaVersion": "1.0",
  "mode": "existing",
  "busy": false,
  "locks": {
    "scheduler": "free",
    "pipeline": "free",
    "workbook": "free",
    "adapterMutation": "free"
  },
  "latestTerminalRunId": "run-YYYYMMDD-HHMMSS"
}
```

### `GET /v1/runs` and `GET /v1/runs/{runId}`

Run responses use the normalized evidence object defined in
`RUN_EVIDENCE_CONTRACT.md`. Absolute paths are replaced with repository-relative
tokens and hashes.

### `GET /v1/snapshot`

```json
{
  "schemaVersion": "1.0",
  "mode": "existing",
  "dataClass": "local-private",
  "generatedAt": "ISO-8601 timestamp",
  "runSnapshot": {
    "runId": "run-YYYYMMDD-HHMMSS",
    "scope": "run-scoped",
    "status": "complete"
  },
  "currentWorkspace": {
    "scope": "current-snapshot",
    "status": "available"
  },
  "metrics": {},
  "sources": [],
  "queue": [],
  "applications": [],
  "outreach": [],
  "runs": [],
  "reports": []
}
```

Run-scoped and current-snapshot values must remain separate in the response and
the UI. Current aliases must never overwrite historical run evidence.

## Primary operator projections

`GET /api/v1/operator/overview` returns operator assets schema `1.1`. Three
projections make the local UI usable as the primary operating surface without
weakening evidence boundaries.

### Current run progress

`current_run_progress` selects an active run only when the pipeline lock is
owned and the scheduler lock plus scheduler attempt state bind one exact run ID.
It may then project:

- a phase inferred from an append-only snapshot of that run's timestamped log;
- allowlisted search-complete and extracted totals from a same-window LinkedIn
  progress checkpoint;
- scoring attempted/error/accepted totals from the exact scored-artifact pointer
  in the active log;
- source-family and action-queue aggregates from an exact active-run manifest;
- hashes, sizes, and repository-relative evidence tokens for those minimized
  inputs.

It never returns raw log text, search terms, job cards, URLs, message content, or
the private upstream browser-owner marker. If lock ownership changes during
capture, current evidence is discarded and the result is `partial`. When idle,
the lightweight progress endpoint checks terminal summary candidates
newest-first and stops at the first fully verified projection. It does not call
the complete history scan on each poll; the full overview/history may still scan
all runs.

If scheduler state proves that a newer completed actual-pipeline attempt exists
but its exact summary, manifest, source/action pointers, and Outreach report do
not verify, `current_run_progress` returns that exact attempt as noncurrent
`attention` instead of showing the previous run forever. Its scheduler evidence
is restricted to a derived run ID, bounded start/completion/capture timestamps,
and a generic missing-chain or nonzero-exit reason. Raw scheduler status details,
paths, and rejection text are not projected.

The upstream engine owns its dedicated LinkedIn Chrome process through a
per-run marker and terminal cleanup. The adapter does not scan arbitrary Chrome
windows or equate a visible Playwright process with a run. A LinkedIn phase is
reported only from the exact active-run evidence above.

### Next-run plan

`next_run_plan` is a maximum-30-row, `scope: derived-plan` projection. Its basis
is the latest fully verified terminal run plus the current durable operator
review ledger. It prioritizes explicit failed/timed-out/not-reported sources,
then exact action-queue lanes and pending/reviewed/approved review work. Every
item carries a category, priority, reason, count, basis run, and evidence binding.

While a newer run is active the plan status is `partial`, and its reason states
that the prior exact run remains the basis. It rebases only when the new summary,
manifest, source metrics, action queue, and Outreach report verify. It does not
write a task table or invent recommendations from mutable current files.

### Account tracker

`account_tracker` is a nontransactional `stable-at-capture` aggregate. The
companion timestamps and fingerprints inputs, probes all five locks before and
after, and revalidates every bounded identity/hash without ever owning an
upstream lock; this prevents a UI read from breaking nonblocking production
writers. Any change discards the entire mutable bundle. It may contain account/action/due counts, allowlisted
tier/stage/action-type mappings, activity totals, people-mapped count, score
summaries, and evidence metadata. The existing bounded Action Queue rows remain
under the workbook projection.

Its `open_action` advertises the fixed `open.account_tracker` capability,
confirmation phrase, and availability. The companion resolves the server-owned
allowlisted workbook path and calls the platform opener; the HTTP client cannot
provide a path. A busy or unsafe workbook fails closed.

## Snapshot source schemas

The application workbook's `Jobs` and `Archive` sheets use:

```text
id,date_found,date_posted,company,role_title,role_type,location,url,url_hash,
source,fit_score,fit_rationale,status,date_applied,folder_path,jd_text,notes
```

The `ReviewCache` sheet uses:

```text
cache_key,url_hash,tc_hash,url,company,role_title,source,decision,category,
fit_score,fit_rationale,notes,search_term,time_window,date_reviewed
```

Current application queue rows expose identifiers, company and role labels,
scores, status, source/run provenance, queue bucket, rank, and folder readiness.
An application counts as submitted only when its tracker status is `applied`; a
generated folder is not submission evidence.

Outreach CSV headers are:

```text
organizations: organization_id,name,organization_type,target_lists,status,city,
website,linkedin_url,source_kind,source_url,discovered_at,last_updated_at,notes

contacts: contact_id,organization_id,full_name,title,contact_type,target_lists,
preferred_channel,status,linkedin_url,email,source_kind,source_url,discovered_at,
last_contacted_at,notes

touchpoints: touchpoint_id,organization_id,contact_id,channel,status,message_kind,
message_text,recorded_at,sent_at,source_artifact,notes

opportunities: opportunity_id,organization_id,title,opportunity_type,target_lists,
location,status,source_kind,source_url,discovered_at,compensation_hint,notes

sources: source_id,label,source_kind,base_url,extraction_method,owner,last_run_at,
notes
```

The local adapter may use these fields to calculate a private snapshot. A public
deployment must receive only reviewed aggregates or fictional demo data.

## Existing versus portable mode

Existing-engine mode is an adapter over installed repositories, their release
guard, local scheduler, browser session, private workspace, and source-specific
policies. It reuses upstream decisions and reports instead of recreating them.

Portable mode is a different product state:

- it starts with companion-owned storage and no upstream repositories;
- every external source begins as `not_configured`, not `healthy` with zero rows;
- browser automation, generation, and delivery are disabled until configured and
  reviewed separately;
- manual imports and lawful public adapters may be enabled independently;
- no person-specific network, school, role, company, style, or message defaults
  are inherited;
- the scheduler and attestation capabilities are reported as unavailable unless
  portable-native equivalents actually exist;
- fictional preview fixtures remain labeled `fictional-demo`, are never combined
  with local-private metrics, and are not rendered on an unpaired operational route.

Both modes may implement the same normalized API. They must not claim the same
capabilities or evidence.

## Dated validation evidence

On 2026-07-11, the zero-mutation production preflight returned `valid` for clean,
attested main branches. The attestation referenced 60 passing tests for the
application/resume engine and 482 for the outreach engine, plus release-tree
checks. This is dated release evidence, not a live health claim; clients must run
preflight again before any privileged action.

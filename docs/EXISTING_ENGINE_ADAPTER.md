# Existing-engine adapter

## Purpose

The existing-engine adapter is a local, read-first bridge between the product
companion and an already installed recruiting engine. It does not replace the
engine's discovery, scoring, review, generation, or delivery logic. It exposes
normalized status and run evidence while keeping private operating data on the
user's machine.

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

## Zero-mutation allowlist

The public adapter surface is deny-by-default. Version 1 may perform only these
operations without a separate human authorization flow:

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
artifacts or use browser/network state. They belong behind a separate restricted
policy if added later.

## Human-gated non-allowlist

The following are not part of the public allowlist:

- an arbitrary command, path, environment override, or CLI flag;
- a forced nightly run or a direct Daily Engine/pipeline invocation;
- any flag containing `--execute`, `--execute-sends`, `--send-linkedin`,
  `--generate`, `--force`, or `--promote-approved`;
- LinkedIn invites, follow-ups, inbox replies, or other browser delivery;
- SMTP delivery, even when a draft exists;
- live browser capture, account mapping, or contact-information research;
- relationship imports, company promotion, or tracker status changes;
- disabling the Track 2 outer timeout;
- automatic retry after a timeout or an uncertain delivery result.

A future privileged action API must use named server-owned commands, bounded
limits, an explicit confirmation token, a valid production preflight, and the
lock discipline below. It must never accept raw shell input.

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

Completed, immutable run artifacts may be read while a new run is active. A live
workspace CSV snapshot must wait until the scheduler, pipeline, workbook, and
adapter mutation locks are free. If they are not, return the last run-scoped
report and mark the current snapshot `busy`.

An exit code of zero from a scheduler check is not proof that a run happened; it
may mean not due or already attempted. Run success comes only from the evidence
contract in [RUN_EVIDENCE_CONTRACT.md](RUN_EVIDENCE_CONTRACT.md).

## Normalized local API

The companion should bind to loopback by default and authenticate every request.
The hosted product receives aggregates and status, not direct filesystem access.

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

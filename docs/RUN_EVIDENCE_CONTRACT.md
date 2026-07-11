# Run evidence contract

## Goal

A recruiting decision is trustworthy only when its dashboard can identify the
exact run, exact inputs, exact actions, and exact report that produced it. This
contract prevents a common failure mode: combining a current workspace file with
an older run and presenting the result as one coherent execution.

The contract applies to existing-engine mode. Portable mode may emit the same
normalized response, but only from evidence it actually owns.

## Evidence classes

| Class | Meaning |
|---|---|
| `run-scoped` | Immutable evidence selected through one nightly summary. |
| `current-snapshot` | A point-in-time view of mutable queues or tracker tables. |
| `portable-native` | Evidence produced by the companion's portable workflow. |
| `fictional-demo` | Preview-only data that must never be mixed with real metrics. |

Every metric, source row, queue item, action, and report returned by the adapter
must carry or inherit one of these classes.

## Authority chain

### 1. Select one nightly summary

Enumerate only:

```text
discovery/source_validation/<run-id>-nightly-pipeline-summary.json
```

Select by parsed `created_at` and normalized `run_id`, not filesystem modification
time. The filename and payload run identifiers must agree. The payload must have
a terminal status and a `failures` array.

If a pipeline lock is held, the newest terminal summary remains the latest
completed run. Report the engine as busy rather than inventing a partially
complete summary.

### 2. Follow the summary's exact Daily Engine manifest

The only valid manifest is the readable file named by the summary's
`daily_engine_manifest` field. A missing or unreadable pointer makes the run
failed. Never select the newest manifest by glob, timestamp, or modification
time.

The manifest must satisfy:

- `manifest_schema` is `resume_generator.daily_engine_run_manifest`;
- its `run_id` matches the selected summary;
- its terminal `status` and numeric `returncode` are present;
- `source_metrics` and `action_queue` resolve to readable files for this run;
- `source_families` contains explicit statuses and counts;
- typed arrays for invite, follow-up, reconciliation, Track 2, and email
  artifacts are present, even when empty;
- `app_invites`, `track_2`, and `email_channel` preserve failures, blockers,
  planned counts, actual counts, and unresolved delivery states.

Missing typed fields are schema failures, not permission to inspect a loose
artifact elsewhere.

### 3. Resolve Track 2 through the summary

The authoritative Track 2 pointer is:

```text
outreach_maintenance.track_2_daily_run_artifact
```

The finalized Daily Engine manifest may augment this with typed phase results,
phase artifacts, planned-versus-actual counts, and email state. A command's exit
code is insufficient if the run artifact is missing.

Timeout, partial-send, unknown-send, and reconciliation-required states must be
preserved exactly. They must never be normalized to success, and the adapter must
not offer automatic retry.

### 4. Resolve the run report through the summary

The authoritative report pointer is:

```text
outreach_daily_report.summary_artifact
```

The report JSON must have:

- `report_mode` equal to `run_scoped`;
- `nightly_summary` resolving to the selected summary;
- `since` matching the summary's run window;
- source breakdown, stage metrics, workspace counts, action totals, failures,
  and exact artifact references for that run.

Use the timestamped `html_report_artifact` for historical display. A mutable
`daily_run_report` or `daily_html/daily_run_report.html` alias is a convenience
pointer, not historical evidence.

### 5. Add current workspace state separately

These are mutable snapshots:

- `apps/Apply queues/current_apply_queue/manifest.json`
- `apps/Apply queues/current_apply_queue/priority_order.json`
- `discovery/jobs.xlsx`
- Outreach workspace CSVs
- account-tracker exports
- shared-queue current aliases

They may be returned under `currentWorkspace`, with `capturedAt`, hashes, and
`scope: current-snapshot`. They must not change the selected run's metrics.

## Source and action semantics

Source states are explicit:

- `ran`: the source executed and reported its counts;
- `skipped`: the source was intentionally omitted in this run;
- `failed`: the source attempted and failed;
- `timed_out`: the source exceeded its deadline;
- `not_reported`: the expected source family is absent from otherwise valid
  evidence;
- `not_configured`: the capability does not exist in this installation.

Zero is a count, not a state. `ran` with zero observations differs from
`skipped`, `failed`, and `not_configured`.

Delivery states must distinguish at least:

- planned or drafted;
- approved;
- sent;
- failed;
- unknown with a durable reservation;
- reconciliation required;
- blocked by review, credentials, cadence, or company-identity evidence.

A draft is not a send. An attempted action without a committed result is not a
successful action.

## Path and integrity validation

For every pointer:

1. Resolve it relative to its configured repository root when not absolute.
2. Canonicalize it without allowing traversal outside the configured roots.
3. Require a regular readable file.
4. Record its SHA-256 and size before parsing.
5. Reject duplicate JSON keys and unsupported schema versions where the upstream
   artifact defines a version.
6. Validate run identifiers and parent-child bindings.
7. Return a repository-relative token and hash to clients, not the absolute path.

The adapter must not accept a filesystem path from an HTTP client. Clients select
a run identifier; the server resolves its allowlisted evidence.

## Concurrency and snapshot consistency

Lock-file existence is not a busy signal because advisory lock files persist.
The adapter must test non-blocking ownership.

- Completed run artifacts may be read while a later run is active.
- Mutable application/workbook state must not be read across a workbook write.
- Mutable Outreach CSVs must not be read while any engine or adapter mutation is
  active because the upstream tables do not have one global transaction lock.
- If a stable current snapshot cannot be obtained, return the latest run-scoped
  report and mark `currentWorkspace.status` as `busy`.
- The adapter must serialize every future cross-engine mutation under its own
  exclusive lock.

## Normalized run object

```json
{
  "schemaVersion": "1.0",
  "runId": "run-YYYYMMDD-HHMMSS",
  "mode": "existing",
  "scope": "run-scoped",
  "status": "attention",
  "startedAt": "ISO-8601 timestamp",
  "completedAt": "ISO-8601 timestamp",
  "failures": [],
  "evidence": {
    "summary": {
      "state": "valid",
      "path": "discovery/source_validation/<run-id>-nightly-pipeline-summary.json",
      "sha256": "hex digest"
    },
    "dailyManifest": {
      "state": "valid",
      "path": "discovery/source_validation/<run-id>-daily-engine-run-manifest.json",
      "sha256": "hex digest"
    },
    "outreachReport": {
      "state": "valid",
      "path": "workspace/reports/<run-id>-daily-run-report.json",
      "sha256": "hex digest"
    }
  },
  "sources": [],
  "application": {
    "selected": 0,
    "generated": 0,
    "submitted": 0,
    "inviteActions": {}
  },
  "outreach": {
    "planned": 0,
    "actual": 0,
    "inviteActions": {},
    "followupActions": {},
    "email": {}
  },
  "report": {
    "status": "valid",
    "htmlPath": "workspace/reports/daily_html/<run-id>-daily-run-report.html"
  }
}
```

`status` is derived from evidence, not process appearance:

- `complete`: all required evidence is valid and no failure is recorded;
- `attention`: evidence is complete, but one or more stages were skipped,
  blocked, timed out, or partially failed;
- `failed`: a required pointer, schema binding, or finalization step is invalid;
- `running`: emitted only by live lock/process status, never inferred from a
  stale summary.

## Dashboard projection

The run report is the preferred source for run-scoped dashboard aggregates. Its
projection may include:

- workspace entity counts captured at finalization;
- source status, raw counts, advanced counts, and details;
- stage runtimes and failures;
- application queue and generation counts;
- invite and follow-up totals;
- review and open-action counts;
- company discovery, role-surface, cadence, and outcome summaries.

The action queue supplies run-scoped lanes such as selected applications,
apply-plus-outreach, application-only, relationship work, follow-up, review, and
skipped items. The current apply queue may provide current readiness, but it must
remain labeled as a current snapshot.

Raw message bodies, recipient details, contact records, credentials, browser
state, and private document content are outside the normalized dashboard
contract.

## Fail-closed examples

- A summary exists but its manifest pointer is missing: return `failed`.
- A newer standalone manifest exists: do not attach it to an older summary.
- The report points at a different summary: return `failed`.
- A source is absent: return `not_reported`, not `ran` with zero.
- A send timed out after reserving a target: return `reconciliation_required` and
  do not retry.
- The workspace is being written: return the prior run snapshot and
  `currentWorkspace.status: busy`.
- Only a mutable HTML alias exists: omit historical HTML rather than presenting
  it as the selected run.

These rules favor an explicit incomplete picture over a polished but false one.

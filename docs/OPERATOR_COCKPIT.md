# Private operator cockpit

The operator cockpit makes the hosted Recruiting Engine interface the front door
to the existing local ResumeGenerator and Outreach systems. It is not a second
tracker and it does not copy private workspace data into GitHub Pages.

The browser pairs directly with the loopback companion. The companion projects a
bounded, versioned view of the installed systems and exposes only named local
actions from a fixed registry.

## Connected systems

| Cockpit surface | Installed source of truth | What the UI projects |
|---|---|---|
| Accounts | Outreach `organizations.csv`, `opportunities.csv`, `contacts.csv`, `touchpoints.csv`, and `sources.csv`; `account_tracker.xlsx` is derived | Portfolio counts, tier/stage mix, bounded action queue, workbook availability |
| Apply queue | ResumeGenerator `current_apply_queue/manifest.json` and `priority_order.json` | Ranked company/role rows, fit/priority, state, material readiness, truncation, and item-specific action guards |
| Application history | ResumeGenerator `discovery/jobs.xlsx` (`Jobs` live, `Archive` history) | Status/source/role aggregates without serving the workbook or private answer bank |
| Stories | Career Workbench, story sources, and story bank | Curated filenames/categories and inventory counts; the filename-classified story count may include drafts and is not presented as canonical |
| Communications | Exact nightly manifest plus its bound invite, follow-up, and email draft artifacts | Minimized review counts in overview; exact recipient, context, subject, and draft only from the authenticated selected-target detail endpoint |
| Reports | One completed nightly summary and the exact manifest, source metrics, action queue, and Outreach report it names | Run-scoped status plus the authenticated full HTML artifact in a scriptless, remote-subresource-disabled iframe; zero, skipped, timed-out, and failed sources stay explicit |
| Operations | Production release attestation, scheduler/pipeline/workbook/queue locks, companion mutation lock, and local review ledger | Capability availability, review/approval state, fixed confirmations, and audit trails for reviews and jobs |

Existing mode resolves the persisted mode preference before it requests a read
model. It never calls the portable dashboard endpoint, whose richer DTO can
contain recipient and message fields. The cockpit requests only the minimized
operator overview, existing-engine status, and exact evidence snapshot.

## Operator actions

Actions are not arbitrary command strings. Each action has:

1. a fixed command identifier;
2. a server-owned working directory and argument list;
3. an exact parameter schema, when a selected queue item is required;
4. a typed confirmation phrase;
5. production-lock and path-containment checks;
6. a companion-wide mutation lock;
7. a durable audit job that stores return codes and output hashes, never raw stdout.

The intended operating set covers local opening of the account tracker, apply
queue, exact report, story workbench, communication review, and a selected
application folder; production preflight; account/report/comms refreshes;
review-only Track 2 planning; one-job resume generation; and apply-assist packet
creation. The narrower reviewed-action registry additionally covers one safe
nightly cycle, one LinkedIn delivery, one SMTP delivery, one application
fill-to-review lane, and one archive-first applied/closed transition. The browser
lane is deliberately non-executable until its runner can enforce the final-Submit
boundary at the tool level; a prompt instruction is not sufficient.

Long-running or paid generation returns an audit job immediately and continues
locally. The UI polls status instead of keeping a browser request open for the
duration of a model run.

Queue buttons are also fail-closed per row. A globally registered command is not
enough: the selected row must carry an available action, exact job identifier,
reason, and confirmation phrase from the companion projection.

## Consequential review ledger

Nightly, delivery, browser-fill, and terminal application transitions use a
separate durable review state machine:

1. the companion projects an opaque target from an authoritative source;
2. selecting it fetches private detail from an authenticated, no-store endpoint;
3. the user records review with `REVIEW_EXACT_TARGET`;
4. the user separately approves with `APPROVE_EXACT_TARGET`;
5. a final command-specific phrase is required before an executable capability;
6. approval is consumed before process spawn, so replay is impossible and an
   uncertain result requires reconciliation or a new review.

Every approval expires after 24 hours and binds one item only. It stores separate
hashes for the immutable source and final reviewed subject/body—not
caller-supplied paths, indexes, flags, or environment. LinkedIn and email content
can be edited only through the dedicated exact-review endpoint; any edit resets
the state to pending, clears approval, refreshes expiry, and requires review and
approval again. A changed file, recipient, LinkedIn thread/latest inbound,
subject, message, application material, or release attestation makes the earlier
review stale or invalid.

Before invoking the upstream LinkedIn approval materializer, and immediately
before consuming any reviewed LinkedIn, SMTP, or lifecycle approval, the
companion reruns the same fixed production-attestation preflight.
If an upstream protected file, Git head, or attestation changed, execution stops
and the approval remains unconsumed. The database also enforces one active
pending/reviewed/approved row per exact target, so concurrent staging requests
cannot mint parallel approvals.

The safe-nightly target fingerprints the complete server-owned pipeline argument
vector, the wrapper argument vector, `nightly_prompt.py`,
`run_nightly_pipeline.py`, and the production attestation. Its fixed pipeline
configuration keeps discovery, generation, outreach preparation, and the Track 2
planning pass, sets the legacy send target to zero, and omits
`--execute-sends`, `--track-2-send-linkedin`, and
`--execute-linkedin-followups`. Immediately before consumption, the companion
runs the fixed `--production-check-only` preflight. A dirty or mismatched release
leaves approval unconsumed. After a successful preflight, approval is consumed,
the companion mutation lock is released, and only then is `nightly_prompt.py`
spawned so its scheduler/pipeline lock order cannot deadlock.

The top-bar **Run E2E** control opens this exact nightly target directly; it does
not bypass the review ledger. The user still stages the target, records review,
approves it, and enters `RUN_REVIEWED_NIGHTLY` before the fixed no-delivery
process can start. **Refresh** remains a separate read-only control.

The general operator overview never contains raw profile URLs, email addresses,
recipient names, subjects, message bodies, or thread context. Those fields are
returned only for one selected target through
`GET /api/v1/operator/review-targets/<opaque-id>/detail` after authentication.

Outreach review targets are resolved only through the newest fully verified run
projection and that run's exact Daily Engine manifest. LinkedIn invite candidates
must come from a Track 2 phase artifact explicitly named by the manifest, remain
undelivered, meet the recorded score/QC gates, and bind one canonical profile URL
and note. Follow-ups bind contact, thread, draft kind, latest inbound context, and
exact draft. Email binds organization/contact, exact address, subject, and body.
Loose `latest` files, modification-time discovery, send-result artifacts, paths
outside the installed Outreach root, and symlinks are rejected.

LinkedIn approval materialization invokes only the installed
`outreach.reviewed_linkedin` preview and approve contracts, then executes the
result by approval SHA. Completion requires an exact replay-protected receipt;
blocked, unknown, missing, or contradictory receipts are reconciliation states,
even when the process exits zero. Email writes one private draft and approval
CSV and invokes only the fixed `send-track-2-emails --limit 1 --execute` command.
Its exit code is not delivery proof: completion requires the exact result
artifact to bind the private draft and report one result, `sent: 1`, and
`delivery_status: sent`. A valid held/failed result is reported as not sent;
missing or contradictory evidence requires reconciliation.

Apply assist is projected as a blocked lane only. The installed runner can place
`stop_before_submit: true` in a prompt, but it cannot intercept a Submit action
at the browser-tool boundary and a 2xx response does not prove that the review
screen was reached. The cockpit therefore never invokes its live mode. Re-enable
only after all outbound task inputs are hash-bound and the runner produces an
authoritative terminal receipt behind a tool-enforced final-submit block.

The application lifecycle capability is narrower than a generic tracker edit.
After review, the applied and closed actions invoke the fixed upstream
`transition_application.py` contract for exactly one job while holding the
companion mutation lock. The upstream transaction archives generated materials
before refreshing the queue and rolls back on failure. Closed maps to the
upstream `not-applied` lifecycle state. No generic status string is accepted.

## Deliberate boundaries

The cockpit still does not expose:

- arbitrary shell commands, paths, flags, or environment variables;
- the active scheduler's nightly argument string, because it includes delivery;
- unreviewed or multi-recipient LinkedIn/SMTP execution; each installed executor
  is limited to one exact approved target and must produce authoritative outcome
  evidence;
- live application browser automation or final submission; the current runner
  cannot technically enforce a stop before Submit, so its lane remains blocked;
- raw contacts, email addresses, message bodies, answer-bank payloads, or private
  interview-prep content;
- blind status edits; only the reviewed, artifact-preserving applied/closed
  lifecycle contract may run.

Review lanes remain visible when execution is unavailable so the missing contract
is explicit rather than hidden. An action becomes runnable only when both its
exact review gate and installed fixed-argument contract report ready.

## Concurrency and reporting

Current Outreach CSV writers do not share a global process lock. Therefore the
cockpit fails closed for writes while any upstream production lock is busy and
serializes every cockpit mutation behind its own advisory lock.

An attestation reported as `configured` means only that the release-attestation
path is configured. The UI does not call that state verified; production
preflight is the explicit action that performs validation.

Reports never select independently named `latest` files. The nightly summary is
the run root. The source metrics, decision queue, daily report, and HTML report
must all be the exact artifacts bound to that run before the UI labels them
verified.

`GET /api/v1/operator/reports/<run-id>/html` is web-session authenticated and
accepts only a fully verified exact run. It resolves the expected run-named file
under `Outreach/workspace/reports/daily_html`, rejects aliases, symlinks,
traversal, stale size/hash evidence, and files over 5 MiB, and returns no-store
JSON. The browser injects a deny-by-default document CSP and renders the report
without scripts, forms, navigation, or network access.

Bounded projections display their returned and total row counts and label
truncation. The local workbook or workbench remains the route to the complete
dataset.

## Start it on this Mac

See [OPERATOR_SETUP.md](OPERATOR_SETUP.md) for the macOS LaunchAgent, one-time
pairing, inspection, and uninstall flow. The service is loopback-only and uses
the sibling `ResumeGenerator v1` and `Outreach` directories by default.

After pairing, choose **Private operator cockpit** in Settings. A first-use
operator service defaults to that mode; an existing persisted preference still
wins.

Pairing tokens are one-time exchanges. A successful tab stores only the returned
12-hour web session in `sessionStorage`; the Settings screen then shows
**Connected** and does not attempt to exchange a pasted `re_pair_` token again.
Disconnect the tab before generating and using another pairing token.

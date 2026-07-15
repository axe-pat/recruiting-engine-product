# Primary local operator UI

This is the v1.3 operating contract for the single-user, local-first product.

The canonical operator surface for the current single-user product is:

```text
http://127.0.0.1:8765/app/
```

Launch it with `scripts/open-operator-cockpit.sh`. After that one explicit local
activation, use the URL normally on this Mac. The local companion serves the
generated application and the API from the same loopback origin, so its
HttpOnly cookie stays connected across tabs, browser restarts, and companion
restarts without asking for a pairing token. The hosted GitHub Pages build remains available for
the product story and a later public or portable workflow; its pairing flow is
not the primary operator path.

## Connection model

Generated HTML is public presentation content and never mints a privileged
cookie. The launcher validates the private bearer state, creates a hash-only,
single-use activation ticket that expires within two minutes, captures it
without printing it, and opens `/local-activate/` with the ticket in the URL
fragment. The activation page clears the fragment from history before a strict
same-origin POST establishes a host-only local UI cookie. The cookie is
`HttpOnly`, `SameSite=Strict`, has `Path=/`, and has a
one-year maximum age. Its value is a server-derived credential, not the local
bearer token, and browser JavaScript cannot read it. It remains valid across
companion restarts while the underlying private local bearer remains unchanged.

Cookie-authenticated API calls must also carry the UI's explicit local-request
header and same-origin browser evidence. The companion rejects a non-loopback
Host, a cross-origin request, a missing guard header, and attempts to use the UI
credential as a bearer. The local UI cannot reveal or rotate the long-lived
bearer. `GET /api/v1/local-ui/bootstrap` requires a valid cookie; it may refresh
that cookie and returns only the local mode, relative API base, authentication
state, and companion version. Without a cookie it returns `401`, the safe local
server detection header, and no `Set-Cookie`.

Hosted pages continue to use a one-time pairing code and a short-lived,
tab-scoped web session. The Chrome companion continues to use its local bearer.
Those are separate clients and neither is required to reopen the canonical local
UI. When a hosted operational tab has a previously configured loopback origin
but no usable session, it performs one credential-free health/version probe and
hands off to `/app/` only after the expected local companion responds. Browsers
that block the probe under private-network or mixed-content policy keep a
prominent direct local-cockpit link instead. Neither path places a hosted token
in the destination URL.

## What it operates

The local UI is a projection and control plane over three installed systems:

| Surface | Authority | Local UI behavior |
|---|---|---|
| ResumeGenerator | discovery workbook, current apply queue, scheduler, pipeline, exact run artifacts, and tailored materials | Shows minimized queue/history state, guarded per-job actions, active run progress, and immutable reports |
| Outreach | organizations, opportunities, contacts, touchpoints, sources, account tracker, Track 2 artifacts, and communication evidence | Shows aggregate account/outreach state, exact review targets, guarded delivery, and a desktop-open action for the full workbook |
| Product companion | authentication, operator review ledger, fixed command registry, job audit, and UI projection | Enforces confirmations, locks, path containment, exact evidence, and no arbitrary commands |

The UI never becomes a second tracker. Run-scoped evidence stays separate from
mutable workbook and queue snapshots, and private report or message content is
requested only for one authenticated exact target.

## Build and install

The companion serves only the promoted, integrity-verified `static-export/`; it
does not build the web application at startup. From the product repository root:

```bash
npm install
npm run export:static
scripts/probe-operator-companion.sh --production-preflight
scripts/install-operator-companion-launch-agent.sh --production-preflight
```

Then verify and open the primary surface:

```bash
curl --fail --silent http://127.0.0.1:8765/api/v1/health
scripts/open-operator-cockpit.sh
```

After a UI change, `npm run export:static` builds a private generation, hashes
every file into `static-integrity.json`, validates the complete tree with the
companion, and atomically publishes it as `static-export.staged/`. It never
rewrites the directory the running UI serves. Re-run the installer: after the
restart interlock has stopped the old service, it validates the staged tree
again, promotes it, validates the promoted path, and starts the new service. A
failed bootstrap restores the prior static generation before the old service is
reloaded. If a replacement process cannot be stopped, rollback deliberately
leaves its validated generation in place so a running process never sees an
incompatible tree. The first install uses the same promotion path with no prior
generation. Generated live, staged, and rollback directories are ignored by
Git; commit source and scripts, not generated files.

The server retains the validated file inventory and checks the requested file's
size and SHA-256 while reading it. Any out-of-band post-start mutation fails
closed with `503`; unvalidated bytes are never returned.

## Everyday operating flow

1. Open `/app/` and confirm the header says **This Mac · always connected**.
2. Check the live run card. If a scheduled or cockpit run is already active, do
   not start a duplicate.
3. Review `/app/plan` for the evidence-grounded work that should happen before
   the next cycle.
4. Use `/app/queue`, `/app/accounts`, `/app/stories`, and `/app/outreach` for
   current work. Use guarded desktop-open actions when the complete local
   workbook or workbench is needed.
5. Use **Run E2E** only after reviewing its exact production target and delivery
   contract. Follow the run in `/app/runs`, then inspect its exact report in
   `/app/reports`.

### Live run progress

The operator overview exposes `assets.current_run_progress`. While the scheduler
and pipeline locks prove an active run, the companion binds progress to the
exact scheduler attempt, timestamped run log, active-run manifest/action queue,
and exact-parent-bound LinkedIn progress or scoring artifacts. While a scheduled
run or cockpit job is active, the UI polls the lightweight authenticated
`GET /api/v1/operator/progress` endpoint every four seconds. That endpoint skips
workbooks, stories, report history, source history, and the next-run plan. The UI
shows the run ID, phase, timestamps, discovered/kept/review counts, and the
number of bound artifacts. It does not display raw log lines, searches, URLs,
cards, or private report content.

When no run is active, the card checks terminal summary candidates newest-first
and stops at the first fully verified projection instead of rescanning the full
history every four seconds. If scheduler state records a newer completed actual
pipeline attempt but its exact summary/report chain does not verify, the card
shows that attempt as noncurrent `attention`, with its run ID, bounded timestamps,
and a generic verification or nonzero-exit reason. It never falls back silently
to the previous run or exposes raw scheduler rejection text. The full overview
and history may still scan all verified runs. If a lock changes during capture
or an exact active artifact cannot be validated, the projection is `partial` or
`attention`; it does not manufacture progress.

The upstream LinkedIn browser uses a per-run ownership marker and terminal
cleanup that targets only that run's dedicated Chrome process. The marker itself
is private and is never projected. A normal personal Chrome window is not run
evidence, and the companion labels a LinkedIn phase only when the active
scheduler/pipeline attempt and its bound artifacts support that conclusion. Do
not kill a suspected run-owned Chrome process while a run is active. The
upstream contract expects ownership-scoped terminal cleanup, but the active UI
does not prove a PID or completed cleanup; verify terminal cleanup evidence
before intervening.

`/app/sources` labels its rows as exact manifest source-family metrics. The
separately pointed source-metrics file is still required to pass terminal-chain
verification, but the overview does not mislabel that richer object as the data
it displays. Report and review-ledger windows publish true total/returned and
truncation metadata.

### Next-run plan

`/app/plan` renders `assets.next_run_plan`, a bounded derived plan with at most
30 items. It is grounded in the latest fully verified exact run and the current
durable review ledger. Source failures or timeouts come first, followed by exact
action-queue lanes and pending/reviewed/approved operator work. Every row keeps
its evidence category and basis run.

If tonight's run is still active, the plan is intentionally `partial`: it uses
the previous terminal run plus current durable reviews and says so. It rebases
after the new summary, manifest, source metrics, action queue, and Outreach
report pass terminal verification. It is not a free-form or invented task list.

### Account tracker

`/app/accounts` shows a nontransactional `stable-at-capture` aggregate of the current account tracker:
account and action counts, due-now and due-bucket counts, tier/stage/action mix,
activity totals, people mapped, and score summaries. The existing bounded action
queue remains visible. The full editable workbook remains the source of truth.

**Open account tracker** invokes only the server-owned allowlisted workbook path,
after the UI presents the fixed confirmation. The client cannot provide a path
or substitute a file. The companion never holds upstream locks during a UI read;
it probes them before and after, fingerprints every bounded mutable input, and
revalidates identities and hashes at the end so it cannot disrupt nonblocking
production writers. A busy lock or changed fingerprint discards every mutable
projection together, and the open action explains its current availability.

### Production nightly semantics

**Run E2E** is a reviewed off-cycle production nightly, not a report-only or
no-send test. One click stages the exact target, records review and approval
through the durable ledger with the fixed confirmation phrases, and submits the
`RUN_REVIEWED_NIGHTLY` job bound to that approval — the same audited state
machine, driven automatically. Execution reuses the attested canonical nightly
argument vector, including bounded application-queue delivery and Track 2
LinkedIn delivery. Email remains separately recipient-reviewed.

The companion performs a fresh production preflight immediately before
consuming approval. Completion requires exactly one new healthy summary,
manifest, source metrics file, action queue, and Outreach report that prove the
expected delivery mode. Exit code zero alone is not success. Final application
submission remains human-owned, and no generic multi-recipient send or arbitrary
shell surface exists.

## External-agent handoff

An agent taking over this surface should follow this order:

1. Read this file, [OPERATOR_COCKPIT.md](OPERATOR_COCKPIT.md),
   [EXISTING_ENGINE_ADAPTER.md](EXISTING_ENGINE_ADAPTER.md), and
   [RUN_EVIDENCE_CONTRACT.md](RUN_EVIDENCE_CONTRACT.md).
2. Inspect `git status` in this repository and both installed engine
   repositories. Preserve all user-owned dirty files; never reset a sibling
   checkout to make preflight pass.
3. Inspect the installed LaunchAgent, health endpoint, engine lock ownership,
   and current operator overview before restarting, rebuilding, or triggering a
   run. An active scheduled run and an active cockpit job are both reasons to
   wait. A normal companion upgrade additionally holds the legacy SQLite writer
   slot until the old process has stopped, then keeps the adapter lock through
   new-service activation; never bypass that handoff except through the explicit
   reviewed emergency override.
4. Treat configured paths, bearer/pairing material, browser-owner markers, raw
   logs, report bodies, and review-target detail as private. Do not print or
   commit them.
5. Keep the local UI and hosted workflows distinct. Changes to cookie auth must
   retain loopback Host validation, same-origin proof, the explicit request
   header, two-minute hash-only single-use activation, raw-HTML non-escalation,
   `HttpOnly`/`SameSite=Strict`, and the rule that the cookie never exposes or
   rotates the bearer.
6. Keep progress aggregate-only and exact-run-bound. Never infer an active phase
   from a visible browser window or select mutable `latest` artifacts.
7. Run the web, companion, extension, privacy, type, lint, and export checks
   appropriate to the change. Rebuild `static-export/` before reloading the
   installed service.
8. Commit and push the product repository only after the tests pass. Updating
   this surface does not authorize committing unrelated dirty files in the
   ResumeGenerator or Outreach repositories.

## Troubleshooting

- **The local URL does not load:** check the health endpoint, LaunchAgent state,
  and companion error log. Confirm that `static-export/app/index.html` and
  `static-export/assets/` exist and are regular, non-symlink entries. The normal
  `scripts/open-operator-cockpit.sh` launcher first tries to re-enable, load,
  and start the installer-managed LaunchAgent when health is unavailable; it
  does not replace the plist, rotate auth, or kill an unhealthy live process.
- **The local UI says activation required:** run
  `scripts/open-operator-cockpit.sh`. Do not show or rotate hosted pairing
  material. If private bearer/state validation fails, use the explicitly
  reported `python3 -m recruiting_companion repair-auth` command; it rotates all
  auth sessions and reports paths, never secrets.
- **The hosted page asks to pair or says its session expired:** that is expected
  after the hosted path's 12-hour tab session ends. Do not rotate pairing for
  daily operation. The hosted operational UI will hand off automatically after
  positive local health/version evidence when browser policy permits; otherwise
  use its **Open permanent local cockpit** button. The protected local cookie is
  browser-profile scoped and lasts across normal browser and service restarts.
- **Progress appears partial:** inspect lock state and exact run artifacts. A
  partial projection is a safety response, not permission to start another run.
- **A LinkedIn browser remains after terminal state:** verify the upstream run's
  recorded browser cleanup result and ownership before intervening. Never kill
  an unrelated personal Chrome process.
- **Account data is busy:** wait for scheduler, pipeline, workbook, queue, and
  companion mutation locks to become free, then refresh.

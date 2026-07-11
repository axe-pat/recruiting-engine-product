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
| Communications | Outreach communication-learning and recommendation-review artifacts | Delivery-backed totals, corpus labels, and recommendation-review aggregates; no drafts or message bodies |
| Reports | One completed nightly summary and the exact manifest, source metrics, action queue, and Outreach report it names | Run-scoped reports and explicit source status, including zero, skipped, timed-out, and failed |
| Operations | Production release attestation plus scheduler, pipeline, workbook, and companion mutation locks | Capability availability, fixed confirmations, and an audit trail of local jobs |

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
creation.

Long-running or paid generation returns an audit job immediately and continues
locally. The UI polls status instead of keeping a browser request open for the
duration of a model run.

Queue buttons are also fail-closed per row. A globally registered command is not
enough: the selected row must carry an available action, exact job identifier,
reason, and confirmation phrase from the companion projection.

## Deliberate boundaries

The generic cockpit does not expose:

- arbitrary shell commands, paths, flags, or environment variables;
- full nightly execution, whose production arguments can include live sends;
- LinkedIn or SMTP delivery;
- rtrvr live execution or final application submission;
- raw contacts, email addresses, message bodies, answer-bank payloads, or private
  interview-prep content;
- blind `Mark applied` or `Mark closed` actions that could drop generated files
  from the active queue before they are archived.

Those are product boundaries, not missing buttons. A later execution surface can
add a consequential action only after it has its own recipient/artifact binding,
archive semantics, bounded limits, and reconciliation contract.

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

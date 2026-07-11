# Data portability and privacy boundary

## Purpose

The hosted surface is a product proof, not a public copy of the private recruiting workspace. It should make the engine's architecture, operating depth, evolution, and verified scale legible while keeping every candidate, contact, message, and application artifact private.

The unpaired public runtime reads one reviewed, static module: `lib/product-data.ts`. It never imports either production repository, opens local run artifacts, connects to signed-in browser state, or receives credentials. A paired browser can call the separately installed loopback companion directly; those requests are not proxied through or stored by the hosted site.

## Data classes

### Aggregate production evidence

Permitted evidence is numeric and non-identifying:

- repository age and aggregate commit counts;
- passing-test totals and static-analysis state;
- source health expressed as aggregate observed/advanced counts;
- role-family discovery, scoring, and surfacing totals;
- deduplicated queue size and review-gate totals;
- aggregate tracker, touchpoint, and outcome counts;
- system stages, policies, safety controls, and product milestones.

The initial snapshot is dated July 11, 2026. It was assembled from a reviewed backlog-completion evidence manifest, an exact-run nightly summary, a run-scoped daily report, a role-surface replay, shared-queue summary statistics, and repository history. No raw artifact is copied into this product.

### Fictional demonstration data

Queue cards are invented examples. Every record has all three of the following signals:

- `demo: true`;
- `dataClass: "fictional-demo"`;
- a `Demo Company` alias and an explicit safety note.

These examples demonstrate routing and review behavior only. They are not altered versions of real prospects and must not be presented as measured outcomes.

### Prohibited data

The portable surface must never contain:

- personal names, email addresses, phone numbers, profile URLs, or stable person identifiers;
- real candidate, prospect, or relationship records;
- real company-level target records when they could expose outreach intent;
- private message text, outreach drafts, replies, or communication history;
- resumes, cover letters, story-bank content, job-description text, or generated application materials;
- local filesystem paths, browser-session state, API keys, SMTP settings, tokens, or other credentials;
- raw captures, source payloads, screenshots, spreadsheets, or production database exports.

## Portability architecture

```text
Public proof lane                         Private operating lane
Private production artifacts             Curated uploads and reviewed imports
        │                                           │
        │ manual aggregate review                   ▼
        ▼                                  Loopback companion + SQLite
Allowlisted metrics + fictional demos              │
        │                                           ├── portable runs
        ▼                                           └── read-only existing adapter
Hosted command center                              │
        │                                           │ direct, paired requests
        └───────────────────────────────────────────┘
```

This boundary keeps the hosted build independently deployable while allowing a user to opt into a real private workspace. A clean checkout needs only the product repository, Node for the command center, and Python's standard library for the companion.

## Evidence semantics

- `aggregate-real-run` means the value came from a reviewed production run or tracker summary.
- `aggregate-repository-history` means the value came from current repository history.
- `fictional-demo` means the entire record was invented for interface demonstration.
- Source status is scoped. `audited-nightly` describes the exact production run; `reviewed-canary` describes a separate bounded adapter check.
- A zero can mean either no qualifying yield or an intentionally disabled source. Those states remain separate.
- The public snapshot is historical evidence, not a live service-level claim and not a promise that future runs will have identical counts.

## Safe refresh procedure

1. Select one authoritative, completed run and its exact evidence pointers. Do not combine unrelated “latest” artifacts.
2. Reduce evidence to an allowlisted aggregate schema outside the product runtime.
3. Remove source URLs, filenames, local paths, free text, identifiers, and company/person-level rows.
4. Review every string manually; aggregation alone does not make copied private text safe.
5. Update the snapshot date and only the values whose provenance was rechecked.
6. Run TypeScript, lint, build, and a content scan for emails, profile URLs, local paths, credentials, and known private identifiers.
7. Visually confirm that real metrics and fictional examples are labeled differently everywhere they appear.

## Product claims this layer supports

The snapshot can truthfully support a story about:

- a 96-day, four-calendar-month build and iteration window;
- 151 commits across two cooperating engines;
- a full discovery-to-learning workflow with human gates around consequential actions;
- 542 passing tests in the attested production release;
- a run-scoped role audit of 402 observations across 220 companies and six source families;
- a shared daily queue that deduplicated 128 observations into 119 companies before applying a 50-item operating cap;
- aggregate relationship and outcome telemetry that closes the loop between product decisions and real-world results.

It cannot support claims that the product is a hosted multi-user SaaS, that demo companies are real pipeline opportunities, that the static snapshot is live production telemetry, or that a new portable user inherits the private operator's historical data and configured sources.

## Current portable mode

The local companion now provides per-user SQLite state, private document storage, one-time pairing, reviewed CSV/job intake, deterministic decision runs, reports, and a strict outreach review state machine. It starts empty and labels unconfigured sources explicitly. It does not parse resumes, calculate semantic fit, call models, scrape third-party sites, submit applications, or send messages.

The existing-engine adapter is read-only. It follows the exact summary → manifest → report authority chain and keeps current workspace snapshots separate from immutable run evidence. Live scheduling and execution remain owned by the separately installed production system.

The Chrome extension is an explicit intake and approval surface. It has no persistent content script or send action, and it refuses LinkedIn page capture. Captured context travels to the loopback companion only after a user action.

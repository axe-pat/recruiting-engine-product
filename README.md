# Recruiting Engine

**Primary operator UI on this Mac:**
[127.0.0.1:8765/app](http://127.0.0.1:8765/app/)

**Live product:** [axe-pat.github.io](https://axe-pat.github.io/)

**Working app:** [axe-pat.github.io/app](https://axe-pat.github.io/app/)

**Product story:** [axe-pat.github.io/story](https://axe-pat.github.io/story/)

Recruiting Engine is a local-first recruiting decision system built from a real,
months-long operating workflow. It turns role, company, and relationship evidence
into one reviewed queue: apply, reach out, follow up, research, watch, or skip.

The product now has four cooperating surfaces:

| Surface | Responsibility | Data boundary |
|---|---|---|
| Primary local command center | Daily dashboards, queues, exact run progress, next-run plan, account tracker, reports, and reviewed execution | Same-origin loopback UI; persistently connected on this device |
| Hosted command center | Product story, onboarding, portable workflow, and the later public path | Connection gate until paired; real local data only on operational routes |
| Local companion | Static application serving, SQLite system of record, document storage, persistent local UI auth, pairing, imports, deterministic portable runs, existing-engine evidence, and audited fixed actions | User device only |
| Chrome companion | Explicit page/paste intake and recipient-plus-draft approval | Device-local; no send action |

The original operating depth remains in two source engines:

- [ResumeGenerator](https://github.com/axe-pat/Resume-generator) — discovery,
  eligibility and fit decisions, application state, and tailored materials.
- [Outreach](https://github.com/axe-pat/Outreach) — company and people state,
  relationship workflows, bounded execution, reconciliation, and learning.

## Product architecture

```text
Primary local command center + API (same loopback origin, persistent cookie)
                │
                ▼
Local companion (validated static export + SQLite + private document directory)
       │                         │
       │ portable mode           │ guarded operator adapter
       ▼                         ▼
Reviewed imports          ResumeGenerator + Outreach
deterministic queue        exact summary → manifest → report evidence
       ▲
       │ explicit intake and approval
Chrome MV3 side panel

Hosted command center (optional/later public path)
                │ one-time pairing + tab-scoped web session
                └──────────────────────────────► Local companion
```

The website never becomes the database. The primary local UI is served at
`http://127.0.0.1:8765/app/` by the same companion that serves its API. A
restart-stable, host-only `HttpOnly`, `SameSite=Strict` local cookie keeps it
connected after one explicit `scripts/open-operator-cockpit.sh` activation,
without exposing the bearer token to browser JavaScript or asking for new
pairing codes. Raw HTML cannot mint that cookie. The hosted UI retains its
separate one-time pairing and
12-hour tab-scoped web session for the future public/portable path. Private
documents and operational records remain on the device.

## Product routes

- `/app` — command center with source health, priorities, applications, and conversations;
- `/app/onboarding` — four-step private onboarding with curated uploads;
- `/app/sources` — Handshake/generic CSV import and explicit connector states;
- `/app/queue` — one human-gated daily decision queue;
- `/app/accounts` — real account portfolio, due/action/tier/stage aggregates,
  bounded action queue, and a guarded open-in-Excel action;
- `/app/stories` — private story, positioning, and communication inventories;
- `/app/operations` — fixed local capabilities, production guards, and job history;
- `/app/runs` and `/app/reports` — exact scheduled/cockpit progress polling,
  reviewed production E2E execution, run-scoped evidence, and sandboxed full
  exact reports;
- `/app/plan` — a prioritized next-run action plan derived from the last exact
  run plus the durable review ledger;
- `/app/applications` and `/app/outreach` — execution state and full-draft approval;
- `/app/settings` — pairing, portable/existing mode, and engine-binding status;
- `/story`, `/architecture`, and `/privacy` — product narrative, system design, and data policy.

Unpaired hosted operational routes render a hard connection gate and no company,
queue, run, or report rows. The primary loopback UI authenticates itself through
its same-origin local cookie. Fictional examples remain confined to public
product-story surfaces and code fixtures; they are never presented as an
operator workspace or mixed with local data.

## Start the primary local product

Requirements: Node.js `>=22.13.0` for the generated UI and Python `>=3.11` for
the dependency-free companion.

Build the static application:

```bash
npm install
npm run export:static
```

Then promote the validated stage and start the companion through the guarded
installer:

```bash
scripts/install-operator-companion-launch-agent.sh --production-preflight
```

The companion binds to `127.0.0.1:8765`, creates a private per-user data
directory, validates the promoted `static-export/`, and serves both UI and API.
The exporter writes only `static-export.staged/`; the installer stops the old
service under its interlock before promotion and rolls the UI generation back if
replacement startup fails. The server verifies each requested file against the
sealed startup inventory. In another terminal run
`scripts/open-operator-cockpit.sh`; it captures a two-minute,
single-use activation without printing it, then establishes the same-origin
local cookie. The cookie persists across normal browser and companion restarts.
No pairing token is required for this primary path.

`npm run dev` remains available for web development. The hosted GitHub Pages
build and Chrome companion retain their explicit pairing flows.

See [the companion guide](companion/README.md) for the API, custom data roots,
token rotation, source import schema, and security model. See the
[primary local UI runbook](docs/PRIMARY_LOCAL_UI.md) for daily operation,
progress, rebuild/restart rules, and external-agent handoff.

## Bind an existing engine

Portable mode starts empty and makes only claims it can prove from the user's
local imports. Existing-engine mode is a private operator cockpit over a
separately installed ResumeGenerator + Outreach system:

```bash
export RECRUITING_ENGINE_RESUME_ROOT="/path/to/resume-engine"
export RECRUITING_ENGINE_OUTREACH_ROOT="/path/to/outreach-engine"
export RECRUITING_ENGINE_RUNTIME_DIR="/path/to/runtime-lock-directory"
export RECRUITING_ENGINE_ATTESTATION_PATH="/path/to/release-attestation.json"
python3 -m recruiting_companion serve
```

The adapter follows exact run pointers and refuses mutable `latest`/`current`
aliases. Bounded local projections, a durable exact-target review ledger, and
fixed confirmed actions are available. Applied/closed lifecycle transitions use
the artifact-preserving upstream contract. Recipient-bound delivery and the
bounded production nightly execute only after one exact target is reviewed and
approved and their installed readiness checks pass. The production target
enables the off-cycle app-queue and Track 2 LinkedIn delivery contracts; email
delivery remains separately recipient-reviewed. The browser-fill lane stays
blocked because the installed runner cannot technically intercept final Submit;
a prompt-only stop rule is not treated as a safety boundary. LinkedIn and SMTP
completion require exact outcome artifacts. An operator run is complete only
after one newly created summary, manifest, and report chain verifies as healthy;
exit code zero alone is not success. The run surfaces label each exact delivery
contract and never read a mutable `latest` report alias. Arbitrary commands never exist. See the
[operator cockpit contract](docs/OPERATOR_COCKPIT.md),
[the adapter contract](docs/EXISTING_ENGINE_ADAPTER.md) and
[run-evidence contract](docs/RUN_EVIDENCE_CONTRACT.md).

On macOS, the repository includes a loopback-only, reversible operator service
setup using sibling `ResumeGenerator v1` and `Outreach` checkouts by default:

```bash
scripts/probe-operator-companion.sh --production-preflight
scripts/install-operator-companion-launch-agent.sh --dry-run
scripts/install-operator-companion-launch-agent.sh --production-preflight
```

The generated LaunchAgent contains no credentials or pairing tokens. Build the
static export before installing it, then use the canonical local URL. See the
[macOS operator setup](docs/OPERATOR_SETUP.md) for overrides, logs, inspection,
hosted pairing, and uninstall commands.

## Install the Chrome companion

For local development:

1. Start and pair the local companion.
2. Open `chrome://extensions`, enable Developer mode, and choose **Load unpacked**.
3. Select this repository's `extension/` directory.
4. Open the toolbar action, grant access to the selected loopback origin, and pair.

The extension requests `activeTab`, `scripting`, `sidePanel`, `storage`, and an
optional loopback host grant. It has no persistent content script, broad internet
host access, message-send action, or LinkedIn capture. Full details are in
[the extension guide](extension/README.md) and
[store-review notes](extension/STORE_REVIEW.md).

## Verification

Hosted app:

```bash
npm run verify:privacy
npm run lint
npx tsc --noEmit
npm test
npm audit --omit=dev
```

Local companion:

```bash
PYTHONPATH=companion python3 -m unittest discover -s companion/tests -v
python3 -m compileall -q companion/recruiting_companion companion/tests
```

Chrome extension:

```bash
node --test extension/tests/*.test.mjs
node --check extension/service-worker.js
node --check extension/sidepanel.js
python3 -m json.tool extension/manifest.json >/dev/null
```

## Privacy and product claims

The public bundle contains reviewed non-identifying aggregates and fictional
examples. It contains no resumes, contacts, messages, credentials, signed-in
browser state, or production artifacts. The primary local app and any paired
hosted client talk directly to the loopback companion; there is no hosted user
database in this release.

This repository supports two truthful claims:

1. the underlying single-user system has real production operating depth,
   scheduled runs, exact evidence, and human-gated execution;
2. the portable release is a real local-first product for onboarding, imports,
   state, decisions, reports, browser intake, and reviewed outreach—not a claim
   of feature parity with the private operator's accumulated data or sources.

See the [hosted privacy policy](https://axe-pat.github.io/privacy/) and
[data-portability boundary](product-notes/data-portability.md).

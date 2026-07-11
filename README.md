# Recruiting Engine

**Live product:** [axe-pat.github.io](https://axe-pat.github.io/)

**Working app:** [axe-pat.github.io/app](https://axe-pat.github.io/app/)

**Product story:** [axe-pat.github.io/story](https://axe-pat.github.io/story/)

Recruiting Engine is a local-first recruiting decision system built from a real,
months-long operating workflow. It turns role, company, and relationship evidence
into one reviewed queue: apply, reach out, follow up, research, watch, or skip.

The product now has three cooperating surfaces:

| Surface | Responsibility | Data boundary |
|---|---|---|
| Hosted command center | Onboarding, dashboards, sources, queues, runs, applications, outreach review, and reports | Static preview until paired |
| Local companion | SQLite system of record, document storage, pairing, imports, deterministic portable runs, existing-engine evidence, and audited fixed actions | User device only |
| Chrome companion | Explicit page/paste intake and recipient-plus-draft approval | Device-local; no send action |

The original operating depth remains in two source engines:

- [ResumeGenerator](https://github.com/axe-pat/Resume-generator) — discovery,
  eligibility and fit decisions, application state, and tailored materials.
- [Outreach](https://github.com/axe-pat/Outreach) — company and people state,
  relationship workflows, bounded execution, reconciliation, and learning.

## Product architecture

```text
Hosted command center (public code, fictional preview)
                │
                │ one-time loopback pairing
                ▼
Local companion (SQLite + private document directory)
       │                         │
       │ portable mode           │ guarded operator adapter
       ▼                         ▼
Reviewed imports          ResumeGenerator + Outreach
deterministic queue        exact summary → manifest → report evidence
       ▲
       │ explicit intake and approval
Chrome MV3 side panel
```

The website never becomes the database. It persists only a loopback origin; a
30-minute web token lives in tab-scoped session storage. Private documents and
operational records go directly to the companion running on that device.

## Product routes

- `/app` — command center with source health, priorities, applications, and conversations;
- `/app/onboarding` — four-step private onboarding with curated uploads;
- `/app/sources` — Handshake/generic CSV import and explicit connector states;
- `/app/queue` — one human-gated daily decision queue;
- `/app/accounts` — real account portfolio, action queue, tiers, and stages;
- `/app/stories` — private story, positioning, and communication inventories;
- `/app/operations` — fixed local capabilities, production guards, and job history;
- `/app/runs` and `/app/reports` — run-scoped evidence and decision briefs;
- `/app/applications` and `/app/outreach` — execution state and full-draft approval;
- `/app/settings` — pairing, portable/existing mode, and engine-binding status;
- `/story`, `/architecture`, and `/privacy` — product narrative, system design, and data policy.

Every unpaired route uses explicitly fictional records. Pairing replaces the
preview with the user's local data; it never mixes the two.

## Start the working product locally

Requirements: Node.js `>=22.13.0` for the hosted UI and Python `>=3.11` for the
dependency-free companion.

Start the command center:

```bash
npm install
npm run dev
```

In another terminal, from this repository root:

```bash
export PYTHONPATH="$PWD/companion"
python3 -m recruiting_companion serve
```

The companion binds to `127.0.0.1:8765`, creates a private per-user data
directory, and prints the path to a one-time pairing token. Open
`http://localhost:3000/app/onboarding`, paste that token in the final step, and
the browser exchanges it for a local bearer.

See [the companion guide](companion/README.md) for the API, custom data roots,
token rotation, source import schema, and security model.

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
aliases. Bounded local projections and fixed, confirmed actions are available;
arbitrary commands, external sends, final submission, and full nightly execution
are not. The installed scheduler remains the only production-run owner. See the
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

The generated LaunchAgent contains no credentials or pairing tokens. See the
[macOS operator setup](docs/OPERATOR_SETUP.md) for overrides, logs, inspection,
pairing, and uninstall commands.

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
browser state, or production artifacts. The paired app talks directly to the
loopback companion; there is no hosted user database in this release.

This repository supports two truthful claims:

1. the underlying single-user system has real production operating depth,
   scheduled runs, exact evidence, and human-gated execution;
2. the portable release is a real local-first product for onboarding, imports,
   state, decisions, reports, browser intake, and reviewed outreach—not a claim
   of feature parity with the private operator's accumulated data or sources.

See the [hosted privacy policy](https://axe-pat.github.io/privacy/) and
[data-portability boundary](product-notes/data-portability.md).

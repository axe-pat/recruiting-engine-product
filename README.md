# Recruiting Engine — portable product surface

**Live product:** [https://axe-pat.github.io/](https://axe-pat.github.io/)

Recruiting Engine is a portfolio-grade, hosted interface for a real personal AI
product built across two operating repositories:

- [ResumeGenerator](https://github.com/axe-pat/Resume-generator) — discovery,
  fit decisions, application state, and tailored application materials.
- [Outreach](https://github.com/axe-pat/Outreach) — company and people state,
  relationship workflows, bounded execution, reconciliation, and outcome learning.

The original product is private and operator-specific. This repository is the
portable proof surface: it explains the system, demonstrates its decision model,
and publishes reviewed aggregate evidence without exposing resumes, contacts,
messages, credentials, or real targeting data.

## Product routes

- `/` — interactive product demo, verified proof metrics, evolution, and public
  source-repository links.
- `/story` — fact-checked PM/first-user case study, including the failure that
  became a permanent product guardrail.
- `/architecture` — source, decision, execution, role-coverage, source-health,
  and safety architecture.

## Evidence and privacy

`lib/product-data.ts` is the only public product snapshot. It contains:

- reviewed, non-identifying aggregate metrics;
- system stages, policies, and safety controls;
- role-family and source-health totals;
- clearly labeled fictional demo queue records.

It intentionally contains no personal names, emails, profile URLs, private
message text, resumes, application materials, credentials, browser state, or raw
production artifacts. See [data portability](product-notes/data-portability.md)
for the full boundary and refresh procedure.

## Product story

The defensible narrative, source ledger, resume bullets, and interview-ready
version live in [the narrative brief](product-notes/narrative-brief.md). The short
version:

> I acted as the product manager, first user, and operator, and used AI coding
> agents as my engineering team. Across 96 versioned days and 151 commits, I
> turned a resume-tailoring workflow into a production recruiting decision system
> with two specialized execution lanes, a scheduled nightly path, exact-run
> evidence, human gates, and 542 attested release tests.

## Local development

Requirements: Node.js `>=22.13.0`.

```bash
npm install
npm run dev
npm test
```

The site uses the bundled vinext/Cloudflare Workers-compatible build and stores
no runtime secrets or database state.

For the exact validation and GitHub Pages publishing sequence, see
[deployment notes](docs/DEPLOYMENT.md).

## Portfolio positioning

This is a real single-user vertical AI product and a portable read-only showcase.
It is not presented as a multi-tenant SaaS, a monetized service, or a fully
autonomous job-application agent. Its proof is the operating depth underneath the
interface: durable state, source adapters, decision queues, production scheduling,
execution controls, exact artifacts, outcome telemetry, and product changes driven
by real use.

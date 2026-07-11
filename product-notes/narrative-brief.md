# Recruiting Engine — fact-checked product narrative brief

## Narrative spine

**The Recruiting Engine began as a resume-tailoring tool and evolved, through months of use on a real job search, into a production recruiting decision system that decides what to apply to, who to contact, when to follow up, and when to do nothing.**

The strongest story is not that Akshat made a clever resume generator. It is that he served as both product manager and first customer for a vertical AI product, repeatedly turned observed workflow failures into requirements, and used AI coding agents as an engineering team to ship those requirements through two real Git repositories. The proof is the product's evolution: source ingestion, decision queues, generated application packages, relationship tracking, bounded LinkedIn execution, run-scoped reporting, a nightly production path, release attestation, and regression tests created from real failures.

The portfolio surface should make one product idea unmistakable:

> A job search is not a writing problem. It is a noisy, stateful decision-and-execution problem.

The engine reduces that mess to a trustworthy next action: **apply, apply plus outreach, outreach, follow up, buffer, or skip**.

## Honest positioning

### What Akshat can credibly claim

- He was the product manager, first user, and operator of the system. His own search supplied the real workflow, private source material, acceptance criteria, edge cases, and feedback loop.
- He used AI coding agents as high-velocity engineering capacity. He set priorities, specified behavior, reviewed outcomes, introduced new requirements, operated real runs, and made the product and safety decisions reflected in the repos.
- The system is a real single-user vertical product, not a mockup. It has durable state, real source adapters, generated documents, browser-backed execution, production schedules, failure artifacts, tests, and release controls.
- It has a genuine development/production boundary at the code-release level: feature work is expected to happen on branches/worktrees, while the unattended pipeline is guarded by clean `main` branches and an attestation binding production to exact tested SHAs.
- The hosted portfolio surface should be a portable, sanitized proof of the underlying product. It should not pretend that the private local engine is already a self-serve, multi-tenant SaaS.

### The credit story, phrased well

Use:

> I acted as the PM and first customer, and used AI coding agents as my engineering team. I owned the problem definition, roadmap, tradeoffs, acceptance criteria, operating reviews, and the decision to ship or roll back behavior. The agents accelerated implementation, but the product judgment came from months of using the workflow and turning failures into requirements.

Avoid implying that Akshat personally hand-authored every line, or that simply prompting an agent was the product work. The differentiated skill is the closed product loop: **observe → prioritize → specify → ship → operate → inspect evidence → change the product**.

## Credible evolution timeline

| Period | Product stage | Evidence-backed evolution |
|---|---|---|
| March 2026 | Resume intelligence | The earliest system report, dated March 17, describes a JD-to-resume engine that extracted hiring signals, selected and reordered stories, chose variants, and ran automated QC. A seven-JD batch passed 42/42 documented checks. The March application-system plan already recognized that resume generation was only one stage in a broader discovery, ranking, generation, review, and outreach workflow. |
| April 6–8 | From artifact to tracked product | ResumeGenerator entered Git on April 6 as a functioning application-package system; Outreach began as a separate LinkedIn automation scaffold on April 8. This is the beginning of the verifiable 96-day Git history through July 11. |
| April 13–20 | Workflow and systems of record | ResumeGenerator added run-native discovery staging, non-PM generation paths, queue maintenance, and cost-aware model routing. Outreach added public-source adapters, an entity-first workbook, a ResumeGenerator bridge, and progressively safer LinkedIn send/reconciliation behavior. The key architecture decision was to keep discovery shared while preserving separate application and relationship systems of record. |
| April 24–May 2 | Broader intake and queue resilience | Startup job-board discovery was added, and queue refreshes were changed to preserve manual entries. This is a strong example of dogfooding: a real operating failure—useful hand-curated work disappearing during refresh—became a durable product rule. |
| May 27–29 | Daily decision engine | ResumeGenerator added JobSpy breadth discovery, source validation, a gated action queue, HTML review output, a supervised daily engine, and recorded real supervised runs. Outreach hardened batch resilience, adaptive startup gates, and shared-history note hooks. The product moved from document generation to a daily operating loop. |
| June 9–13 | Source expansion and scheduling | The combined system was formally documented as the Recruiting Engine. Handshake became a source lane, nightly orchestration gained browser preflight and timeouts, and overlapping nightly runs were prevented. Outreach added message-quality regression tests and continued real send reconciliation. |
| July 6–10 | Relationship engine and trustworthy evidence | A supervised Track 2 relationship run was hardened, and the nightly report became explicitly run-scoped. Role-aware messaging, company review/watchlists, cadence, outcome learning, cold-email gating, and role-surface monitoring were integrated. A crucial PM decision was that a report is not trustworthy unless every metric points to the exact run that produced it. |
| July 11 | Production hardening and portfolio-grade breadth | The two repos added release attestation, exact manifests, fail-reporting nightly finalization, subprocess deadlines, shared discovery across roles/companies/warm contacts, reviewed company/news inputs, high-affinity expansion, PeopleGrove curation, and idempotent relationship imports. A real mistargeted invite was withdrawn and converted into fail-closed regression coverage across every send path. Both repository HEADs were then attested and pushed on `main`. |

## Product decisions worth surfacing

### 1. Solve the operating system before building the interface

The original Outreach architecture deliberately recommended a Python CLI before a web app. That was sound product sequencing: browser automation, selectors, scoring, note quality, state reconciliation, and source contracts were still changing quickly. The UI is valuable now because the workflow and evidence model are mature enough to deserve a portable surface—not because a dashboard is the product.

### 2. Separate shared discovery from specialized execution

ResumeGenerator owns application state. Outreach owns relationship state. The shared queue merges their signals without creating a third mutable tracker or silently writing back. This prevents a common internal-tools failure: one convenient dashboard becoming an unauditable second source of truth.

### 3. Model entities, not source-specific spreadsheets

Outreach stores organizations, opportunities, contacts, touchpoints, and sources, then uses tags and provenance to segment them. That made it possible to add YC, Built In, LinkedIn, company/news feeds, USC relationship sources, and future email without redesigning storage for each channel.

### 4. Use AI where judgment is valuable; use rules where certainty matters

The product routes model spend by role fit, defers cover letters until needed, prefilters obvious non-fit jobs before paid scoring, and keeps deterministic safety/QC around model output. The defensible claim is cost-aware routing—not a perfect autonomous optimizer.

### 5. Human review is a product feature

Application assist stops before final submit. SMTP is disabled without reviewed drafts and sender credentials. Affinity expansion is bounded and default-off. Relationship imports require stage/review/import gates. The system automates preparation and constrained execution while preserving human authority at high-cost or irreversible steps.

### 6. Trust requires run-scoped provenance

The engine learned not to mix the newest file lying in a workspace with the current nightly run. Exact manifests now bind source metrics, queues, send artifacts, failures, and reports to a run ID. Missing evidence is surfaced as skipped or failed rather than inferred as success.

### 7. Production means recoverable failure, not a perfect demo

The strongest maturity story is a real canary failure. A fallback associated one person with the target company based on a name match rather than verified current-employer evidence. The batch was stopped, the single invite was withdrawn, no message was sent, the record was marked do-not-contact, and the failure became a permanent fail-closed rule and regression suite across direct, manual, Track 2, and application-queue send paths.

Public phrasing:

> A canary run exposed a false-positive company match. I stopped the batch, withdrew the invite, and changed the system contract: coverage-only candidates cannot send without independent current-employer evidence. That incident shaped the product more than another polished demo would have.

## Development and production maturity

The product has more than a `dev` and `prod` label:

- **Development contract:** new sources, selectors, messaging rules, and reports are exercised in a branch or isolated worktree with focused and combined non-live tests.
- **Release contract:** production requires both repos on clean `main`, exact tested SHAs, release evidence, and a successful production check.
- **Production entrypoint:** a 1:00 a.m. LaunchAgent invokes the nightly pipeline; the installed agent and release attestation were present at audit time.
- **Failure behavior:** the nightly runner attempts to produce its exact manifest, summary, and report even after subprocess failure. It records partial action evidence and refuses blind replay.
- **Execution controls:** sends are explicit and bounded; uncertain delivery reserves the slot until a signed-in reconciliation; timeouts terminate isolated process groups rather than leaving hidden live automation.
- **Current release evidence:** the July 11 attestation binds Outreach `d5e4c0c` and ResumeGenerator `2a0ffa3` and records 482 + 60 passing tests, plus lint/compile/release-tree checks.

This is real production maturity for a personal vertical product. It is not yet proof of multi-tenant onboarding, external uptime, billing, account isolation, or generalized customer adoption. The portfolio should treat those as out of scope, not paper over them.

## Defensible metrics

All product-state metrics below are point-in-time snapshots from July 11, 2026. They demonstrate real use and system breadth; they are not claims of external customer traction.

| Metric | Defensible claim | Source/qualification |
|---|---:|---|
| Verifiable build history | 151 commits across the two operating repos over 96 days | 79 Outreach commits + 72 ResumeGenerator commits from April 6 through July 11. The histories include parallel/reconciliation commits, so say commits, not working sessions. |
| Git release state | Both operating HEADs matched `origin/main` at audit time | Outreach `d5e4c0c`; ResumeGenerator `2a0ffa3`; each showed 0 ahead / 0 behind. Runtime data and separate story-workbench files remained locally modified/untracked and should not be described as a wholly clean workspace. |
| Role corpus | 2,514 unique role records; 253 recorded as applied | `discovery/jobs.xlsx`, combining unique IDs in `Jobs` and `Archive`; 1,184 active-sheet rows and 1,330 archive rows. |
| Generated application artifacts | 280 resume DOCX files and 172 cover-letter DOCX files present locally | File snapshot under `ResumeGenerator v1/apps/`; describe as generated artifacts, not necessarily unique applications or externally submitted files. |
| Relationship system | 560 organizations, 412 opportunities, 846 contacts, 849 touchpoints, 13 sources | Reviewed completion evidence artifact. |
| Observed outreach outcomes | 623 sends, 49 accepts, 17 replies | Outcome-learning snapshot. Do not imply causal lift or complete attribution. |
| Relationship-source curation | 1,845 PeopleGrove profiles captured; 135 imported; 1,710 not imported | Twelve targeted queries; seven exhausted and five broad queries were bounded samples. Do not claim the 43.1k+ directory was fully scraped. No emails or LinkedIn URLs were guessed. |
| Role coverage audit | 402 observations, 379 unique roles, 220 companies, 6 source families, 0 families below configured floor | Exact July 11 role-surface replay. Floors demonstrate configured coverage, not complete market coverage. |
| Shared decision queue | 128 observations merged into 119 companies; 50 returned after cap; 18 ready and 32 human-review-required | Exact July 11 shared queue. The system intentionally suppressed 69 after ranking/cap. |
| Production verification | 542 release tests recorded as passing | July 11 release attestation: 482 Outreach + 60 ResumeGenerator. |

### Recommended public metric strip

Use no more than four numbers in the hero area:

**2,514 roles tracked · 560 companies · 849 touchpoints · 542 release tests**

Place the more personal outcome counts in an expandable evidence panel, not the hero.

## Landing-page copy

### Hero

**Eyebrow**  
An AI product built in production, with myself as the first user

**Headline**  
From thousands of recruiting signals to the next right move.

**Subhead**  
Recruiting Engine turns fragmented job, company, and relationship data into one reviewed decision queue: apply, reach out, follow up, buffer, or skip. I built it over months as its product manager, operator, and first customer—using AI coding agents as my engineering team.

**Primary CTA**  
Explore the live product

**Secondary CTA**  
See how it evolved

### Product thesis

**The hard part of a job search is not writing one more resume. It is deciding where attention is worth spending.**

Job boards produce noise. Relationship context lives somewhere else. Application artifacts become stale. Follow-ups disappear. AI can generate text, but without state, provenance, and stop rules it simply generates more noise. Recruiting Engine combines discovery, judgment, execution, and evidence in one loop.

### How it works

1. **Discover** — ingest roles, companies, news signals, and relationship leads from multiple source families.
2. **Decide** — score and merge evidence into a company-level action: apply, outreach, follow up, review, or skip.
3. **Prepare** — generate role-specific resumes, optional cover letters, contact candidates, and messages.
4. **Execute safely** — stop at human gates, bound live actions, and preserve every run's evidence.
5. **Learn** — feed outcomes and failures back into source rules, role coverage, messaging, and safety contracts.

### Builder story

**I did not invent a hypothetical user. I was the user.**

Every weak recommendation, duplicate queue item, noisy source, stale metric, awkward message, and unsafe automation path showed up in my own workflow. I turned those moments into requirements and shipped them through Git. That is how a resume tool became a recruiting operating system with a nightly production path and an auditable release process.

### Trust section

**Automation earns scope. It does not receive it by default.**

The engine uses exact run manifests, human review gates, idempotent imports, bounded sends, delivery reconciliation, and fail-closed company matching. A real canary failure was not hidden; it became a product rule and regression coverage across every live-send path.

### Closing line

**The interface is new. The product underneath it has been earning its shape for months.**

## Resume-ready project description

### Compact version

**Recruiting Engine — Product Manager & AI-Native Builder | Mar–Jul 2026**  
Built and operated a multi-source recruiting decision engine that converts job, company, and relationship signals into reviewed application and outreach actions; evolved it through 151 commits across two repos into an attested nightly production pipeline with 542 release tests.

### Three-bullet version

- Designed and shipped a multi-source recruiting engine spanning LinkedIn, Handshake, JobSpy, startup directories, company/news signals, and relationship sources; routed targets into apply, apply-plus-outreach, follow-up, review, buffer, or skip workflows.
- Served as product manager and first user across a 96-day, 151-commit build cycle, translating real operating failures into cost-aware model routing, entity-first state, exact provenance, idempotent imports, human review gates, and fail-closed LinkedIn execution.
- Operationalized a released 1:00 a.m. nightly pipeline with exact-SHA attestation and 542 passing release tests; current product evidence spans 2,514 unique roles, 560 organizations, 846 contacts, and 849 tracked touchpoints.

### One-line portfolio card

I used my own job search as the live product environment for an AI recruiting engine that discovers opportunities, decides the next action, prepares tailored execution, and learns from real outcomes.

## 90-second interview story

I started this in March as a resume-tailoring tool. You could give it a job description and it would extract the strongest signals, choose the right stories from my background, generate a tailored resume, and run quality checks. It worked—but using it made me realize that writing was not the real bottleneck in a job search. The hard problem was deciding which opportunities deserved attention, whether to apply or build a relationship, who to contact, and when to follow up.

So I became both the PM and the first customer. I used AI coding agents as my engineering team, but I owned the roadmap, acceptance criteria, tradeoffs, and production reviews. Over roughly three months of Git history, the product expanded into two connected systems: ResumeGenerator for applications and Outreach for relationships. It now ingests multiple source families, merges them into a reviewed daily queue, generates application materials and outreach, tracks state and outcomes, and runs through an attested nightly production path.

The most important iteration came from a failure. In one canary run, a fallback matched a person to the wrong company and sent one invite. I stopped the batch, withdrew it, and changed the product contract so a candidate cannot send without independent current-employer evidence. Then I added regression coverage across every send path. That was when it stopped feeling like an automation script and started feeling like a real product.

Today the system has tracked 2,514 roles, 560 organizations, and 849 touchpoints, with 542 tests in the current release attestation. My biggest lesson was that AI product management is not about adding a model to a workflow. It is about building the state, evidence, guardrails, and feedback loops that make model-driven decisions trustworthy.

## Product-surface implications

The hosted surface should tell the truth while still feeling ambitious:

- Use a sanitized, deterministic snapshot rather than reading the private live workbooks in a public deployment.
- Make the core interaction the decision queue, not a generic analytics dashboard.
- Let visitors inspect provenance, why an action was recommended, which human gate remains, and what the system will not do automatically.
- Include a product-evolution timeline backed by commits and artifacts.
- Show the canary incident as an interactive “failure became feature” case study.
- Label data as a dated product snapshot, not real-time market coverage.
- Link to public Git only if private data, runtime CSV history, secrets, generated personal documents, and ignored artifacts have been audited for disclosure.
- Keep the public demo read-only. Portability for this portfolio means a credible product surface with sanitized examples—not exposing the operator's credentials, LinkedIn session, private story bank, or live contacts.

## Claims to avoid

- Do not say “a thousand working sessions”; the repos prove 151 commits, not session count.
- Do not say the product has been in Git for “many years” or even six months; the verifiable Git interval is April 6–July 11, with dated product evidence beginning March 17.
- Do not call it a fully autonomous application agent. `apply_assist` intentionally stops before submission.
- Do not call it a multi-user SaaS, monetized product, or external customer platform.
- Do not claim every nightly run succeeded. The credible story includes partial failures, reconciliation, and fail-reporting behavior.
- Do not claim all 43.1k+ PeopleGrove users were captured; 1,845 unique profiles were captured from targeted and bounded searches.
- Do not claim cold email is active in production; live SMTP remains gated on sender credentials and draft approval.
- Do not claim the system proved causal messaging lift; the outcome layer is advisory and its observed sends/accepts/replies are not an experiment.
- Do not publish names, profile URLs, raw resumes, application URLs, message text, credentials, or personal answer-bank data in the portable demo.

## Sources and commits used

### Repository and product evidence

- `ResumeGenerator v1/docs/SYSTEM_REPORT.md` — March 17 resume-engine mechanism, seven-JD batch, and 42/42 QC result.
- `ResumeGenerator v1/docs/APPLICATION_SYSTEM_PLAN.md` — March vision from discovery through application generation, review, and outreach.
- `ResumeGenerator v1/README.md` — current application workflow, cost-aware model routing, scheduler, source taxonomy, and supervised application assist.
- `ResumeGenerator v1/docs/RECRUITING_ENGINE.md` — combined operating model, source lanes, action buckets, nightly automation, exact manifests, and safety rules.
- `ResumeGenerator v1/docs/PRODUCTION_RELEASE.md` — dev/release/production contract and fail-reporting behavior.
- `ResumeGenerator v1/apply_assist/README.md` — auditable task packets and explicit stop-before-submit boundary.
- `ResumeGenerator v1/discovery/jobs.xlsx` — point-in-time role and applied-status counts; IDs were checked for uniqueness across `Jobs` and `Archive`.
- `Outreach/docs/architecture.md` — original CLI-first sequencing, service separation, observability, and review-first rollout.
- `Outreach/docs/system_overview.md` — entity-first model, cross-repo queue, ranking views, and LinkedIn-as-subsystem framing.
- `Outreach/README.md` and `Outreach/docs/TODO.md` — current implemented scope and remaining live/external activation gates.
- `Outreach/artifacts/20260711-145311-backlog-completion-evidence.json` — reviewed July 11 metrics, run audit, PeopleGrove funnel, role coverage, current tracker counts, and incident reconciliation.
- `Outreach/workspace/comms_learning/outcome_recommendation_review_2026-07-11.json` — observed send/accept/reply corpus and advisory-only learning posture.
- `~/Library/Application Support/ResumeGenerator/production_release.json` — exact current release SHAs and 482 + 60 test evidence.
- `ResumeGenerator v1/docs/career_workbench/answer_engine.md` and `docs/career_workbench/story_sources/interview_story_scripts.md` — existing interview style: mechanism-first, human, specific, and grounded in a product decision.

### Git history checkpoints

**ResumeGenerator**

- `61dc566` (2026-04-06) — initial project snapshot.
- `e28628b` — run-native discovery staging and non-PM generation.
- `4e6f720` — smart cost controls for application generation.
- `249970e` — startup apply discovery lane.
- `62b069f` — preserve manual queue entries on refresh.
- `41e4f81` — supervised daily engine and JobSpy scoring lane.
- `7b31a7d` — formal Recruiting Engine documentation.
- `088be20` — Handshake saved-search source.
- `0a5a55b` — prevention of overlapping nightly runs.
- `ff68ea9` — run-scoped recruiting intelligence pipeline.
- `c89f61b` — nightly production orchestration and release attestation.
- `99dfc90` — fail closed on unbound application-invite candidates.
- `2a0ffa3` — attested/pushed `main` HEAD at narrative audit time.

**Outreach**

- `085c69e` (2026-04-08) — initial outreach automation scaffold.
- `8741711` — multi-source discovery and entity-first outreach targeting.
- `2aed78d` — ResumeGenerator-to-Outreach bridge.
- `b3ff1a8` — safer live invites and state reconciliation.
- `f400a78` — adaptive startup outreach gates.
- `6dc37f5` — shared-history hooks in messaging.
- `03e1c8e` — outreach-note quality regression tests.
- `ea412fb` — supervised Track 2 end-to-end hardening.
- `c9fe4ee` — run-scoped nightly reporting and communication learning.
- `93da83e` — recruiting intelligence, cadence, watchlist, role, email, and outcome loop.
- `62f6039` — shared discovery, company/news, affinity, and PeopleGrove features.
- `fff9b25` / `5899fd9` — live invite failures made fail-closed and reportable.
- `d5e4c0c` — attested/pushed `main` HEAD at narrative audit time.

### Audit note

At the time of this brief, both operating repos were on `main` and each returned `0 0` for `origin/main...HEAD`. Outreach still had modified runtime workbook CSVs, while ResumeGenerator had separate story-workbench changes/untracked files. The released code paths were recorded clean in the production attestation; the broader working directories should not be described as entirely clean.

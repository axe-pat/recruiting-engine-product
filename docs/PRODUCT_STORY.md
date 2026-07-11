# Recruiting Engine product story

## Resume title

**Recruiting Engine — Product Manager & AI-Native Builder | Mar–Jul 2026**

## Resume summary

Built and operated a multi-source recruiting decision engine that converts job,
company, and relationship signals into reviewed application and outreach actions;
evolved it through 151 commits across two repositories into an attested nightly
production pipeline with 542 release tests.

## Resume bullets

- Designed and shipped a multi-source recruiting engine spanning professional
  networks, university sources, job aggregators, startup directories, company/news
  signals, and relationship sources; routed targets into apply,
  apply-plus-outreach, follow-up, review, watch, or skip workflows.
- Served as product manager and first user across a 96-day, 151-commit build
  cycle, translating real operating failures into cost-aware model routing,
  entity-first state, exact provenance, idempotent imports, human review gates,
  and fail-closed execution.
- Operationalized an attested 1:00 a.m. nightly pipeline with 542 passing release
  tests; the reviewed product snapshot spans 2,514 roles, 560 organizations, 846
  contacts, and 849 tracked touchpoints.

## 90-second interview story

I started this as a resume-tailoring tool. It could read a job description,
extract the strongest signals, choose relevant stories from my background, and
generate a tailored resume. It worked, but using it made me realize that writing
was not the real bottleneck. The harder problem was deciding which opportunities
deserved attention, whether to apply or build a relationship, who to contact, and
when to follow up.

I became both the PM and the first customer. I used AI coding agents as my
engineering team, while owning the roadmap, tradeoffs, acceptance criteria, and
production reviews. Over 96 days of Git history, the product expanded into two
connected systems: ResumeGenerator for applications and Outreach for
relationships. Together they ingest multiple source families, build a reviewed
daily queue, prepare application and relationship actions, track state and
outcomes, and run through an attested nightly production path.

The most important iteration came from a failure. A canary fallback matched one
person to the wrong company and sent a connection invite. I stopped the batch,
withdrew the invite, and changed the product contract so a candidate cannot send
without independent current-employer evidence. Then I added regression coverage
across every send path. That was when it stopped feeling like an automation script
and started feeling like a real product.

The current release has 542 attested tests, and the system has tracked 2,514
roles, 560 organizations, and 849 touchpoints. My biggest lesson was that AI
product management is not about adding a model to a workflow. It is about building
the state, evidence, guardrails, and feedback loops that make model-driven
decisions trustworthy.

## Credit framing

Use this wording:

> I acted as the PM and first customer, and used AI coding agents as my
> engineering team. I owned the problem definition, roadmap, tradeoffs,
> acceptance criteria, operating reviews, and the decision to ship or roll back
> behavior. The agents accelerated implementation; the product judgment came from
> operating the workflow and turning failures into requirements.

## Claims boundary

- Say 151 commits, not a thousand working sessions.
- Say the verifiable build history spans 96 days, with earlier dated product
  evidence in March.
- Do not describe the product as multi-tenant, monetized, or fully autonomous.
- Do not publish personal documents, messages, people records, credentials, or
  private production artifacts.
- Treat the hosted product as a sanitized proof surface, not live production
  telemetry.


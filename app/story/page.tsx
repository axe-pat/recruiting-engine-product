import type { Metadata } from "next";
import Link from "next/link";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";
import { currentProof, snapshotMeta } from "@/lib/product-data";

export const metadata: Metadata = {
  title: "The product story",
  description:
    "How months of dogfooding, product judgment, AI-native engineering, and real operating failures turned a resume tool into a production recruiting engine.",
};

const chapters = [
  {
    label: "01 · The first problem",
    title: "It began as a writing tool. Usage revealed a decision problem.",
    body:
      "The earliest version extracted hiring signals from a job description, chose the strongest stories from my background, generated a tailored resume, and checked the output. It worked—but operating it every day made the bigger bottleneck obvious. The hard part was not producing another document. It was deciding which opportunities deserved attention, which story to tell, whether to apply or build a relationship, and what to do next.",
    quote: "A job search is not a writing problem. It is a noisy, stateful decision-and-execution problem.",
  },
  {
    label: "02 · The product loop",
    title: "I stopped imagining a user. I became the research loop.",
    body:
      "I served as the PM, first customer, and operator. Every weak recommendation, duplicated queue item, stale metric, noisy source, awkward message, or unsafe browser action appeared inside my own workflow. I translated those moments into product requirements, acceptance criteria, and operating rules, then used AI coding agents as a high-velocity engineering team to ship and test them.",
    quote: "Observe → prioritize → specify → ship → operate → inspect evidence → change the product.",
  },
  {
    label: "03 · The architecture",
    title: "Two specialized engines became one recruiting operating system.",
    body:
      "ResumeGenerator owns application intelligence: discovery, eligibility, fit, application state, story selection, and tailored materials. Outreach owns relationship intelligence: organizations, people, touchpoints, contextual communication, and outcome learning. A shared queue merges their evidence without creating a third source of truth. That separation made the product easier to reason about—and safer to operate.",
    quote: "Share the decision surface. Keep the systems of record specialized.",
  },
  {
    label: "04 · Production",
    title: "Dev and prod became operating contracts, not labels.",
    body:
      "The production path is scheduled, bounded, and evidence-backed. Exact manifests tie reports to the run that produced them. Feature work is tested away from the unattended path. Irreversible actions have human gates, hard limits, and reconciliation state. The current release is bound to exact Git SHAs across both repositories with a passing release attestation.",
    quote: "Production means recoverable failure—not a perfect demo.",
  },
  {
    label: "05 · The failure",
    title: "One wrong invite created a stronger product contract.",
    body:
      "A canary fallback once associated a person with the wrong company and sent one connection invite. I stopped the remaining batch, withdrew the invite, confirmed that no message had been sent, and converted the incident into a fail-closed rule across every send surface: coverage-only candidates cannot execute without independent current-employer evidence. The incident is part of the proof, not something hidden from the story.",
    quote: "The product matured when a real failure became a permanent regression test.",
  },
] as const;

export default function StoryPage() {
  return (
    <main className="inner-page">
      <SiteNav />

      <header className="page-intro">
        <span className="eyebrow">Product case study · {snapshotMeta.buildWindow}</span>
        <h1>How a personal workflow became a production AI product.</h1>
        <p>
          This is a story about product management through use: finding the real bottleneck,
          building the smallest useful system, operating it on consequential work, and turning
          every failure into a sharper product contract.
        </p>
      </header>

      <section className="story-thesis">
        <div className="section-shell">
          <div className="story-thesis-copy">
            <span className="story-chapter-label">The thesis</span>
            <h2>
              I acted as the PM and first customer. <em>AI agents became the engineering team.</em>
            </h2>
            <p>
              I owned the problem definition, roadmap, tradeoffs, acceptance criteria, operating
              reviews, and the decision to ship or roll back behavior. The agents accelerated
              implementation. The product judgment came from {snapshotMeta.activeBuildDays} days
              of using the workflow and turning observed friction into requirements.
            </p>
          </div>
          <aside className="story-aside">
            <span className="micro-label">Verifiable build history</span>
            <strong>{snapshotMeta.repositoryCommits} commits</strong>
            <p>
              Across two cooperating repositories, from the first Git snapshots through the
              attested July production release.
            </p>
          </aside>
        </div>
      </section>

      <section className="story-chapters" aria-label="Product evolution chapters">
        {chapters.map((chapter) => (
          <article className="story-chapter" key={chapter.label}>
            <span className="story-chapter-label">{chapter.label}</span>
            <div>
              <h2>{chapter.title}</h2>
              <p>{chapter.body}</p>
              <blockquote>{chapter.quote}</blockquote>
            </div>
          </article>
        ))}
      </section>

      <section className="section-shell">
        <div className="section-heading">
          <span className="section-index">What exists today</span>
          <h2>
            The interface is new. <em>The operating evidence is not.</em>
          </h2>
        </div>
        <div className="proof-grid">
          <article className="proof-card">
            <span className="micro-label">Unique roles tracked</span>
            <strong>{currentProof.applicationEngine.uniqueRolesTracked.toLocaleString()}</strong>
            <p>Application records across active and archived operating state.</p>
          </article>
          <article className="proof-card">
            <span className="micro-label">Organizations modeled</span>
            <strong>{currentProof.tracker.organizations}</strong>
            <p>Entity-first company state—not one spreadsheet per source.</p>
          </article>
          <article className="proof-card">
            <span className="micro-label">Touchpoints reconciled</span>
            <strong>{currentProof.tracker.touchpoints}</strong>
            <p>Planned, attempted, completed, and outcome-bearing relationship work.</p>
          </article>
          <article className="proof-card">
            <span className="micro-label">Attested release tests</span>
            <strong>
              {currentProof.verification.outreachTestsPassed +
                currentProof.verification.applicationEngineTestsPassed}
            </strong>
            <p>Across both production repositories at the current release boundary.</p>
          </article>
        </div>
        <div className="hero-actions">
          <Link className="button-primary" href="/architecture">
            Inspect the architecture <span aria-hidden="true">→</span>
          </Link>
          <Link className="button-secondary" href="/">
            Return to the product <span aria-hidden="true">↗</span>
          </Link>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}


import type { CSSProperties } from "react";
import type { Metadata } from "next";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";
import { roleCoverage, sourceHealth } from "@/lib/product-data";

export const metadata: Metadata = {
  title: "System architecture",
  description:
    "The product architecture behind Recruiting Engine: source adapters, decision intelligence, specialized execution lanes, human gates, and run-scoped evidence.",
};

const sourceNodes = [
  ["Role sources", "Professional networks, university boards, broad job aggregators"],
  ["Company signals", "Startup directories, hiring surfaces, funding and news adapters"],
  ["Relationship context", "Reviewed warm networks and low-frequency alumni sources"],
] as const;

const intelligenceNodes = [
  ["Normalize", "Stable identity, deduplication, and source provenance"],
  ["Evaluate", "Eligibility, role family, fit, freshness, and confidence"],
  ["Route", "Apply, outreach, follow-up, research, watch, or skip"],
  ["Prioritize", "One capped daily queue with explicit human gates"],
] as const;

const executionNodes = [
  ["Application lane", "Tailored strategy, resume, optional cover letter, assist-before-submit"],
  ["Relationship lane", "People mapping, contextual drafts, bounded execution, reconciliation"],
  ["Learning loop", "Coverage, outcomes, source health, failures, and reviewed recommendations"],
] as const;

const guardrails = [
  {
    title: "Exact-run provenance",
    body: "Reports point to the artifacts produced by that run. A convenient newer file cannot silently replace missing evidence.",
  },
  {
    title: "Human authority",
    body: "High-cost or irreversible decisions stop at review. The product prepares and recommends; it does not erase operator judgment.",
  },
  {
    title: "Fail-closed execution",
    body: "Missing company evidence, uncertain delivery, timeouts, or incomplete state block retries and preserve an audit path.",
  },
  {
    title: "Bounded automation",
    body: "Every live workflow has daily budgets, per-company caps, stop rules, and isolated workers that can be terminated safely.",
  },
  {
    title: "Private-by-design demo",
    body: "This hosted surface uses aggregate evidence and fictional examples—not real people, applications, messages, or credentials.",
  },
  {
    title: "Advisory learning",
    body: "Outcomes can propose an experiment, but they never rewrite communication or targeting policy without review.",
  },
] as const;

export default function ArchitecturePage() {
  return (
    <main className="inner-page">
      <SiteNav />

      <header className="architecture-hero">
        <span className="eyebrow">Architecture · Portable product view</span>
        <h1>A decision system with execution attached—not automation looking for a problem.</h1>
        <p>
          Shared discovery creates one next-action surface. Specialized application and
          relationship systems preserve the state, evidence, and controls each workflow needs.
        </p>
      </header>

      <section className="architecture-map" aria-label="Recruiting Engine system map">
        <div className="architecture-columns">
          <div className="architecture-column">
            <span className="micro-label">01 · Inputs</span>
            <h2>Market evidence</h2>
            {sourceNodes.map(([title, description], index) => (
              <div key={title}>
                <article className="architecture-node">
                  <strong>{title}</strong>
                  <span>{description}</span>
                </article>
                {index < sourceNodes.length - 1 ? (
                  <p className="architecture-arrow">plus</p>
                ) : null}
              </div>
            ))}
          </div>

          <div className="architecture-column">
            <span className="micro-label">02 · Core</span>
            <h2>Decision intelligence</h2>
            {intelligenceNodes.map(([title, description], index) => (
              <div key={title}>
                <article className="architecture-node">
                  <strong>{title}</strong>
                  <span>{description}</span>
                </article>
                {index < intelligenceNodes.length - 1 ? (
                  <p className="architecture-arrow">then</p>
                ) : null}
              </div>
            ))}
          </div>

          <div className="architecture-column">
            <span className="micro-label">03 · Outputs</span>
            <h2>Bounded action</h2>
            {executionNodes.map(([title, description], index) => (
              <div key={title}>
                <article className="architecture-node">
                  <strong>{title}</strong>
                  <span>{description}</span>
                </article>
                {index < executionNodes.length - 1 ? (
                  <p className="architecture-arrow">and</p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section-shell">
        <div className="section-heading">
          <span className="section-index">Role surface</span>
          <h2>
            Product stays primary. <em>Adjacent value stays visible.</em>
          </h2>
        </div>
        <div className="coverage-panel">
          <div className="coverage-head">
            <span>Role family</span>
            <span>Discovered</span>
            <span>Scored</span>
            <span>Surfaced</span>
          </div>
          {roleCoverage.map((role) => {
            const width = Math.max(4, Math.round((role.discovered / 94) * 100));
            const style = { "--coverage-width": `${width}%` } as CSSProperties;
            return (
              <article className="coverage-row" key={role.id} style={style}>
                <div className="coverage-label">
                  <strong>{role.label}</strong>
                  <span>{role.coverageState === "floor-met" ? "Floor met" : "Below floor"}</span>
                </div>
                <strong>{role.discovered}</strong>
                <strong>{role.scored}</strong>
                <strong>{role.surfaced}</strong>
                <i aria-hidden="true" />
              </article>
            );
          })}
        </div>
      </section>

      <section className="source-health-section">
        <div className="section-shell">
          <div className="section-heading">
            <span className="section-index">Run evidence</span>
            <h2>
              Zero, skipped, and failed are <em>different product states.</em>
            </h2>
          </div>
          <div className="source-health-grid">
            {sourceHealth.map((source) => (
              <article className="source-health-card" key={source.id}>
                <div>
                  <span className={`health-status health-${source.status}`}>{source.status}</span>
                  <span className="micro-label">{source.runScope.replaceAll("-", " ")}</span>
                </div>
                <h3>{source.label}</h3>
                <p>{source.detail}</p>
                <dl>
                  <div>
                    <dt>Observed</dt>
                    <dd>{source.observed ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Advanced</dt>
                    <dd>{source.advanced ?? "—"}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section-shell">
        <div className="section-heading">
          <span className="section-index">Guardrails</span>
          <h2>
            The model is useful because the system knows <em>when to stop it.</em>
          </h2>
        </div>
        <div className="guardrails-grid">
          {guardrails.map((guardrail, index) => (
            <article className="guardrail-card" key={guardrail.title}>
              <span className="micro-label">G{String(index + 1).padStart(2, "0")}</span>
              <strong>{guardrail.title}</strong>
              <p>{guardrail.body}</p>
            </article>
          ))}
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}


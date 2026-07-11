import Link from "next/link";

import { EngineConsole, type ConsoleQueueItem, type ConsoleStage } from "@/components/EngineConsole";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";
import {
  demoQueue,
  engineStages,
  evolutionMilestones,
  proofMetrics,
  snapshotMeta,
  type QueueGate,
  type QueueLane,
} from "@/lib/product-data";

const stageProof: Record<string, string> = {
  discover: "6 source families, exact-run scoped",
  score: "402 observations classified",
  route: "119 companies unified",
  execute: "50-item operating cap",
  learn: "623 sends measured",
};

const selectedStageIds = ["discover", "score", "route", "execute", "learn"] as const;

const consoleStages: ConsoleStage[] = selectedStageIds.map((id, index) => {
  const stage = engineStages.find((item) => item.id === id);
  if (!stage) throw new Error(`Missing public engine stage: ${id}`);

  return {
    id: stage.id,
    index: String(index + 1).padStart(2, "0"),
    label: stage.label,
    shortLabel: stage.shortLabel,
    description: stage.description,
    proof: stageProof[id],
    status: stage.mode.replaceAll("-", " "),
  };
});

const laneLabels: Record<QueueLane, ConsoleQueueItem["lane"]> = {
  application_plus_outreach: "Apply + Outreach",
  application_only: "Apply",
  company_outreach: "Relationship",
  relationship_follow_up: "Relationship",
  role_watch: "Watch",
};

const gateLabels: Record<QueueGate, string> = {
  ready_for_next_stage: "Ready for review",
  human_review_required: "Human gate",
  monitor_only: "Monitor only",
};

const consoleQueue: ConsoleQueueItem[] = demoQueue.map((item) => ({
  company: item.companyAlias,
  role: item.role,
  lane: laneLabels[item.lane],
  score: item.fitScore,
  action: item.nextAction.split(".")[0],
  status: gateLabels[item.gate],
}));

const tickerItems = [
  "Multi-source discovery",
  "Decision intelligence",
  "Role-specific applications",
  "Relationship outreach",
  "Run-scoped evidence",
  "Human-gated execution",
];

const principles = [
  {
    title: "Decisions before documents",
    body: "The product does not begin by writing. It first decides whether an opportunity deserves attention, which lane owns it, and what evidence is still missing.",
  },
  {
    title: "State before cleverness",
    body: "Roles, companies, people, touchpoints, and outcomes stay durable. AI works inside a system that remembers what happened instead of restarting from a blank prompt.",
  },
  {
    title: "Automation earns scope",
    body: "Consequential actions remain bounded, reviewable, and recoverable. Missing evidence blocks execution; ambiguous delivery reserves the slot until reality is reconciled.",
  },
  {
    title: "Failures become contracts",
    body: "A real canary miss was stopped, withdrawn, and converted into a permanent rule: no live outreach without independent current-employer evidence.",
  },
] as const;

export default function Home() {
  return (
    <main className="page-shell">
      <SiteNav />

      <section className="hero" id="product">
        <div className="hero-copy">
          <p className="eyebrow">Production AI product · {snapshotMeta.buildWindow}</p>
          <h1>
            The job search,
            <em>rebuilt as a product.</em>
          </h1>
          <p className="hero-description">
            Recruiting Engine turns fragmented job, company, and relationship signals into one
            reviewed decision: apply, reach out, follow up, watch, or skip. I built and operated it
            as the product manager, first customer, and daily user—using AI coding agents as my
            engineering team.
          </p>
          <div className="hero-actions">
            <a className="button-primary" href="#proof">
              Explore the product <span aria-hidden="true">↓</span>
            </a>
            <Link className="button-secondary" href="/story">
              Read the build story <span aria-hidden="true">→</span>
            </Link>
          </div>
          <div className="hero-meta" aria-label="Build facts">
            <div>
              <strong>{snapshotMeta.activeBuildDays}</strong>
              <span>Versioned build days</span>
            </div>
            <div>
              <strong>{snapshotMeta.repositoryCommits}</strong>
              <span>Commits across two repos</span>
            </div>
            <div>
              <strong>1:00a</strong>
              <span>Production nightly run</span>
            </div>
          </div>
        </div>

        <div className="hero-product">
          <EngineConsole stages={consoleStages} queue={consoleQueue} />
        </div>
      </section>

      <div className="signal-strip" aria-hidden="true">
        <div className="signal-strip-inner">
          {[...tickerItems, ...tickerItems].map((item, index) => (
            <span key={`${item}-${index}`}>{item}</span>
          ))}
        </div>
      </div>

      <section className="section-shell" id="proof">
        <div className="section-heading">
          <span className="section-index">01 / Proof</span>
          <h2>
            Not a concept deck. <em>A used system.</em>
          </h2>
        </div>
        <div className="proof-grid">
          {proofMetrics.map((metric) => (
            <article className="proof-card" key={metric.id}>
              <span className="micro-label">{metric.label}</span>
              <strong>{metric.displayValue}</strong>
              <p>{metric.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="principles-section">
        <div className="section-shell">
          <div className="section-heading">
            <span className="section-index">02 / Product judgment</span>
            <h2>
              AI generates options. <em>The product creates trust.</em>
            </h2>
          </div>
          <div className="principle-list">
            {principles.map((principle, index) => (
              <article className="principle-row" key={principle.title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{principle.title}</h3>
                <p>{principle.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="section-shell">
        <div className="section-heading">
          <span className="section-index">03 / Evolution</span>
          <h2>
            Dogfooding turned a tool into an <em>operating system.</em>
          </h2>
        </div>
        <div className="evolution-grid">
          {evolutionMilestones.map((milestone) => (
            <article className="evolution-card" key={milestone.id}>
              <time>{milestone.date}</time>
              <h3>{milestone.title}</h3>
              <p>{milestone.description}</p>
              <small>{milestone.proof.join(" · ")}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="section-shell">
        <div className="section-heading">
          <span className="section-index">04 / Build evidence</span>
          <h2>
            Two real repositories. <em>One product loop.</em>
          </h2>
        </div>
        <div className="repo-proof">
          <a
            className="repo-card"
            href="https://github.com/axe-pat/Resume-generator"
            target="_blank"
            rel="noreferrer"
          >
            <div>
              <span className="micro-label">Application intelligence</span>
              <h3>ResumeGenerator</h3>
              <p>
                Discovers and scores roles, maintains application state, routes model spend, and
                prepares tailored, reviewable application packages.
              </p>
            </div>
            <span className="repo-arrow" aria-hidden="true">
              ↗
            </span>
          </a>
          <a
            className="repo-card"
            href="https://github.com/axe-pat/Outreach"
            target="_blank"
            rel="noreferrer"
          >
            <div>
              <span className="micro-label">Relationship intelligence</span>
              <h3>Outreach</h3>
              <p>
                Maps companies and people, prioritizes relationship actions, drafts contextual
                communication, executes bounded workflows, and learns from outcomes.
              </p>
            </div>
            <span className="repo-arrow" aria-hidden="true">
              ↗
            </span>
          </a>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}

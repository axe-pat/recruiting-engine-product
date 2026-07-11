/**
 * Public-safe product evidence for the Recruiting Engine showcase.
 *
 * This module is deliberately static. It contains aggregate telemetry from a
 * reviewed production snapshot plus unmistakably fictional UI examples. The
 * hosted product must never import the private operating workspaces directly.
 */

export type EvidenceProvenance =
  | "aggregate-real-run"
  | "aggregate-repository-history"
  | "fictional-demo";

export type AutomationMode =
  | "automated"
  | "policy-gated"
  | "human-gated"
  | "supervised";

export type SourceStatus =
  | "healthy"
  | "no-yield"
  | "scheduled-off"
  | "human-gated"
  | "partial-failure";

export type QueueLane =
  | "application_plus_outreach"
  | "application_only"
  | "company_outreach"
  | "relationship_follow_up"
  | "role_watch";

export type QueueGate =
  | "ready_for_next_stage"
  | "human_review_required"
  | "monitor_only";

export interface EngineStage {
  id: string;
  order: number;
  label: string;
  shortLabel: string;
  mode: AutomationMode;
  description: string;
  input: string;
  output: string;
  safety: string;
}

export interface ProofMetric {
  id: string;
  label: string;
  value: number;
  displayValue: string;
  detail: string;
  provenance: Exclude<EvidenceProvenance, "fictional-demo">;
}

export interface RoleCoverageMetric {
  id: string;
  label: string;
  discovered: number;
  scored: number;
  surfaced: number;
  coverageState: "floor-met" | "below-floor";
}

export interface SourceHealthMetric {
  id: string;
  label: string;
  runScope: "audited-nightly" | "reviewed-canary";
  status: SourceStatus;
  observed: number | null;
  advanced: number | null;
  detail: string;
}

export interface ProductMilestone {
  id: string;
  date: string;
  phase: string;
  title: string;
  description: string;
  proof: readonly string[];
}

export interface DemoQueueItem {
  id: string;
  demo: true;
  dataClass: "fictional-demo";
  companyAlias: string;
  role: string;
  lane: QueueLane;
  gate: QueueGate;
  fitScore: number;
  confidence: "high" | "medium";
  sourceSignals: readonly string[];
  whyNow: string;
  nextAction: string;
  safety: string;
}

export const snapshotMeta = {
  asOf: "2026-07-11",
  evidenceLabel: "Reviewed production snapshot",
  buildWindow: "April–July 2026",
  activeBuildDays: 96,
  calendarMonthsTouched: 4,
  repositories: 2,
  repositoryCommits: 151,
  privacyMode: "aggregate-only",
  liveWorkspaceConnected: false,
  disclaimer:
    "Real metrics are aggregate snapshots. Queue records are fictional demonstrations, not real candidates, people, or target companies.",
} as const;

export const engineStages = [
  {
    id: "discover",
    order: 1,
    label: "Discover the market",
    shortLabel: "Discover",
    mode: "automated",
    description:
      "Collect live roles, startup hiring signals, company news, and relationship context from multiple source families.",
    input: "Source adapters and bounded browser captures",
    output: "Provenance-preserving observations",
    safety: "Every source reports ran, skipped, or failed; missing evidence never masquerades as a successful run.",
  },
  {
    id: "normalize",
    order: 2,
    label: "Normalize and deduplicate",
    shortLabel: "Normalize",
    mode: "automated",
    description:
      "Resolve repeated roles and company signals into a stable operating record while retaining source lineage.",
    input: "Raw observations from heterogeneous sources",
    output: "Canonical roles, companies, and source links",
    safety: "Stable identity keys and idempotent imports prevent duplicate people, companies, and actions.",
  },
  {
    id: "score",
    order: 3,
    label: "Evaluate fit",
    shortLabel: "Score",
    mode: "policy-gated",
    description:
      "Apply deterministic eligibility filters first, then score role fit across product, strategy, operations, and adjacent lanes.",
    input: "Normalized role evidence and a private candidate profile",
    output: "Fit score, decision, role family, and rationale",
    safety: "Hard exclusions run before model spend; low-confidence or incomplete evidence is routed to review.",
  },
  {
    id: "route",
    order: 4,
    label: "Choose the right motion",
    shortLabel: "Route",
    mode: "policy-gated",
    description:
      "Decide whether a signal deserves an application, company outreach, relationship follow-up, research, or monitoring.",
    input: "Fit, affinity, freshness, and current relationship state",
    output: "A lane-specific action with an explicit gate",
    safety: "A company signal cannot become a person-level send without independent current-employer evidence.",
  },
  {
    id: "generate",
    order: 5,
    label: "Tailor application materials",
    shortLabel: "Tailor",
    mode: "supervised",
    description:
      "Build role-specific strategy and tailored application materials from a private, reusable story and experience corpus.",
    input: "Approved role, job evidence, and private source materials",
    output: "Reviewable role strategy and tailored documents",
    safety: "Generation is cost-tiered, quality-checked, and kept private; no resume content is shipped with this showcase.",
  },
  {
    id: "prioritize",
    order: 6,
    label: "Assemble the daily queue",
    shortLabel: "Prioritize",
    mode: "automated",
    description:
      "Merge application, outreach, warm-network, watchlist, and follow-up work into one ranked operating surface.",
    input: "Exact-run application actions plus outreach workspace state",
    output: "A deduplicated, capped daily queue",
    safety: "The queue records exact inputs, merges duplicate evidence, and separates ready work from human review.",
  },
  {
    id: "review",
    order: 7,
    label: "Apply human judgment",
    shortLabel: "Review",
    mode: "human-gated",
    description:
      "Review high-impact decisions, targeting, generated materials, and communication drafts before irreversible actions.",
    input: "Ranked actions and review context",
    output: "Approved, edited, held, or rejected actions",
    safety: "No public demo item can execute and no held item silently advances.",
  },
  {
    id: "execute",
    order: 8,
    label: "Execute bounded workflows",
    shortLabel: "Execute",
    mode: "supervised",
    description:
      "Run approved application assistance, connection, follow-up, and research workflows within explicit budgets.",
    input: "Human-approved actions",
    output: "Attempt records and durable delivery state",
    safety: "Per-item caps, hard timeouts, prelaunch reservations, and fail-closed ambiguity prevent blind retries.",
  },
  {
    id: "reconcile",
    order: 9,
    label: "Reconcile reality",
    shortLabel: "Reconcile",
    mode: "supervised",
    description:
      "Compare planned actions with actual platform state and preserve a trustworthy audit trail of what happened.",
    input: "Execution progress and signed-in platform state",
    output: "Confirmed outcomes, exceptions, and next actions",
    safety: "Unknown delivery remains reserved until explicitly reconciled; reports distinguish plans from confirmed sends.",
  },
  {
    id: "learn",
    order: 10,
    label: "Learn and improve",
    shortLabel: "Learn",
    mode: "human-gated",
    description:
      "Aggregate acceptance, reply, source, and role-coverage evidence to propose the next product or operating change.",
    input: "Run-scoped evidence and outcome telemetry",
    output: "Reviewed recommendations and product backlog updates",
    safety: "Outcomes can recommend an experiment; they never rewrite prompts or policy automatically.",
  },
] as const satisfies readonly EngineStage[];

export const proofMetrics = [
  {
    id: "build-days",
    label: "Days of iteration",
    value: 96,
    displayValue: "96",
    detail: "From the first repository snapshot to the reviewed production release.",
    provenance: "aggregate-repository-history",
  },
  {
    id: "commits",
    label: "Repository commits",
    value: 151,
    displayValue: "151",
    detail: "Combined current-branch history across the application and outreach engines.",
    provenance: "aggregate-repository-history",
  },
  {
    id: "tests",
    label: "Passing tests",
    value: 542,
    displayValue: "542",
    detail: "482 outreach tests plus 60 application-engine tests in the attested production release.",
    provenance: "aggregate-real-run",
  },
  {
    id: "role-observations",
    label: "Role observations audited",
    value: 402,
    displayValue: "402",
    detail: "One exact-source replay spanning six source families and 220 unique companies.",
    provenance: "aggregate-real-run",
  },
  {
    id: "daily-company-universe",
    label: "Companies unified",
    value: 119,
    displayValue: "119",
    detail: "Deduplicated from 128 observations before the daily queue's 50-item operating cap.",
    provenance: "aggregate-real-run",
  },
  {
    id: "relationship-contacts",
    label: "Relationship leads",
    value: 174,
    displayValue: "174",
    detail: "Reviewed, source-backed contacts across low-frequency relationship channels.",
    provenance: "aggregate-real-run",
  },
  {
    id: "touchpoints",
    label: "Touchpoints tracked",
    value: 849,
    displayValue: "849",
    detail: "Aggregate lifecycle records across planned, attempted, completed, and reconciled work.",
    provenance: "aggregate-real-run",
  },
  {
    id: "outcome-sends",
    label: "Outreach sends measured",
    value: 623,
    displayValue: "623",
    detail: "The reviewed learning corpus also recorded 49 accepts and 17 replies.",
    provenance: "aggregate-real-run",
  },
] as const satisfies readonly ProofMetric[];

export const currentProof = {
  asOf: snapshotMeta.asOf,
  applicationEngine: {
    uniqueRolesTracked: 2514,
    appliedStatusRecords: 253,
    resumeDocumentsPresent: 280,
    coverLetterDocumentsPresent: 172,
  },
  tracker: {
    organizations: 560,
    opportunities: 412,
    contacts: 846,
    touchpoints: 849,
    sourceFamilies: 13,
  },
  reviewedRelationshipImport: {
    profilesCaptured: 1845,
    imported: 135,
    excluded: 1710,
    guessedEmails: 0,
    guessedProfileUrls: 0,
    idempotentRerunChanges: 0,
  },
  sharedQueue: {
    observations: 128,
    uniqueCompanies: 119,
    returned: 50,
    readyForNextStage: 18,
    humanReviewRequired: 32,
    duplicatesMerged: 9,
    strategicAccountsWatched: 57,
  },
  outcomes: {
    sends: 623,
    accepts: 49,
    replies: 17,
    acceptRatePercent: 7.9,
    replyRatePercent: 2.7,
    automaticPolicyChanges: 0,
  },
  verification: {
    outreachTestsPassed: 482,
    applicationEngineTestsPassed: 60,
    staticAnalysis: "passed",
    activeSendProcessesAtHandoff: 0,
  },
  trustSignals: {
    sourcesRan: 6,
    sourcesSkipped: 0,
    sourcesFailed: 0,
    roleFamiliesBelowFloor: 0,
    confirmedInvitesInAuditedNightly: 5,
    postIncidentRegressionSurfaces: 5,
  },
} as const;

export const roleCoverage = [
  {
    id: "product",
    label: "Product",
    discovered: 94,
    scored: 5,
    surfaced: 5,
    coverageState: "floor-met",
  },
  {
    id: "product-strategy",
    label: "Product Strategy",
    discovered: 1,
    scored: 0,
    surfaced: 0,
    coverageState: "floor-met",
  },
  {
    id: "business-operations",
    label: "Business Operations & Strategy",
    discovered: 8,
    scored: 2,
    surfaced: 2,
    coverageState: "floor-met",
  },
  {
    id: "program-operations",
    label: "Program & Operations",
    discovered: 24,
    scored: 5,
    surfaced: 5,
    coverageState: "floor-met",
  },
  {
    id: "growth-adjacent",
    label: "Growth-adjacent",
    discovered: 7,
    scored: 2,
    surfaced: 2,
    coverageState: "floor-met",
  },
] as const satisfies readonly RoleCoverageMetric[];

export const sourceHealth = [
  {
    id: "professional-network-jobs",
    label: "Professional network jobs",
    runScope: "audited-nightly",
    status: "healthy",
    observed: 47,
    advanced: 2,
    detail: "The source ran without errors; strict scoring advanced only two roles.",
  },
  {
    id: "professional-network-feed",
    label: "Professional network feed",
    runScope: "audited-nightly",
    status: "healthy",
    observed: 5,
    advanced: 3,
    detail: "Five reviewed signals produced three additions and two updates.",
  },
  {
    id: "job-aggregation",
    label: "Broad job aggregation",
    runScope: "audited-nightly",
    status: "healthy",
    observed: 302,
    advanced: 60,
    detail: "Forty-four items moved to review, thirteen to outreach context, and three to immediate scoring.",
  },
  {
    id: "startup-sources",
    label: "Startup and company sources",
    runScope: "audited-nightly",
    status: "healthy",
    observed: 65,
    advanced: 60,
    detail: "The application and relationship lanes both ran in the exact nightly scope.",
  },
  {
    id: "university-job-board",
    label: "University job board",
    runScope: "audited-nightly",
    status: "no-yield",
    observed: 0,
    advanced: 0,
    detail: "The source ran successfully but returned no qualifying observations in this cycle.",
  },
  {
    id: "profile-viewer-context",
    label: "Passive profile-viewer context",
    runScope: "audited-nightly",
    status: "scheduled-off",
    observed: 0,
    advanced: 0,
    detail: "Intentionally skipped because this low-frequency context source was not scheduled.",
  },
  {
    id: "company-news",
    label: "Company and news adapters",
    runScope: "reviewed-canary",
    status: "human-gated",
    observed: 21,
    advanced: 2,
    detail: "A separate reviewed canary captured two research signals; neither was auto-promoted.",
  },
  {
    id: "relationship-execution",
    label: "Relationship execution",
    runScope: "audited-nightly",
    status: "partial-failure",
    observed: null,
    advanced: null,
    detail: "A hung browser action was stopped and reconciled; timeout, reservation, and exact-company regression guards were then added.",
  },
] as const satisfies readonly SourceHealthMetric[];

export const evolutionMilestones = [
  {
    id: "foundation",
    date: "April 2026",
    phase: "Personal workflow",
    title: "A resume generator becomes a repeatable pipeline",
    description:
      "The first system moved from one-off document work to live role discovery, eligibility checks, fit scoring, a durable tracker, and role-specific generation.",
    proof: [
      "Initial application-engine snapshot on April 6",
      "Automated discovery and retry hardening",
      "Separate product and non-product generation routes",
    ],
  },
  {
    id: "connected-engine",
    date: "April 2026",
    phase: "Connected system",
    title: "Application intent starts driving outreach",
    description:
      "A second engine added multi-source company discovery and connected high-fit application context to bounded relationship workflows.",
    proof: [
      "Outreach scaffold on April 8",
      "Application-to-outreach bridge on April 16",
      "Send limits and browser-state reconciliation",
    ],
  },
  {
    id: "daily-operating-system",
    date: "May 2026",
    phase: "Daily operating system",
    title: "Sources, queues, and decisions become observable",
    description:
      "Source-breadth validation, a gated daily action queue, and a supervised engine turned scattered scripts into one reviewable operating rhythm.",
    proof: [
      "Source breadth and startup reports",
      "Pre-score and post-score queue artifacts",
      "Supervised daily engine with bounded send volume",
    ],
  },
  {
    id: "closed-loop",
    date: "June 2026",
    phase: "Closed loop",
    title: "The product learns from what happens next",
    description:
      "Follow-up reconciliation, account planning, company enrichment, cost controls, and role monitoring connected discovery to real outcomes.",
    proof: [
      "Reply-aware follow-up planning",
      "Account campaign scoring and context enrichment",
      "Run metrics and nightly overlap protection",
    ],
  },
  {
    id: "production",
    date: "July 2026",
    phase: "Production-grade personal product",
    title: "Dev, production, evidence, and safety converge",
    description:
      "The nightly orchestrator now joins both engines, emits exact-run reports, maintains a shared discovery queue, audits role coverage, and fails closed around irreversible actions.",
    proof: [
      "Run-scoped reporting across six source families",
      "Shared queue covering 119 deduplicated companies",
      "542 passing tests and durable delivery-state safeguards",
    ],
  },
] as const satisfies readonly ProductMilestone[];

export const demoQueueItems = [
  {
    id: "demo-queue-001",
    demo: true,
    dataClass: "fictional-demo",
    companyAlias: "Demo Company A",
    role: "AI Strategy Product Manager",
    lane: "application_plus_outreach",
    gate: "ready_for_next_stage",
    fitScore: 9.1,
    confidence: "high",
    sourceSignals: ["Fictional live role", "Fictional warm-company signal"],
    whyNow:
      "Strong role fit and a timely company signal converge in the same fictional example.",
    nextAction: "Review tailored application strategy and the outreach rationale.",
    safety: "Demo only—no person, URL, draft, or send target exists.",
  },
  {
    id: "demo-queue-002",
    demo: true,
    dataClass: "fictional-demo",
    companyAlias: "Demo Company B",
    role: "Product Operations Lead",
    lane: "application_only",
    gate: "human_review_required",
    fitScore: 8.6,
    confidence: "medium",
    sourceSignals: ["Fictional job-board role", "Fictional role-family match"],
    whyNow:
      "The fictional role clears the score threshold, but evidence quality still calls for human review.",
    nextAction: "Verify scope and decide whether to promote into the generation queue.",
    safety: "Demo only—no private job description or application material is included.",
  },
  {
    id: "demo-queue-003",
    demo: true,
    dataClass: "fictional-demo",
    companyAlias: "Demo Company C",
    role: "Business Operations, AI Programs",
    lane: "role_watch",
    gate: "monitor_only",
    fitScore: 8.2,
    confidence: "medium",
    sourceSignals: ["Fictional strategic account", "Fictional adjacent-role signal"],
    whyNow:
      "The fictional company is strategically interesting, but the role evidence is not strong enough to trigger action.",
    nextAction: "Keep on role watch and wait for independently verified evidence.",
    safety: "Demo only—monitoring cannot auto-create an outreach action.",
  },
] as const satisfies readonly DemoQueueItem[];

/** Stable, page-facing alias. */
export const demoQueue = demoQueueItems;

export const portableDataPolicy = {
  allowed: [
    "Aggregate counts",
    "System stages and safety rules",
    "Role-family coverage totals",
    "Source status without source URLs",
    "Repository-history aggregates",
    "Clearly labeled fictional UI examples",
  ],
  prohibited: [
    "Personal names or identifiers",
    "Email addresses or profile URLs",
    "Private message or draft text",
    "Resume, cover-letter, or story-bank content",
    "Real candidate or prospect records",
    "Credentials, browser state, or local file paths",
  ],
  runtimeRule:
    "The hosted showcase consumes this reviewed module only; it does not read the private production workspaces.",
} as const;

export const productData = {
  snapshotMeta,
  engineStages,
  proofMetrics,
  currentProof,
  roleCoverage,
  sourceHealth,
  evolutionMilestones,
  demoQueue,
  portableDataPolicy,
} as const;

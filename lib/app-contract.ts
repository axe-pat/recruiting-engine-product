export type EngineMode = "preview" | "portable" | "existing";

export type SourceState = "healthy" | "attention" | "skipped" | "offline";

export type SourceSummary = {
  id: string;
  label: string;
  state: SourceState;
  observed: number;
  advanced: number;
  note: string;
};

export type QueueItem = {
  id: string;
  company: string;
  role: string;
  lane: "Apply + Outreach" | "Apply" | "Relationship" | "Watch";
  score: number;
  status: "Ready" | "Review" | "Blocked" | "Watching";
  nextAction: string;
  source: string;
};

export type ApplicationItem = {
  id: string;
  company: string;
  role: string;
  status: "Prepared" | "Review" | "Applied" | "Interview" | "Closed";
  updatedAt: string;
};

export type OutreachItem = {
  id: string;
  company: string;
  recipient: string;
  channel: "LinkedIn" | "Email";
  status: "Draft" | "Approved" | "Sent" | "Replied" | "Held";
  preview: string;
  workflowState?: "draft" | "reviewed" | "approved" | "sent" | "replied" | "failed" | "cancelled";
};

export type RunSummary = {
  id: string;
  startedAt: string;
  completedAt?: string;
  status: "running" | "complete" | "attention" | "failed";
  mode: EngineMode;
  discovered: number;
  queued: number;
  reportId?: string;
};

export type ReportItem = {
  id: string;
  runId: string;
  title: string;
  createdAt: string;
  status: "complete" | "attention";
  summary: string;
};

export type DashboardSnapshot = {
  dataClass: "fictional-demo" | "local-private";
  generatedAt: string;
  profile: {
    displayName: string;
    target: string;
    onboardingComplete: boolean;
  };
  metrics: {
    discovered: number;
    reviewQueue: number;
    applications: number;
    outreach: number;
    replies: number;
  };
  sources: SourceSummary[];
  queue: QueueItem[];
  applications: ApplicationItem[];
  outreach: OutreachItem[];
  runs: RunSummary[];
  reports: ReportItem[];
  presentation?: {
    applications: { total: number; returned: number; truncated: boolean };
    outreach: { total: number; returned: number; truncated: boolean };
  };
};

export type CompanionConfig = {
  baseUrl: string;
  token: string;
};

export const defaultCompanionConfig: CompanionConfig = {
  baseUrl: "http://127.0.0.1:8765",
  token: "",
};

export const previewSnapshot: DashboardSnapshot = {
  dataClass: "fictional-demo",
  generatedAt: "Preview dataset · no private workspace connected",
  profile: {
    displayName: "Preview workspace",
    target: "Product, strategy, and operations",
    onboardingComplete: false,
  },
  metrics: {
    discovered: 128,
    reviewQueue: 16,
    applications: 12,
    outreach: 9,
    replies: 3,
  },
  sources: [
    {
      id: "professional-network",
      label: "Professional network",
      state: "skipped",
      observed: 0,
      advanced: 0,
      note: "Connect your own reviewed exports or lawful sources.",
    },
    {
      id: "university-board",
      label: "University job board",
      state: "healthy",
      observed: 36,
      advanced: 8,
      note: "Imported file passed normalization and freshness checks.",
    },
    {
      id: "jobspy",
      label: "JobSpy",
      state: "healthy",
      observed: 62,
      advanced: 12,
      note: "Broad-market discovery completed for configured role families.",
    },
    {
      id: "company-signals",
      label: "Company signals",
      state: "healthy",
      observed: 30,
      advanced: 6,
      note: "Reviewed company and news adapters contributed candidates.",
    },
  ],
  queue: [
    {
      id: "demo-q-1",
      company: "Northstar Labs",
      role: "Associate Product Manager",
      lane: "Apply + Outreach",
      score: 9.1,
      status: "Ready",
      nextAction: "Review tailored package",
      source: "University board",
    },
    {
      id: "demo-q-2",
      company: "Parcel Works",
      role: "Product Operations Associate",
      lane: "Apply",
      score: 8.7,
      status: "Review",
      nextAction: "Confirm location preference",
      source: "JobSpy",
    },
    {
      id: "demo-q-3",
      company: "Signal House",
      role: "Product Strategy Analyst",
      lane: "Relationship",
      score: 8.4,
      status: "Review",
      nextAction: "Approve contextual draft",
      source: "Company signal",
    },
    {
      id: "demo-q-4",
      company: "Cedar Systems",
      role: "Business Operations",
      lane: "Watch",
      score: 7.8,
      status: "Watching",
      nextAction: "Monitor for role opening",
      source: "Company signal",
    },
  ],
  applications: [
    {
      id: "demo-a-1",
      company: "Northstar Labs",
      role: "Associate Product Manager",
      status: "Prepared",
      updatedAt: "Today, 08:14",
    },
    {
      id: "demo-a-2",
      company: "Bluejay Health",
      role: "Product Analyst",
      status: "Applied",
      updatedAt: "Yesterday, 17:42",
    },
    {
      id: "demo-a-3",
      company: "Fieldcraft",
      role: "Strategy & Operations",
      status: "Interview",
      updatedAt: "Jul 08, 12:10",
    },
  ],
  outreach: [
    {
      id: "demo-o-1",
      company: "Signal House",
      recipient: "Product lead",
      channel: "LinkedIn",
      status: "Draft",
      preview: "Your team’s recent launch connected with a problem I’ve been working on…",
      workflowState: "draft",
    },
    {
      id: "demo-o-2",
      company: "Northstar Labs",
      recipient: "Product manager",
      channel: "Email",
      status: "Approved",
      preview: "I’m applying to the APM opening and wanted to share one relevant build…",
      workflowState: "approved",
    },
    {
      id: "demo-o-3",
      company: "Fieldcraft",
      recipient: "Operations leader",
      channel: "LinkedIn",
      status: "Replied",
      preview: "Thanks for reaching out — happy to find twenty minutes next week.",
      workflowState: "replied",
    },
  ],
  runs: [
    {
      id: "preview-0711",
      startedAt: "Jul 11, 01:00",
      completedAt: "Jul 11, 01:37",
      status: "complete",
      mode: "preview",
      discovered: 128,
      queued: 16,
      reportId: "preview-report-0711",
    },
    {
      id: "preview-0710",
      startedAt: "Jul 10, 01:00",
      completedAt: "Jul 10, 01:29",
      status: "attention",
      mode: "preview",
      discovered: 94,
      queued: 11,
      reportId: "preview-report-0710",
    },
  ],
  reports: [
    {
      id: "preview-report-0711",
      runId: "preview-0711",
      title: "Daily decision brief",
      createdAt: "Jul 11, 01:37",
      status: "complete",
      summary: "Four source families reconciled. Sixteen decisions await review; no live action occurred.",
    },
    {
      id: "preview-report-0710",
      runId: "preview-0710",
      title: "Daily decision brief",
      createdAt: "Jul 10, 01:29",
      status: "attention",
      summary: "One source timed out and is explicitly excluded. Eleven decisions remain trustworthy.",
    },
  ],
};

export function companionHeaders(token: string): HeadersInit {
  return token
    ? {
        Authorization: `Bearer ${token}`,
      }
    : {};
}

export function companionUrl(baseUrl: string, path: string): string {
  const parsed = new URL(baseUrl);
  const loopbackHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
  if (parsed.protocol !== "http:" || !loopbackHosts.has(parsed.hostname)) {
    throw new Error("The companion address must be an HTTP loopback address on this device.");
  }
  if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("Use only the companion origin, without credentials, a path, or query string.");
  }
  return new URL(path, parsed).toString();
}

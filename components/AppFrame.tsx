"use client";

/* eslint-disable @next/next/no-html-link-for-pages -- static export uses full-page route navigation */

import { useCallback, useEffect, useRef, useState } from "react";

import { OperatorWorkspace } from "@/components/OperatorWorkspace";

import {
  companionHeaders,
  companionUrl,
  defaultCompanionConfig,
  previewSnapshot,
  type CompanionConfig,
  type DashboardSnapshot,
  type EngineMode,
  type OutreachItem,
} from "@/lib/app-contract";
import type {
  OperatorActionResult,
  OperatorOverview,
  OperatorReportDocument,
  OperatorReview,
  OperatorReviewResult,
  OperatorReviewTargetDetail,
} from "@/lib/operator-contract";

export type AppView =
  | "dashboard"
  | "sources"
  | "queue"
  | "runs"
  | "applications"
  | "outreach"
  | "reports"
  | "accounts"
  | "stories"
  | "operations"
  | "settings";

type ConnectionState = "checking" | "connected" | "preview" | "error";

type ExistingEngineStatus = {
  configured?: boolean;
  roots_available?: boolean;
  production_guard?: string;
  verified_run_count?: number;
  latest_verified_run?: {
    run_id?: string;
    created_at?: string;
    status?: string;
  } | null;
  rejections?: string[];
};

type ExistingEngineSnapshot = {
  generated_at?: string;
  run_snapshot?: {
    scope?: string;
    status?: string;
    reason?: string;
    run_id?: string;
    started_at?: string;
    completed_at?: string;
    failure_count?: number;
    sources?: { source?: string; status?: string; raw_count?: number; kept_count?: number }[];
    queue?: { decision_total?: number; decision_total_name?: string; decision_total_parts?: Record<string, number>; counts?: Record<string, number> };
    report?: {
      workspace_counts?: Record<string, number>;
      invite_totals?: Record<string, number>;
      pending_review_count?: number;
      track_2_failed?: boolean;
    };
  };
  current_workspace?: {
    scope?: string;
    status?: string;
    captured_at?: string;
    reasons?: string[];
    application_queue?: {
      ready_count?: number;
      manual_review_count?: number;
      priority_item_count?: number;
      status_counts?: Record<string, number>;
      source_label_count?: number;
    } | null;
    outreach_counts?: Record<string, number>;
  };
};

const sessionConfigKey = "recruiting-engine.companion-session.v1";
const originStorageKey = "recruiting-engine.companion-origin.v1";

const navItems: { id: AppView; label: string; glyph: string; href: string }[] = [
  { id: "dashboard", label: "Command center", glyph: "⌁", href: "/app" },
  { id: "sources", label: "Sources", glyph: "◎", href: "/app/sources" },
  { id: "queue", label: "Decision queue", glyph: "◇", href: "/app/queue" },
  { id: "runs", label: "Runs", glyph: "↻", href: "/app/runs" },
  { id: "applications", label: "Applications", glyph: "▤", href: "/app/applications" },
  { id: "outreach", label: "Outreach", glyph: "↗", href: "/app/outreach" },
  { id: "reports", label: "Reports", glyph: "▥", href: "/app/reports" },
  { id: "accounts", label: "Accounts", glyph: "◉", href: "/app/accounts" },
  { id: "stories", label: "Stories", glyph: "✦", href: "/app/stories" },
  { id: "operations", label: "Operations", glyph: "⌘", href: "/app/operations" },
  { id: "settings", label: "Settings", glyph: "⚙", href: "/app/settings" },
];

function statusLabel(state: ConnectionState): string {
  if (state === "connected") return "Local companion online";
  if (state === "checking") return "Checking companion";
  if (state === "error") return "Session expired or unavailable";
  return "Not connected · no live data";
}

function safeSnapshot(value: unknown): DashboardSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const wrapped = value as { snapshot?: Record<string, unknown> };
  if (wrapped.snapshot) {
    const local = wrapped.snapshot;
    const counts = (local.counts ?? {}) as Record<string, number>;
    const recentRuns = Array.isArray(local.recent_runs) ? local.recent_runs : [];
    const recentReports = Array.isArray(local.recent_reports) ? local.recent_reports : [];
    const applicationItems = Array.isArray(local.application_items) ? local.application_items : [];
    const outreachItems = Array.isArray(local.outreach_items) ? local.outreach_items : [];
    const presentationMeta = local.presentation_meta && typeof local.presentation_meta === "object"
      ? (local.presentation_meta as Record<string, Record<string, unknown>>)
      : {};
    const actionQueue = Array.isArray(local.action_queue) ? local.action_queue : [];
    const latestReport = local.latest_report && typeof local.latest_report === "object"
      ? (local.latest_report as Record<string, unknown>)
      : null;
    const latestOutputCounts = latestReport?.output_counts && typeof latestReport.output_counts === "object"
      ? (latestReport.output_counts as Record<string, unknown>)
      : {};
    const reportByRun = new Map(recentReports.map((raw) => {
      const report = raw as Record<string, unknown>;
      return [String(report.run_id), report];
    }));
    const applicationStatus: Record<string, DashboardSnapshot["applications"][number]["status"]> = {
      planned: "Prepared",
      materials_ready: "Prepared",
      reviewed: "Review",
      submitted: "Applied",
      interviewing: "Interview",
      closed: "Closed",
      withdrawn: "Closed",
    };
    const outreachStatus: Record<string, DashboardSnapshot["outreach"][number]["status"]> = {
      draft: "Draft",
      reviewed: "Draft",
      approved: "Approved",
      sent: "Sent",
      replied: "Replied",
      failed: "Held",
      cancelled: "Held",
    };
    return {
      ...previewSnapshot,
      dataClass: "local-private",
      generatedAt: String(local.generated_at ?? "Generated by the local companion"),
      profile: {
        ...previewSnapshot.profile,
        displayName: local.profile_ready ? "Private workspace" : "New workspace",
        onboardingComplete: Boolean(local.profile_ready),
      },
      metrics: {
        discovered: Number(counts.jobs ?? actionQueue.length),
        reviewQueue: Number(latestOutputCounts.queue_items ?? actionQueue.length),
        applications: Number(counts.applications ?? 0),
        outreach: Number(counts.outreach ?? 0),
        replies: Number(((local.outreach_by_state ?? {}) as Record<string, number>).replied ?? 0),
      },
      sources: [
        {
          id: "local-documents",
          label: "Profile evidence",
          state: Number(counts.documents ?? 0) > 0 ? "healthy" : "attention",
          observed: Number(counts.documents ?? 0),
          advanced: Number(counts.documents ?? 0),
          note: Number(counts.documents ?? 0) > 0 ? "Stored in the private local workspace." : "Add a baseline resume to complete the profile.",
        },
        {
          id: "local-jobs",
          label: "Reviewed job imports",
          state: Number(counts.jobs ?? 0) > 0 ? "healthy" : "skipped",
          observed: Number(counts.jobs ?? 0),
          advanced: actionQueue.filter((item) => (item as Record<string, unknown>).entity_type === "job").length,
          note: Number(counts.jobs ?? 0) > 0 ? "Local records are normalized and run-scoped." : "No job source is configured yet.",
        },
        {
          id: "local-relationships",
          label: "Relationship records",
          state: Number(counts.contacts ?? 0) > 0 ? "healthy" : "skipped",
          observed: Number(counts.contacts ?? 0),
          advanced: Number(counts.outreach ?? 0),
          note: Number(counts.contacts ?? 0) > 0 ? "Drafts remain behind explicit review gates." : "No relationship source is configured yet.",
        },
      ],
      queue: actionQueue.map((raw, index) => {
        const item = raw as Record<string, unknown>;
        const action = String(item.action ?? "review");
        const entityType = String(item.entity_type ?? "job");
        const lane = action.includes("application")
          ? "Apply"
          : action.includes("relationship") || entityType === "outreach" || entityType === "contact"
            ? "Relationship"
            : "Watch";
        const gate = String(item.gate ?? "human_review_required");
        return {
          id: String(item.id ?? `local-q-${index}`),
          company: String(item.company ?? item.company_name ?? "Company pending"),
          role: String(item.label ?? item.role ?? item.title ?? "Opportunity"),
          lane: lane as DashboardSnapshot["queue"][number]["lane"],
          score: Number(item.score ?? item.fit_score ?? Number(item.priority ?? 0) / 10),
          status: (gate === "ready_for_external_execution" ? "Ready" : "Review") as DashboardSnapshot["queue"][number]["status"],
          nextAction: String(item.reason ?? item.next_action ?? action.replaceAll("_", " ")),
          source: Array.isArray(item.evidence) ? item.evidence.join(" · ").replaceAll("_", " ") : "Local record",
        };
      }),
      applications: applicationItems.map((raw) => {
        const item = raw as Record<string, unknown>;
        return {
          id: String(item.id),
          company: String(item.company || "Company pending"),
          role: String(item.role || "Opportunity"),
          status: applicationStatus[String(item.status)] ?? "Review",
          updatedAt: String(item.updated_at ?? ""),
        };
      }),
      outreach: outreachItems.map((raw) => {
        const item = raw as Record<string, unknown>;
        const rawState = String(item.state || "draft");
        return {
          id: String(item.id),
          company: String(item.company || "Company pending"),
          recipient: String(item.recipient || "Recipient pending review"),
          channel: String(item.channel).toLowerCase() === "email" ? "Email" : "LinkedIn",
          status: outreachStatus[rawState] ?? "Held",
          preview: String(item.text || "Draft content pending."),
          workflowState: rawState as OutreachItem["workflowState"],
        };
      }),
      runs: recentRuns.map((raw, index) => {
        const run = raw as Record<string, unknown>;
        return {
          id: String(run.id ?? `local-run-${index}`),
          startedAt: String(run.started_at ?? "Pending"),
          completedAt: run.completed_at ? String(run.completed_at) : undefined,
          status: String(run.status === "completed" ? "complete" : run.status ?? "running") as DashboardSnapshot["runs"][number]["status"],
          mode: String(run.run_type ?? run.mode ?? "portable") as EngineMode,
          discovered: Number(run.discovered ?? run.discovered_count ?? ((run.input_counts ?? {}) as Record<string, number>).jobs ?? 0),
          queued: Number(run.queued ?? run.queue_count ?? ((run.output_counts ?? {}) as Record<string, number>).queue_items ?? 0),
          reportId: reportByRun.get(String(run.id))?.id ? String(reportByRun.get(String(run.id))?.id) : undefined,
        };
      }),
      reports: recentReports.map((raw) => {
        const report = raw as Record<string, unknown>;
        return {
          id: String(report.id),
          runId: String(report.run_id),
          title: report.kind === "portable_queue" ? "Portable decision brief" : "Decision brief",
          createdAt: String(report.created_at ?? ""),
          status: (report.status === "completed" ? "complete" : "attention") as "complete" | "attention",
          summary: String(report.summary_text ?? "Run-scoped local report."),
        };
      }),
      presentation: {
        applications: {
          total: Number(presentationMeta.applications?.total ?? applicationItems.length),
          returned: Number(presentationMeta.applications?.returned ?? applicationItems.length),
          truncated: Boolean(presentationMeta.applications?.truncated),
        },
        outreach: {
          total: Number(presentationMeta.outreach?.total ?? outreachItems.length),
          returned: Number(presentationMeta.outreach?.returned ?? outreachItems.length),
          truncated: Boolean(presentationMeta.outreach?.truncated),
        },
      },
    };
  }
  const candidate = value as Partial<DashboardSnapshot>;
  if (!candidate.metrics || !Array.isArray(candidate.queue) || !Array.isArray(candidate.runs)) {
    return null;
  }
  return {
    ...previewSnapshot,
    ...candidate,
    dataClass: "local-private",
    profile: { ...previewSnapshot.profile, ...candidate.profile },
    metrics: { ...previewSnapshot.metrics, ...candidate.metrics },
    sources: Array.isArray(candidate.sources) ? candidate.sources : [],
    queue: candidate.queue,
    applications: Array.isArray(candidate.applications) ? candidate.applications : [],
    outreach: Array.isArray(candidate.outreach) ? candidate.outreach : [],
    runs: candidate.runs,
    reports: Array.isArray(candidate.reports) ? candidate.reports : [],
  };
}

function operatorShellSnapshot(generatedAt?: string): DashboardSnapshot {
  return {
    dataClass: "local-private",
    generatedAt: generatedAt || "Generated by the minimized operator projection",
    profile: {
      ...previewSnapshot.profile,
      displayName: "Private operator workspace",
      onboardingComplete: true,
    },
    metrics: { discovered: 0, reviewQueue: 0, applications: 0, outreach: 0, replies: 0 },
    sources: [],
    queue: [],
    applications: [],
    outreach: [],
    runs: [],
    reports: [],
    presentation: {
      applications: { total: 0, returned: 0, truncated: false },
      outreach: { total: 0, returned: 0, truncated: false },
    },
  };
}

async function apiRequest<T>(
  config: CompanionConfig,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(companionUrl(config.baseUrl, path), {
    ...init,
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: {
      ...companionHeaders(config.token),
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Companion returned ${response.status}`);
  }
  return (await response.json()) as T;
}

async function exchangePairingToken(config: CompanionConfig): Promise<CompanionConfig> {
  if (config.token.startsWith("re_web_")) return config;
  if (config.token.startsWith("re_local_")) {
    throw new Error("Use a one-time pairing token in the hosted app. Keep the long-lived local token in the Chrome companion.");
  }
  if (!config.token.startsWith("re_pair_")) {
    throw new Error("Enter the one-time pairing token printed by the local companion.");
  }
  const response = await fetch(companionUrl(config.baseUrl, "/api/v1/pair"), {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairing_token: config.token, client_type: "web" }),
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { error?: { message?: unknown } };
      if (typeof payload.error?.message === "string") detail = payload.error.message;
    } catch {
      // Keep the fallback concise; never echo an HTML body or submitted credential.
    }
    const safeDetail = detail
      .replace(/re_(?:pair|web|local)_[A-Za-z0-9_-]+/g, "[credential redacted]")
      .replace(/[\r\n\t]+/g, " ")
      .slice(0, 180);
    throw new Error(
      `${safeDetail || "The one-time pairing token was rejected or already used."} Pairing tokens work once; generate a fresh token only after disconnecting this tab.`,
    );
  }
  const payload = (await response.json()) as { bearer_token?: string };
  if (!payload.bearer_token) throw new Error("The companion did not return a device token.");
  return { ...config, token: payload.bearer_token };
}

export function AppFrame({ view }: { view: AppView }) {
  const [config, setConfig] = useState<CompanionConfig>(defaultCompanionConfig);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(previewSnapshot);
  const [notice, setNotice] = useState("");
  const [running, setRunning] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<"portable" | "existing">("portable");
  const [existingEngine, setExistingEngine] = useState<ExistingEngineStatus | null>(null);
  const [existingSnapshot, setExistingSnapshot] = useState<ExistingEngineSnapshot | null>(null);
  const [operatorOverview, setOperatorOverview] = useState<OperatorOverview | null>(null);
  const [preferencesData, setPreferencesData] = useState<Record<string, unknown>>({});
  const [autoReviewCommandId, setAutoReviewCommandId] = useState("");

  const loadDashboard = useCallback(async (nextConfig: CompanionConfig) => {
    try {
      await apiRequest(nextConfig, "/api/v1/health");
      // Resolve mode before choosing a read model. Existing-engine mode must not
      // fetch the portable dashboard because that DTO can contain recipient and
      // message-body fields that the minimized operator contract intentionally omits.
      const preferences = await apiRequest<{ preferences?: Record<string, unknown> }>(nextConfig, "/api/v1/preferences");
      const nextMode = preferences.preferences?.mode === "existing" ? "existing" : "portable";
      let existing: { existing_engine?: ExistingEngineStatus } = {};
      let existingEvidence: { existing_engine?: ExistingEngineSnapshot } = {};
      let operatorPayload: { operator?: OperatorOverview } = {};
      let nextSnapshot: DashboardSnapshot;
      if (nextMode === "existing") {
        [existing, existingEvidence, operatorPayload] = await Promise.all([
          apiRequest<{ existing_engine?: ExistingEngineStatus }>(nextConfig, "/api/v1/existing-engine/status"),
          apiRequest<{ existing_engine?: ExistingEngineSnapshot }>(nextConfig, "/api/v1/existing-engine/snapshot"),
          apiRequest<{ operator?: OperatorOverview }>(nextConfig, "/api/v1/operator/overview"),
        ]);
        nextSnapshot = operatorShellSnapshot(operatorPayload.operator?.generated_at);
      } else {
        const payload = await apiRequest<unknown>(nextConfig, "/api/v1/dashboard");
        const portableSnapshot = safeSnapshot(payload);
        if (!portableSnapshot) throw new Error("The companion returned an incompatible dashboard payload.");
        nextSnapshot = portableSnapshot;
      }
      setSnapshot(nextSnapshot);
      setWorkspaceMode(nextMode);
      setPreferencesData(preferences.preferences ?? {});
      setExistingEngine(existing.existing_engine ?? null);
      setExistingSnapshot(existingEvidence.existing_engine ?? null);
      setOperatorOverview(operatorPayload.operator ?? null);
      setConnection("connected");
      setNotice("");
      return true;
    } catch (error) {
      setSnapshot(previewSnapshot);
      setOperatorOverview(null);
      setConnection(nextConfig.token ? "error" : "preview");
      setNotice(error instanceof Error ? error.message : "Could not reach the companion.");
      return false;
    }
  }, []);

  useEffect(() => {
    let nextConfig = defaultCompanionConfig;
    try {
      const storedOrigin = window.localStorage.getItem(originStorageKey);
      if (storedOrigin) nextConfig = { ...nextConfig, baseUrl: storedOrigin };
      const storedSession = window.sessionStorage.getItem(sessionConfigKey);
      if (storedSession) nextConfig = { ...nextConfig, ...(JSON.parse(storedSession) as CompanionConfig) };
    } catch {
      // Corrupt device-local config falls back to the safe loopback default.
    }
    const hydrate = async () => {
      await Promise.resolve();
      setConfig(nextConfig);
      if (!nextConfig.token) {
        setConnection("preview");
        return;
      }
      await loadDashboard(nextConfig);
    };
    void hydrate();
  }, [loadDashboard]);

  useEffect(() => {
    if (view !== "runs") return;
    const requested = new URLSearchParams(window.location.search).get("start");
    if (requested !== "nightly") return;
    const timer = window.setTimeout(() => setAutoReviewCommandId("nightly.run"), 0);
    return () => window.clearTimeout(timer);
  }, [view]);

  useEffect(() => {
    if (!mobileNav) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNav(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileNav]);

  useEffect(() => {
    const active = operatorOverview?.recent_jobs?.some((job) => job.status === "queued" || job.status === "running");
    if (!active || connection !== "connected" || workspaceMode !== "existing") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await apiRequest<{ items?: OperatorOverview["recent_jobs"] }>(config, "/api/v1/operator/jobs?limit=10");
        if (cancelled) return;
        const jobs = payload.items ?? [];
        setOperatorOverview((current) => current ? { ...current, recent_jobs: jobs } : current);
        if (!jobs.some((job) => job.status === "queued" || job.status === "running")) await loadDashboard(config);
      } catch {
        // The next explicit refresh will recover a transient local polling error.
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [config, connection, loadDashboard, operatorOverview?.recent_jobs, workspaceMode]);

  const runEngine = async () => {
    if (connection !== "connected") {
      setNotice("Pair the local companion before starting a real run.");
      return;
    }
    if (workspaceMode === "existing") {
      window.location.assign("/app/runs?start=nightly");
      return;
    }
    setRunning(true);
    setNotice("Starting a bounded run…");
    try {
      await apiRequest(config, "/api/v1/runs", {
        method: "POST",
        body: JSON.stringify({ type: "portable", config: {} }),
      });
      setNotice("Run accepted. The companion owns execution and evidence collection.");
      await loadDashboard(config);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The run could not start.");
    } finally {
      setRunning(false);
    }
  };

  const refreshCockpit = async () => {
    if (connection !== "connected") return;
    setRunning(true);
    setNotice("Refreshing live local evidence and execution guards…");
    await loadDashboard(config);
    setRunning(false);
  };

  const saveConfig = async (nextConfig: CompanionConfig) => {
    const sameAddress = nextConfig.baseUrl.trim().replace(/\/$/, "") === config.baseUrl.trim().replace(/\/$/, "");
    if (connection === "connected" && sameAddress && config.token.startsWith("re_web_")) {
      setNotice("Already connected. The pairing token was consumed once and this tab is using its short-lived private session; do not paste it again.");
      return;
    }
    let normalized = {
      baseUrl: nextConfig.baseUrl.trim().replace(/\/$/, ""),
      token: nextConfig.token.trim(),
    };
    setConnection("checking");
    try {
      normalized = await exchangePairingToken(normalized);
    } catch (error) {
      setConnection("error");
      setNotice(error instanceof Error ? error.message : "Pairing failed.");
      return;
    }
    window.localStorage.setItem(originStorageKey, normalized.baseUrl);
    window.sessionStorage.setItem(sessionConfigKey, JSON.stringify(normalized));
    setConfig(normalized);
    const connected = await loadDashboard(normalized);
    if (connected) setNotice("Paired. Private records remain on this device.");
  };

  const approveOutreach = async (item: OutreachItem, reviewedText: string) => {
    if (connection !== "connected") {
      setNotice("Approval is disabled in the fictional preview workspace.");
      return { approved: false, state: item.workflowState ?? "draft" } as const;
    }
    let authoritativeState: OutreachItem["workflowState"] = item.workflowState ?? "draft";
    try {
      const editedAfterReview = item.workflowState === "reviewed" && reviewedText !== item.preview;
      if (editedAfterReview) {
        await apiRequest(config, `/api/v1/outreach/${encodeURIComponent(item.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ state: "draft", actor: "local-user", note: "Returned to draft for an edited review" }),
        });
        authoritativeState = "draft";
      }
      if (item.workflowState !== "reviewed" || editedAfterReview) {
        await apiRequest(config, `/api/v1/outreach/${encodeURIComponent(item.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ state: "reviewed", actor: "local-user", reviewed_text: reviewedText, note: "Recipient and content reviewed in the command center" }),
        });
        authoritativeState = "reviewed";
      }
      await apiRequest(config, `/api/v1/outreach/${encodeURIComponent(item.id)}/approve`, {
        method: "POST",
        body: JSON.stringify({ actor: "local-user", note: "Explicit approval in the command center" }),
      });
      setNotice("Draft approved for the next explicit channel action. Nothing was auto-sent.");
      await loadDashboard(config);
      return { approved: true, state: "approved" } as const;
    } catch (error) {
      await loadDashboard(config);
      setNotice(error instanceof Error ? error.message : "Approval failed.");
      return { approved: false, state: authoritativeState } as const;
    }
  };

  const runOperatorAction = async (
    commandId: string,
    confirmation: string,
    parameters: Record<string, unknown> = {},
  ): Promise<OperatorActionResult | null> => {
    if (connection !== "connected" || workspaceMode !== "existing") {
      setNotice("Pair this Mac and enable Existing engine before running a local operator action.");
      return null;
    }
    try {
      const rawResult = await apiRequest<OperatorActionResult>(config, "/api/v1/operator/jobs", {
        method: "POST",
        body: JSON.stringify({ command_id: commandId, confirmation, parameters }),
      });
      const result = { ...rawResult, job: rawResult.job ?? rawResult.operator_job };
      setNotice(result.message || result.job?.summary || "Local action accepted and recorded.");
      await loadDashboard(config);
      return result;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The local action could not run.");
      return null;
    }
  };

  const loadOperatorReviewTarget = async (
    targetId: string,
  ): Promise<OperatorReviewTargetDetail | null> => {
    if (connection !== "connected" || workspaceMode !== "existing") return null;
    try {
      const result = await apiRequest<OperatorReviewResult>(
        config,
        `/api/v1/operator/review-targets/${encodeURIComponent(targetId)}/detail`,
      );
      return result.review_target ?? null;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The exact review target is no longer available.");
      return null;
    }
  };

  const loadOperatorReview = async (
    reviewId: string,
  ): Promise<OperatorReviewResult | null> => {
    if (connection !== "connected" || workspaceMode !== "existing") return null;
    try {
      return await apiRequest<OperatorReviewResult>(
        config,
        `/api/v1/operator/reviews/${encodeURIComponent(reviewId)}/detail`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The stored private review detail is no longer available.");
      return null;
    }
  };

  const createOperatorReview = async (
    commandId: string,
    targetId: string,
    reviewedText: string,
    reviewedSubject: string,
  ): Promise<OperatorReview | null> => {
    try {
      const result = await apiRequest<OperatorReviewResult>(config, "/api/v1/operator/reviews", {
        method: "POST",
        body: JSON.stringify({
          command_id: commandId,
          target_id: targetId,
          reviewed_text: reviewedText,
          reviewed_subject: reviewedSubject,
        }),
      });
      await loadDashboard(config);
      return result.operator_review ?? null;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The review could not be staged.");
      return null;
    }
  };

  const updateOperatorReviewContent = async (
    reviewId: string,
    reviewedText: string,
    reviewedSubject: string,
    confirmation: string,
  ): Promise<OperatorReviewResult | null> => {
    try {
      const result = await apiRequest<OperatorReviewResult>(
        config,
        `/api/v1/operator/reviews/${encodeURIComponent(reviewId)}/content`,
        {
          method: "PUT",
          body: JSON.stringify({
            reviewed_text: reviewedText,
            reviewed_subject: reviewedSubject,
            confirmation,
          }),
        },
      );
      await loadDashboard(config);
      return result;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The exact review content could not be updated.");
      return null;
    }
  };

  const transitionOperatorReview = async (
    reviewId: string,
    transition: "review" | "approve" | "revoke",
    confirmation: string,
  ): Promise<OperatorReview | null> => {
    try {
      const result = await apiRequest<OperatorReviewResult>(
        config,
        `/api/v1/operator/reviews/${encodeURIComponent(reviewId)}/${transition}`,
        { method: "POST", body: JSON.stringify({ confirmation }) },
      );
      await loadDashboard(config);
      return result.operator_review ?? null;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The review state did not change.");
      return null;
    }
  };

  const loadOperatorReport = async (runId: string): Promise<OperatorReportDocument | null> => {
    if (connection !== "connected" || workspaceMode !== "existing") {
      setNotice("Pair this Mac before opening a private exact report.");
      return null;
    }
    try {
      const result = await apiRequest<{ report?: OperatorReportDocument }>(
        config,
        `/api/v1/operator/reports/${encodeURIComponent(runId)}/html`,
      );
      if (!result.report) throw new Error("The companion returned no exact report document.");
      return result.report;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The exact report could not be opened.");
      return null;
    }
  };

  const importJobs = async (file: File, sourceLabel: string) => {
    if (connection !== "connected") {
      setNotice("Pair the local companion before importing a private source file.");
      return;
    }
    const payload = new FormData();
    payload.set("file", file);
    payload.set("source_label", sourceLabel);
    try {
      const result = await apiRequest<{ import?: Record<string, unknown> }>(config, "/api/v1/imports/jobs", {
        method: "POST",
        body: payload,
      });
      const summary = result.import ?? {};
      setNotice(`Import complete: ${Number(summary.imported ?? 0)} added, ${Number(summary.skipped ?? 0)} skipped.`);
      await loadDashboard(config);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The source file could not be imported.");
    }
  };

  const changeWorkspaceMode = async (nextMode: "portable" | "existing") => {
    if (connection !== "connected") {
      setNotice("Pair the local companion before changing workspace mode.");
      return;
    }
    try {
      await apiRequest(config, "/api/v1/preferences", {
        method: "PUT",
        body: JSON.stringify({ preferences: { ...preferencesData, mode: nextMode } }),
      });
      setWorkspaceMode(nextMode);
      setPreferencesData((current) => ({ ...current, mode: nextMode }));
      setNotice(nextMode === "existing" ? "Private operator cockpit enabled. Fixed local actions are available only when their guards pass." : "Portable local workspace enabled.");
      await loadDashboard(config);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Workspace mode could not be changed.");
    }
  };

  const disconnectCompanion = () => {
    window.sessionStorage.removeItem(sessionConfigKey);
    setConfig((current) => ({ ...current, token: "" }));
    setSnapshot(previewSnapshot);
    setConnection("preview");
    setWorkspaceMode("portable");
    setExistingEngine(null);
    setExistingSnapshot(null);
    setOperatorOverview(null);
    setNotice("This tab is disconnected. The local companion and its data were not changed.");
  };

  const active = navItems.find((item) => item.id === view) ?? navItems[0];
  const mode: EngineMode = connection === "connected" ? "portable" : "preview";
  const operatorReviewProps = {
    onLoadReviewTarget: loadOperatorReviewTarget,
    onLoadReview: loadOperatorReview,
    onCreateReview: createOperatorReview,
    onUpdateReviewContent: updateOperatorReviewContent,
    onTransitionReview: transitionOperatorReview,
    onLoadReport: loadOperatorReport,
    autoReviewCommandId,
  };

  return (
    <main className="operating-app">
      <aside className={mobileNav ? "app-sidebar sidebar-open" : "app-sidebar"} id="product-navigation">
        <button
          className="app-sidebar-close"
          type="button"
          onClick={() => setMobileNav(false)}
          aria-label="Close navigation"
        >
          ×
        </button>
        <a className="app-brand" href="/" aria-label="Recruiting Engine product site">
          <span>RE</span>
          <div>
            <strong>Recruiting Engine</strong>
            <small>Decision OS</small>
          </div>
        </a>

        <div className="workspace-chip">
          <span className={`connection-dot dot-${connection}`} />
          <div>
            <small>Workspace</small>
            <strong>{connection === "connected" ? snapshot.profile.displayName : connection === "checking" ? "Checking this Mac" : "No live workspace"}</strong>
          </div>
        </div>

        <nav aria-label="Product navigation">
          {navItems.map((item) => (
            <a
              className={item.id === view ? "active" : ""}
              href={item.href}
              key={item.id}
            >
              <span aria-hidden="true">{item.glyph}</span>
              {item.label}
            </a>
          ))}
        </nav>

        <div className="app-sidebar-foot">
          <a href="/app/onboarding">+ New workspace</a>
          <a href="/story">Product story ↗</a>
          <p>Local-first · Human-gated</p>
        </div>
      </aside>

      <section className="app-main">
        <header className="app-topbar">
          <button
            className="mobile-nav-button"
            type="button"
            onClick={() => setMobileNav((value) => !value)}
            aria-label="Toggle navigation"
            aria-expanded={mobileNav}
            aria-controls="product-navigation"
          >
            ☰
          </button>
          <div>
            <span className="app-breadcrumb">Workspace / {active.label}</span>
            <h1>{active.label}</h1>
          </div>
          <div className="topbar-actions">
            <span className={`connection-pill connection-${connection}`}>
              <i /> {statusLabel(connection)}
            </span>
            {connection === "connected" && workspaceMode === "existing" ? <button className="refresh-button" type="button" onClick={refreshCockpit} disabled={running}>↻ Refresh</button> : null}
            <button className="run-button" type="button" onClick={runEngine} disabled={running} aria-label={workspaceMode === "existing" ? "Open the reviewed end-to-end nightly run" : "Run the portable engine"}>
              <span>{running ? "Working" : workspaceMode === "existing" ? "Run E2E" : connection === "connected" ? "Run engine" : "Connect to run"}</span>
              <b aria-hidden="true">{running ? "…" : "▶"}</b>
            </button>
          </div>
        </header>

        {notice ? (
          <div className="app-notice" role="status">
            <span>{notice}</span>
            {connection !== "connected" ? <a href="/app/settings">Connect companion →</a> : null}
            <button type="button" onClick={() => setNotice("")} aria-label="Dismiss notice">
              ×
            </button>
          </div>
        ) : null}

        <div className="app-content">
          {view !== "settings" && connection !== "connected" ? (
            <ConnectionGate connection={connection} view={view} />
          ) : (
          <>
          {view === "dashboard" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <Dashboard snapshot={snapshot} mode={mode} workspaceMode={workspaceMode} existingEngine={existingEngine} existingSnapshot={existingSnapshot} />) : null}
          {view === "sources" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <SourcesView snapshot={snapshot} onImport={importJobs} />) : null}
          {view === "queue" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <QueueView snapshot={snapshot} />) : null}
          {view === "runs" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <RunsView snapshot={snapshot} />) : null}
          {view === "applications" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <ApplicationsView snapshot={snapshot} />) : null}
          {view === "outreach" ? (
            workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <OutreachView snapshot={snapshot} onApprove={approveOutreach} />
          ) : null}
          {view === "reports" ? (workspaceMode === "existing" ? <OperatorWorkspace view={view} overview={operatorOverview} connected={connection === "connected"} onAction={runOperatorAction} {...operatorReviewProps} /> : <ReportsView snapshot={snapshot} />) : null}
          {view === "accounts" || view === "stories" || view === "operations" ? <OperatorWorkspace view={view} overview={workspaceMode === "existing" ? operatorOverview : null} connected={connection === "connected" && workspaceMode === "existing"} onAction={runOperatorAction} {...operatorReviewProps} /> : null}
          {view === "settings" ? (
            <SettingsView
              key={`${config.baseUrl}|${config.token}`}
              config={config}
              connection={connection}
              snapshot={snapshot}
              workspaceMode={workspaceMode}
              existingEngine={existingEngine}
              onModeChange={changeWorkspaceMode}
              onDisconnect={disconnectCompanion}
              onSave={saveConfig}
            />
          ) : null}
          </>
          )}
        </div>
      </section>
    </main>
  );
}

function ConnectionGate({ connection, view }: { connection: ConnectionState; view: AppView }) {
  const checking = connection === "checking";
  return (
    <section className="operator-empty app-panel connection-gate" aria-live="polite">
      <span className="operator-kicker">{checking ? "Checking private companion" : "Live data is disconnected"}</span>
      <h2>{checking ? "Looking for your local recruiting engine…" : `Connect this Mac to open ${navItems.find((item) => item.id === view)?.label.toLowerCase() || "this workspace"}.`}</h2>
      <p>
        {checking
          ? "No records will appear until the authenticated local workspace responds."
          : "No company, queue, run, or report shown on this screen is mock data. Pair the local companion to load your real workspace; fictional preview records are no longer rendered on operational routes."}
      </p>
      {!checking ? <div><a href="/app/settings">Connect this Mac →</a><a href="/install">Open pairing guide ↗</a></div> : null}
    </section>
  );
}

function SourcesView({
  snapshot,
  onImport,
}: {
  snapshot: DashboardSnapshot;
  onImport: (file: File, sourceLabel: string) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("Handshake export");
  const [importing, setImporting] = useState(false);
  const connectors = [
    { name: "Handshake", mode: "Reviewed file import", state: "available", detail: "Bring a CSV export into the same normalized decision queue." },
    { name: "Chrome companion", mode: "User-triggered intake", state: "available", detail: "Capture a page or selected context after an explicit click." },
    { name: "JobSpy", mode: "Existing-engine adapter", state: "operator", detail: "Broad role discovery runs in the separately installed engine." },
    { name: "LinkedIn", mode: "Existing-engine only", state: "operator", detail: "The public extension does not scrape or automate LinkedIn." },
    { name: "Company + news", mode: "Reviewed adapters", state: "operator", detail: "Candidate signals enter the same approval pipeline." },
  ] as const;

  const submit = async () => {
    if (!file) return;
    setImporting(true);
    await onImport(file, source);
    setImporting(false);
  };

  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="Source control"
        title="Connect evidence without hiding where it came from."
        body="Portable users begin with reviewed imports and explicit browser intake. Existing operators can bind the richer source engine without changing the UI contract."
      />
      <div className="source-workbench">
        <div className="connector-list">
          {connectors.map((connector) => (
            <article key={connector.name}>
              <span className={`connector-glyph connector-${connector.state}`}>{connector.state === "available" ? "✓" : "↗"}</span>
              <div><strong>{connector.name}</strong><small>{connector.mode}</small><p>{connector.detail}</p></div>
              <span className="state-tag">{connector.state}</span>
            </article>
          ))}
        </div>
        <aside className="source-import-card">
          <span className="app-kicker">Portable intake</span>
          <h3>Import a reviewed job file</h3>
          <p>CSV rows are validated, normalized, and deduplicated locally. A file import never submits an application.</p>
          <label>Source label<select value={source} onChange={(event) => setSource(event.target.value)}><option>Handshake export</option><option>Job board export</option><option>Manual research</option><option>Other reviewed source</option></select></label>
          <label className="compact-upload"><input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><span>{file ? file.name : "Choose CSV"}</span><b>{file ? "Replace" : "+ Add"}</b></label>
          <button className="run-button" type="button" onClick={submit} disabled={!file || importing} aria-label="Import the selected source file"><span>{importing ? "Importing" : "Import source"}</span><b aria-hidden="true">{importing ? "…" : "→"}</b></button>
          <small>{snapshot.dataClass === "local-private" ? "Connected to your private companion." : "Preview mode · pair the companion to import."}</small>
        </aside>
      </div>
    </section>
  );
}

function Dashboard({ snapshot, mode, workspaceMode, existingEngine, existingSnapshot }: { snapshot: DashboardSnapshot; mode: EngineMode; workspaceMode: "portable" | "existing"; existingEngine: ExistingEngineStatus | null; existingSnapshot: ExistingEngineSnapshot | null }) {
  const existingReady = Boolean(existingEngine?.roots_available && existingEngine.production_guard === "configured" && (existingEngine.verified_run_count ?? 0) > 0);
  const runEvidence = existingSnapshot?.run_snapshot;
  const currentEvidence = existingSnapshot?.current_workspace;
  const existingRunReady = Boolean(existingReady && runEvidence?.run_id && runEvidence.status !== "unavailable");
  const existingMetrics = workspaceMode === "existing" ? {
    discovered: existingRunReady ? Number(runEvidence?.queue?.decision_total ?? 0) : "—",
    reviewQueue: existingRunReady ? Number(runEvidence?.report?.pending_review_count ?? 0) : "—",
    applications: Number(currentEvidence?.application_queue?.status_counts?.applied ?? 0),
    outreach: Number(currentEvidence?.outreach_counts?.touchpoints ?? 0),
  } : snapshot.metrics;
  const metricCards = [
    [workspaceMode === "existing" ? "Verified queue" : mode === "preview" ? "Discovered" : "Jobs in workspace", existingMetrics.discovered, workspaceMode === "existing" ? existingRunReady ? "Exact run scope" : "Unavailable" : mode === "preview" ? "Preview run" : "Local inventory", "signal"],
    [workspaceMode === "existing" ? "Pending review" : mode === "preview" ? "Needs review" : "Latest queue", existingMetrics.reviewQueue, workspaceMode === "existing" ? existingRunReady ? "Exact report" : "Unavailable" : "Latest local run", "coral"],
    ["Applications", existingMetrics.applications, workspaceMode === "existing" ? "Current snapshot" : mode === "preview" ? "Prepared + sent" : "Current records", "violet"],
    ["Outreach", existingMetrics.outreach, workspaceMode === "existing" ? "Current snapshot" : mode === "preview" ? `${snapshot.metrics.replies} replies` : "Current records", "blue"],
  ] as const;

  return (
    <>
      <section className="app-intro">
        <div>
          <span className="app-kicker">{mode === "preview" ? "Fictional preview data" : "Private local data"}</span>
          <h2>
            Good morning. <em>Your next decisions are ready.</em>
          </h2>
          <p>{snapshot.profile.target} · {snapshot.generatedAt}</p>
        </div>
        <a className="quiet-action" href="/app/reports">
          Open latest brief <span>↗</span>
        </a>
      </section>

      {workspaceMode === "existing" ? (
        <aside className={`engine-binding ${existingReady ? "binding-ready" : "binding-attention"}`}>
          <span>{existingReady ? "Existing evidence verified" : "Existing evidence needs attention"}</span>
          <strong>{existingEngine?.latest_verified_run?.run_id ? `Latest verified run ${existingEngine.latest_verified_run.run_id}` : "No exact run is currently eligible for display"}</strong>
          <small>{existingEngine?.latest_verified_run?.status ?? existingEngine?.rejections?.[0] ?? "Configure the two engine roots and production attestation in the companion."}</small>
          <a href="/app/settings">Inspect binding →</a>
        </aside>
      ) : null}

      <section className="metric-ribbon" aria-label="Workspace metrics">
        {metricCards.map(([label, value, detail, accent]) => (
          <article className={`metric-tile accent-${accent}`} key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{detail}</small>
          </article>
        ))}
      </section>

      {workspaceMode === "existing" ? (
        <ExistingEvidencePanel evidence={existingSnapshot} />
      ) : null}

      {workspaceMode !== "existing" ? <div className="dashboard-grid">
        <section className="app-panel decision-panel">
          <PanelHeading eyebrow="Priority queue" title="What deserves attention now" href="/app/queue" />
          <div className="decision-list">
            {snapshot.queue.slice(0, 4).map((item) => (
              <article key={item.id}>
                <span className={`score-orb score-${Math.floor(item.score)}`}>{item.score.toFixed(1)}</span>
                <div>
                  <strong>{item.company}</strong>
                  <p>{item.role}</p>
                  <small>{item.source} · {item.lane}</small>
                </div>
                <div className="decision-next">
                  <span className={`state-tag state-${item.status.toLowerCase()}`}>{item.status}</span>
                  <p>{item.nextAction}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="app-panel source-panel">
          <PanelHeading eyebrow={mode === "preview" ? "Preview run state" : "Local workspace"} title={mode === "preview" ? "Source health" : "Configured inputs"} href="/app/sources" />
          <div className="source-list">
            {snapshot.sources.map((source) => (
              <article key={source.id}>
                <span className={`source-state source-${source.state}`} />
                <div>
                  <strong>{source.label}</strong>
                  <small>{source.note}</small>
                </div>
                <dl>
                  <div>
                    <dt>Seen</dt>
                    <dd>{source.observed}</dd>
                  </div>
                  <div>
                    <dt>Kept</dt>
                    <dd>{source.advanced}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </section>

        <section className="app-panel activity-panel">
          <PanelHeading eyebrow="Live state" title="Applications & conversations" href="/app/applications" />
          <div className="activity-columns">
            <div>
              <span className="column-label">Applications</span>
              {snapshot.applications.slice(0, 3).map((item) => (
                <article key={item.id}>
                  <span className="activity-icon">A</span>
                  <div><strong>{item.company}</strong><small>{item.role}</small></div>
                  <span className="state-tag">{item.status}</span>
                </article>
              ))}
            </div>
            <div>
              <span className="column-label">Outreach</span>
              {snapshot.outreach.slice(0, 3).map((item) => (
                <article key={item.id}>
                  <span className="activity-icon activity-icon-outreach">↗</span>
                  <div><strong>{item.company}</strong><small>{item.recipient} · {item.channel}</small></div>
                  <span className="state-tag">{item.status}</span>
                </article>
              ))}
            </div>
          </div>
        </section>
      </div> : null}

      {snapshot.dataClass === "fictional-demo" ? (
        <aside className="preview-boundary">
          <strong>Private-by-design preview.</strong>
          <span>
            These organizations and records are fictional. Pair the local companion to use your own
            documents, queues, reports, and existing engine without uploading them to this site.
          </span>
          <a href="/app/onboarding">Set up a real workspace →</a>
        </aside>
      ) : null}
    </>
  );
}

function ExistingEvidencePanel({ evidence }: { evidence: ExistingEngineSnapshot | null }) {
  const run = evidence?.run_snapshot;
  const current = evidence?.current_workspace;
  const queueCounts = Object.entries(run?.queue?.counts ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const outreachCounts = Object.entries(current?.outreach_counts ?? {});
  return (
    <section className="existing-evidence-grid">
      <article className="app-panel evidence-card">
        <header><div><span className="app-kicker">Immutable evidence</span><h3>Exact run snapshot</h3></div><span className={`state-tag state-${run?.status === "completed" ? "complete" : "attention"}`}>{run?.status ?? "unavailable"}</span></header>
        {run?.run_id ? <div className="evidence-run-id"><strong>{run.run_id}</strong><span>{run.started_at} → {run.completed_at}</span></div> : <p className="evidence-empty">{run?.reason ?? "No fully verified terminal run is available."}</p>}
        <div className="evidence-counts">
          {queueCounts.map(([label, value]) => <div key={label}><span>{label.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}
        </div>
        <div className="evidence-sources">
          {(run?.sources ?? []).map((source) => <div key={source.source}><span className={`source-state source-${source.status === "ran" || source.status === "completed" ? "healthy" : source.status === "failed" ? "offline" : "skipped"}`} /><strong>{source.source?.replaceAll("_", " ")}</strong><small>{source.status} · {source.raw_count ?? 0} seen · {source.kept_count ?? 0} kept</small></div>)}
        </div>
      </article>
      <article className="app-panel evidence-card">
        <header><div><span className="app-kicker">Mutable state · separate scope</span><h3>Current workspace snapshot</h3></div><span className={`state-tag state-${current?.status === "available" ? "complete" : "attention"}`}>{current?.status ?? "unavailable"}</span></header>
        {current?.status === "busy" ? <p className="evidence-empty">A production lock is active. Current mutable files were not read; exact run evidence above remains valid.</p> : null}
        <div className="current-queue-card">
          <span>Application queue</span>
          <strong>{current?.application_queue?.priority_item_count ?? "—"}</strong>
          <small>{current?.application_queue?.ready_count ?? 0} ready · {current?.application_queue?.manual_review_count ?? 0} manual review · {current?.application_queue?.source_label_count ?? 0} source labels</small>
        </div>
        <div className="evidence-counts outreach-evidence-counts">
          {outreachCounts.map(([label, value]) => <div key={label}><span>{label.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}
        </div>
        {(current?.reasons ?? []).map((reason) => <p className="evidence-reason" key={reason}>{reason}</p>)}
      </article>
    </section>
  );
}

function PanelHeading({ eyebrow, title, href }: { eyebrow: string; title: string; href: string }) {
  return (
    <header className="panel-heading">
      <div><span>{eyebrow}</span><h3>{title}</h3></div>
      <a href={href} aria-label={`Open ${title}`}>↗</a>
    </header>
  );
}

function QueueView({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="One queue · explicit gates"
        title="Decide once. Route every next action."
        body="Opportunities, company signals, and relationship follow-ups meet here before any consequential action."
      />
      <div className="data-table queue-table">
        <div className="table-head"><span>Priority</span><span>Opportunity</span><span>Lane</span><span>Gate</span><span>Next action</span></div>
        {snapshot.queue.map((item) => (
          <article key={item.id}>
            <span className="score-orb">{item.score.toFixed(1)}</span>
            <div><strong>{item.company}</strong><small>{item.role} · {item.source}</small></div>
            <span>{item.lane}</span>
            <span className={`state-tag state-${item.status.toLowerCase()}`}>{item.status}</span>
            <strong className="table-action">{item.nextAction}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function RunsView({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="Run-scoped evidence"
        title="Every result points back to one run."
        body="Missing, skipped, zero, and failed remain distinct. A newer file never silently repairs an older run."
      />
      <div className="run-timeline">
        {snapshot.runs.map((run) => (
          <article key={run.id}>
            <span className={`run-marker run-${run.status}`} />
            <div className="run-identity"><small>{run.id}</small><strong>{run.startedAt}</strong><span>{run.completedAt ?? "In progress"}</span></div>
            <div className="run-stat"><small>Discovered</small><strong>{run.discovered}</strong></div>
            <div className="run-stat"><small>Queued</small><strong>{run.queued}</strong></div>
            <span className={`state-tag state-${run.status}`}>{run.status}</span>
            {run.reportId ? <a href={`/app/reports#${encodeURIComponent(run.reportId)}`}>Open evidence ↗</a> : <span className="evidence-pending">Evidence pending</span>}
          </article>
        ))}
      </div>
    </section>
  );
}

function ApplicationsView({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="Application lane"
        title="Strategy and materials stay attached to the role."
        body="Preparation is automated; submission authority remains with the person applying."
      />
      <CardRows
        rows={snapshot.applications.map((item) => ({
          id: item.id,
          overline: item.status,
          title: item.company,
          detail: item.role,
          meta: item.updatedAt,
          action: item.status === "Prepared" ? "Human review" : "Local record",
        }))}
      />
      {snapshot.presentation?.applications.truncated ? <TruncationNotice meta={snapshot.presentation.applications} label="applications" /> : null}
    </section>
  );
}

function OutreachView({
  snapshot,
  onApprove,
}: {
  snapshot: DashboardSnapshot;
  onApprove: (item: OutreachItem, reviewedText: string) => Promise<{ approved: boolean; state: OutreachItem["workflowState"] }>;
}) {
  const [editing, setEditing] = useState<OutreachItem | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const reviewField = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) reviewField.current?.focus();
  }, [editing]);

  const openReview = (item: OutreachItem) => {
    setEditing(item);
    setDraft(item.preview);
    setConfirmed(false);
  };

  const approve = async () => {
    if (!editing || !confirmed || !draft.trim()) return;
    setSaving(true);
    const result = await onApprove(editing, draft.trim());
    if (result.approved) {
      setEditing(null);
    } else {
      setEditing((current) => current ? { ...current, workflowState: result.state } : current);
      setConfirmed(false);
    }
    setSaving(false);
  };

  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="Relationship lane"
        title="Context first. Approval before channel action."
        body="The engine can research and draft. You confirm the person, content, and channel before anything moves."
      />
      <div className="outreach-grid">
        {snapshot.outreach.map((item) => (
          <article className="outreach-card" key={item.id}>
            <header><span>{item.channel}</span><span className={`state-tag state-${item.status.toLowerCase()}`}>{item.status}</span></header>
            <h3>{item.company}</h3>
            <small>{item.recipient}</small>
            <blockquote>{item.preview}</blockquote>
            <footer>
              <button type="button" onClick={() => openReview(item)}>{item.status === "Draft" ? "Review draft" : "Open detail"}</button>
              {item.status === "Draft" ? (
                <button className="approve-button" type="button" onClick={() => openReview(item)}>Review & approve</button>
              ) : <button type="button" onClick={() => openReview(item)}>Open thread</button>}
            </footer>
          </article>
        ))}
      </div>
      {snapshot.presentation?.outreach.truncated ? <TruncationNotice meta={snapshot.presentation.outreach} label="outreach records" /> : null}
      {editing ? (
        <section className="review-drawer" aria-label="Outreach review">
          <header><div><span className="app-kicker">Explicit review</span><h3>{editing.company} · {editing.recipient}</h3></div><button type="button" onClick={() => setEditing(null)} aria-label="Close review">×</button></header>
          <label>Final draft<textarea ref={reviewField} value={draft} readOnly={editing.status !== "Draft"} onChange={(event) => setDraft(event.target.value)} /></label>
          {editing.status === "Draft" ? <label className="confirmation-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm this recipient and the complete message above.</span></label> : null}
          <footer><button type="button" onClick={() => setEditing(null)}>{editing.status === "Draft" ? "Cancel" : "Close"}</button>{editing.status === "Draft" ? <button className="approve-button" type="button" onClick={approve} disabled={!confirmed || !draft.trim() || saving}>{saving ? "Approving…" : "Save review & approve"}</button> : null}</footer>
          <small>{editing.status === "Draft" ? "Approval changes local workflow state. It does not send through the channel." : "This is a read-only view of the current local workflow state."}</small>
        </section>
      ) : null}
      <div className="guardrail-note"><strong>No auto-send.</strong><span>Approval changes workflow state; the final channel action remains explicit and bounded.</span></div>
    </section>
  );
}

function TruncationNotice({ meta, label }: { meta: { total: number; returned: number }; label: string }) {
  return <p className="truncation-notice" role="status">Showing the {meta.returned} most recently updated {label} of {meta.total}. The metric above remains the full local total.</p>;
}

function ReportsView({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <section className="app-panel full-panel">
      <PageLead
        eyebrow="Decision briefs"
        title="Reports you can trust enough to act on."
        body="Each brief is derived from exact run pointers and preserves missing evidence instead of backfilling from workspace history."
      />
      <CardRows
        rows={snapshot.reports.map((report) => ({
          id: report.id,
          overline: `${report.status} · ${report.runId}`,
          title: report.title,
          detail: report.summary,
          meta: report.createdAt,
          action: "Run-scoped",
        }))}
      />
    </section>
  );
}

function SettingsView({
  config,
  connection,
  snapshot,
  workspaceMode,
  existingEngine,
  onModeChange,
  onDisconnect,
  onSave,
}: {
  config: CompanionConfig;
  connection: ConnectionState;
  snapshot: DashboardSnapshot;
  workspaceMode: "portable" | "existing";
  existingEngine: ExistingEngineStatus | null;
  onModeChange: (mode: "portable" | "existing") => Promise<void>;
  onDisconnect: () => void;
  onSave: (config: CompanionConfig) => Promise<void>;
}) {
  const [draft, setDraft] = useState<CompanionConfig>({ baseUrl: config.baseUrl, token: "" });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const existingReady = Boolean(existingEngine?.roots_available && existingEngine.production_guard === "configured" && (existingEngine.verified_run_count ?? 0) > 0);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (connection === "connected") {
      setFormError("This tab is already connected. The one-time token has been consumed; disconnect first only if you intend to create a new session.");
      return;
    }
    if (draft.baseUrl.replace(/\/$/, "") !== config.baseUrl.replace(/\/$/, "") && !draft.token) {
      setFormError("A changed companion address requires a new one-time pairing token.");
      return;
    }
    setFormError("");
    setSaving(true);
    await onSave({ baseUrl: draft.baseUrl, token: draft.token || config.token });
    setDraft((current) => ({ ...current, token: "" }));
    setSaving(false);
  };

  return (
    <div className="settings-layout">
      <section className="app-panel settings-card">
        <PageLead eyebrow="Device pairing" title="Connect the private companion." body="The loopback address may persist; the hosted app keeps a 12-hour token only in this tab session so a full nightly run can finish without disconnecting. Your documents and records stay in the local companion." />
        {connection === "connected" ? (
          <div className="pairing-connected-card" role="status">
            <span className="connection-dot dot-connected" />
            <div><strong>Connected in this tab</strong><p>The one-time pairing token was consumed successfully. This tab now uses a short-lived private session, so pasting the same token again will correctly be rejected.</p><small>{config.baseUrl}</small></div>
          </div>
        ) : (
          <form onSubmit={submit}>
            <label>Companion address<input type="url" value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} required /></label>
            <label>One-time pairing token<input type="password" value={draft.token} onChange={(event) => setDraft({ ...draft, token: event.target.value })} placeholder="Paste a fresh re_pair_ token" required /></label>
            <button className="run-button" type="submit" disabled={saving}>{saving ? "Checking…" : "Connect this tab"}</button>
          </form>
        )}
        {formError ? <p className="settings-form-error" role="alert">{formError}</p> : null}
        <p className="settings-status"><span className={`connection-dot dot-${connection}`} />{statusLabel(connection)}</p>
        {connection === "connected" ? <button className="disconnect-button" type="button" onClick={onDisconnect}>Disconnect and pair again</button> : null}
      </section>
      <section className="app-panel settings-card">
        <PageLead eyebrow="Workspace mode" title="Portable or existing engine." body="New users start with a compact profile. Existing operators can bind the same UI to an installed Recruiting Engine checkout." />
        <div className="mode-cards">
          <button className={workspaceMode === "portable" ? "active" : ""} type="button" aria-pressed={workspaceMode === "portable"} onClick={() => onModeChange("portable")}><span>01</span><strong>Portable workspace</strong><p>Curated uploads, preferences, lawful imports, decision queue, reports, and reviewed outreach.</p></button>
          <button className={workspaceMode === "existing" ? "active" : ""} type="button" aria-pressed={workspaceMode === "existing"} onClick={() => onModeChange("existing")}><span>02</span><strong>Private operator cockpit</strong><p>Your installed trackers, queues, stories, communications, and exact reports—plus fixed local actions behind explicit guards.</p></button>
        </div>
        <a className="text-link" href="/app/onboarding">Run onboarding again →</a>
        <div className="binding-status-card">
          <span className={`connection-dot ${existingReady ? "dot-connected" : "dot-preview"}`} />
          <div><strong>Operator binding · {existingReady ? "verified" : "needs attention"}</strong><small>{existingEngine?.verified_run_count ?? 0} exact runs verified · guard {existingEngine?.production_guard ?? "unavailable"} · UI mode {workspaceMode}</small></div>
        </div>
        <small className="settings-data-class">Current data class: {snapshot.dataClass}</small>
      </section>
    </div>
  );
}

function PageLead({ eyebrow, title, body }: { eyebrow: string; title: string; body: string }) {
  return <header className="page-lead"><span>{eyebrow}</span><h2>{title}</h2><p>{body}</p></header>;
}

function CardRows({ rows }: { rows: { id: string; overline: string; title: string; detail: string; meta: string; action: string }[] }) {
  return (
    <div className="card-rows">
      {rows.map((row) => (
        <article key={row.id} id={row.id}>
          <span>{row.overline}</span>
          <div><h3>{row.title}</h3><p>{row.detail}</p></div>
          <small>{row.meta}</small>
          <span className="row-action-label">{row.action}</span>
        </article>
      ))}
    </div>
  );
}

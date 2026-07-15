"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  OperatorActionResult,
  OperatorCommand,
  OperatorOverview,
  OperatorQueueAction,
  OperatorQueueItem,
  OperatorReportDocument,
  OperatorReview,
  OperatorReviewLane,
  OperatorReviewResult,
  OperatorReviewTarget,
  OperatorReviewTargetDetail,
} from "@/lib/operator-contract";

export type OperatorView =
  | "dashboard"
  | "sources"
  | "queue"
  | "runs"
  | "plan"
  | "applications"
  | "outreach"
  | "reports"
  | "accounts"
  | "stories"
  | "operations";

type OperatorWorkspaceProps = {
  view: OperatorView;
  overview: OperatorOverview | null;
  connected: boolean;
  onAction: (commandId: string, confirmation: string, parameters?: Record<string, unknown>) => Promise<OperatorActionResult | null>;
  onLoadReviewTarget: (targetId: string) => Promise<OperatorReviewTargetDetail | null>;
  onLoadReview: (reviewId: string) => Promise<OperatorReviewResult | null>;
  onCreateReview: (commandId: string, targetId: string, reviewedText: string, reviewedSubject: string) => Promise<OperatorReview | null>;
  onUpdateReviewContent: (reviewId: string, reviewedText: string, reviewedSubject: string, confirmation: string) => Promise<OperatorReviewResult | null>;
  onTransitionReview: (reviewId: string, transition: "review" | "approve" | "revoke", confirmation: string) => Promise<OperatorReview | null>;
  onLoadReport: (runId: string) => Promise<OperatorReportDocument | null>;
  autoReviewCommandId?: string;
};

const reviewContentUpdatePhrase = "UPDATE_EXACT_REVIEW_CONTENT";

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
}

function asList(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.filter((item): item is UnknownRecord => Boolean(item && typeof item === "object")) : [];
}

function text(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function number(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

function projectionAvailable(record: UnknownRecord): boolean {
  const status = text(record.status, "").toLowerCase();
  if (["available", "partial", "ready"].includes(status)) return true;
  return false;
}

function riskLabel(command: OperatorCommand): string {
  if (command.risk === "model_cost") return "Paid model action";
  if (command.risk === "local_write") return "Local write";
  if (command.risk === "review_artifact") return "Review artifact";
  if (command.risk === "local_open") return "Local open";
  if (command.risk === "read") return "Read-only check";
  if (command.risk === "external") return "External action";
  return "Guarded local action";
}

function firstList(record: UnknownRecord, keys: string[]): UnknownRecord[] {
  for (const key of keys) {
    const items = asList(record[key]);
    if (items.length) return items;
  }
  return [];
}

function firstRecord(record: UnknownRecord, keys: string[]): UnknownRecord {
  for (const key of keys) {
    const candidate = asRecord(record[key]);
    if (Object.keys(candidate).length) return candidate;
  }
  return {};
}

function commandState(command: OperatorCommand): "available" | "blocked" | "disabled" {
  const state = command.state || command.status;
  if (command.requires_approved_review) return "blocked";
  if (command.available === true || state === "available" || state === "ready" || state === "conditionally_available") return "available";
  if (command.risk === "external" || state === "forbidden") return "disabled";
  return "blocked";
}

function commandReason(command: OperatorCommand): string {
  return command.unavailable_reason || command.reason || (commandState(command) === "available" ? "Ready on this device." : "Unavailable in the current guard state.");
}

const commandCopy: Record<string, [string, string]> = {
  "production.preflight": ["Run production preflight", "Verify release attestation and every production lock without starting the pipeline."],
  "reports.daily.refresh": ["Refresh exact daily report", "Rebuild the daily report only from one verified nightly summary."],
  "reports.sources.refresh": ["Refresh source report", "Rebuild role and source coverage from exact run metrics."],
  "open.account_tracker": ["Open account tracker", "Open the derived account workbook in its local desktop app."],
  "open.current_apply_queue": ["Open apply queue", "Open the current ResumeGenerator queue on this Mac."],
  "open.latest_report": ["Open latest exact report", "Open the newest fully verified run-scoped report."],
  "open.story_workbench": ["Open story workbench", "Open the private story and interview workspace locally."],
  "open.communication_review": ["Open communication review", "Open the current local draft-review artifact."],
  "nightly.run": ["Run production nightly pipeline", "Run the fixed off-cycle production pipeline after exact review, with bounded app-queue and Track 2 LinkedIn delivery enabled."],
  "outreach.send": ["Send outreach", "Disabled in the generic cockpit command surface."],
  "application.assist.fill_to_review": ["Application fill safety gate", "Blocked until the browser runner can technically intercept final Submit; prompt-only stopping is insufficient."],
};

function normalizeCommands(commands: OperatorCommand[]): OperatorCommand[] {
  return commands.map((command) => {
    const id = command.id || command.command_id || "";
    const copy = commandCopy[id];
    return {
      ...command,
      id,
      label: command.label || copy?.[0] || id.replaceAll(".", " "),
      description: command.description || copy?.[1] || command.reason,
      state: command.state || command.status,
      requires_confirmation: command.requires_confirmation ?? command.confirmation_required,
      confirmation_phrase: command.confirmation_phrase || command.confirmation,
      risk: command.risk || (command.kind === "external_delivery" || command.kind === "external_submission" ? "external" : command.kind === "report_refresh" ? "local_write" : "read"),
    };
  }).filter((command) => command.id);
}

function queueItems(queue: UnknownRecord): OperatorQueueItem[] {
  return firstList(queue, ["items", "priority_items", "ready_jobs", "queue"]).map((item) => ({
    id: text(item.id ?? item.job_id, ""),
    job_id: text(item.job_id ?? item.id, ""),
    company: text(item.company, "Unknown company"),
    role: text(item.role ?? item.role_title, "Role not labeled"),
    role_title: text(item.role_title ?? item.role, "Role not labeled"),
    fit_score: number(item.fit_score),
    priority_score: number(item.priority_score),
    priority_rank: number(item.priority_rank),
    status: text(item.status, "review"),
    queue_bucket: text(item.queue_bucket, "current"),
    has_resume: Boolean(item.has_resume),
    has_cover_letter: Boolean(item.has_cover_letter),
    has_job_description: Boolean(item.has_job_description),
    has_strategy: Boolean(item.has_strategy),
    has_intel: Boolean(item.has_intel),
    in_latest_run: Boolean(item.in_latest_run),
    material_state: text(item.material_state, ""),
    actions: asList(item.actions).map((action): OperatorQueueAction => {
      const rawJobId = asRecord(action.parameters).job_id;
      const jobId = Number(rawJobId);
      return {
        command_id: text(action.command_id, ""),
        status: text(action.status, "unavailable"),
        reason: text(action.reason, ""),
        confirmation_phrase: text(action.confirmation_phrase, ""),
        parameters: Number.isInteger(jobId) && jobId > 0 ? { job_id: jobId } : null,
        asynchronous: Boolean(action.asynchronous),
      };
    }).filter((action) => action.command_id),
  }));
}

const viewCopy: Record<OperatorView, { eyebrow: string; title: string; body: string }> = {
  dashboard: {
    eyebrow: "Private operator cockpit",
    title: "Your real recruiting system, in one place.",
    body: "Live local projections from ResumeGenerator and Outreach. The public site remains only the interface; your workbooks, stories, queues, and reports stay on this Mac.",
  },
  sources: {
    eyebrow: "Exact source health",
    title: "Every source, including zero and skipped.",
    body: "Source state follows the exact nightly manifest instead of whichever artifact happened to update most recently.",
  },
  queue: {
    eyebrow: "Current apply queue",
    title: "Decide what deserves work next.",
    body: "The live queue, material readiness, and manual-review lane projected from your installed ResumeGenerator workspace.",
  },
  runs: {
    eyebrow: "Verified nightly evidence",
    title: "Completed runs, bound to exact artifacts.",
    body: "Only nightly runs that pass the summary, manifest, source-metrics, queue, and report evidence chain appear here.",
  },
  plan: {
    eyebrow: "Grounded next-run plan",
    title: "What the next cycle should do—and why.",
    body: "A prioritized plan derived from the latest exact run, source failures, review backlog, and live queue state. Every recommendation retains its evidence binding.",
  },
  applications: {
    eyebrow: "Application history",
    title: "Live tracker state without exposing raw rows.",
    body: "Aggregate status, source, role, score, archive, and review-cache evidence from the installed ResumeGenerator workbook.",
  },
  outreach: {
    eyebrow: "Communication outcomes",
    title: "Delivery evidence and advisory review state.",
    body: "Aggregate sends, accepts, replies, corpus labels, and recommendation decisions—without serving message bodies or recipient details.",
  },
  reports: {
    eyebrow: "Run-scoped reports",
    title: "The real daily brief—not a mutable latest view.",
    body: "Nightly summary, source metrics, action queue, and Outreach report remain bound to one exact run.",
  },
  accounts: {
    eyebrow: "Account tracker",
    title: "Companies, relationships, and today’s next move.",
    body: "The workbook is a derived operating view over organizations, opportunities, contacts, touchpoints, and source provenance.",
  },
  stories: {
    eyebrow: "Story evidence workbench",
    title: "The source banks behind each application.",
    body: "See structured story files and curated source inventories without presenting drafts as canonical or copying private prep into the hosted product.",
  },
  operations: {
    eyebrow: "Guarded local controls",
    title: "Replace memorized commands with explicit actions.",
    body: "Every button maps to a fixed local capability. There is no arbitrary shell, no application submit, and no message send hidden behind a generic run button.",
  },
};

export function OperatorWorkspace({
  view,
  overview,
  connected,
  onAction,
  onLoadReviewTarget,
  onLoadReview,
  onCreateReview,
  onUpdateReviewContent,
  onTransitionReview,
  onLoadReport,
  autoReviewCommandId,
}: OperatorWorkspaceProps) {
  const [selected, setSelected] = useState<OperatorCommand | null>(null);
  const [selectedParameters, setSelectedParameters] = useState<Record<string, unknown>>({});
  const [confirmation, setConfirmation] = useState("");
  const [executing, setExecuting] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [reviewTarget, setReviewTarget] = useState<OperatorReviewTarget | null>(null);
  const [reviewDetail, setReviewDetail] = useState<OperatorReviewTargetDetail | null>(null);
  const [reviewRecord, setReviewRecord] = useState<OperatorReview | null>(null);
  const [reviewedSubject, setReviewedSubject] = useState("");
  const [reviewedText, setReviewedText] = useState("");
  const [reviewConfirmation, setReviewConfirmation] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  const autoReviewAttempted = useRef("");
  const [reportDocument, setReportDocument] = useState<OperatorReportDocument | null>(null);
  const [reportLoadingRunId, setReportLoadingRunId] = useState("");

  const assets = asRecord(overview?.assets);
  const workbooks = asRecord(assets.workbooks);
  const rawQueue = asRecord(assets.current_apply_queue);
  const queue = { ...asRecord(rawQueue.summary), ...rawQueue };
  const storyComms = asRecord(assets.story_comms);
  const reports = asRecord(assets.daily_reports);
  const sources = asRecord(assets.source_metrics);
  const currentRunProgress = asRecord(assets.current_run_progress);
  const nextRunPlan = asRecord(assets.next_run_plan);
  const accountTracker = asRecord(assets.account_tracker);
  const commands = normalizeCommands(overview?.capabilities?.commands ?? []);
  const reviewLanes = useMemo(() => overview?.review_queue?.lanes ?? [], [overview?.review_queue?.lanes]);
  const recentReviews = useMemo(() => overview?.review_queue?.recent_reviews ?? [], [overview?.review_queue?.recent_reviews]);
  const items = queueItems(queue);
  const copy = viewCopy[view];
  const visibleReviewLanes = reviewLanes.filter((lane) => {
    if (view === "operations") return true;
    if (view === "runs") return lane.command_id === "nightly.run";
    if (view === "outreach") return lane.command_id.startsWith("outreach.");
    if (view === "queue" || view === "applications") return lane.command_id.startsWith("application.");
    return false;
  });
  const selectedReviewLane = reviewTarget
    ? reviewLanes.find((lane) => lane.command_id === reviewTarget.command_id)
    : undefined;
  const reviewExecutionAvailable = selectedReviewLane?.execution_state === "available";
  const transitionReviewPhrase = !reviewRecord
    ? ""
    : reviewRecord.state === "pending"
      ? reviewRecord.review_confirmation_phrase || "REVIEW_EXACT_TARGET"
      : reviewRecord.state === "reviewed"
        ? reviewRecord.approval_confirmation_phrase || "APPROVE_EXACT_TARGET"
        : reviewExecutionAvailable
          ? reviewRecord.action_confirmation_phrase || reviewRecord.command_id
          : reviewRecord.revocation_confirmation_phrase || "REVOKE_EXACT_TARGET";
  const reviewChannel = String(reviewDetail?.channel || reviewRecord?.channel || reviewTarget?.channel || "").toLowerCase();
  const reviewTargetType = String(reviewDetail?.target_type || reviewRecord?.target_type || reviewTarget?.target_type || "").toLowerCase();
  const isEmailReview = reviewChannel === "email" || reviewTargetType.includes("email");
  const isLinkedInReview = reviewChannel === "linkedin" || reviewTargetType.includes("linkedin");
  const hasEditableReviewContent = isEmailReview || isLinkedInReview;
  const reviewContentChanged = Boolean(
    reviewRecord
    && hasEditableReviewContent
    && (
      reviewedText !== (reviewDetail?.draft_text || "")
      || reviewedSubject !== (reviewDetail?.subject || "")
    )
  );
  const reviewPhrase = reviewContentChanged ? reviewContentUpdatePhrase : transitionReviewPhrase;
  const reviewContentValid = !hasEditableReviewContent
    || (Boolean(reviewedText.trim()) && (!isEmailReview || Boolean(reviewedSubject.trim())));

  const selectCommand = (command: OperatorCommand, parameters: Record<string, unknown> = {}) => {
    setSelectedParameters(parameters);
    setSelected(command);
  };

  const selectReviewTarget = useCallback(async (target: OperatorReviewTarget) => {
    setReviewBusy(true);
    setReviewConfirmation("");
    const existing = recentReviews.find((review) =>
      review.command_id === target.command_id
      && review.target_id === target.target_id
      && ["pending", "reviewed", "approved"].includes(review.state));
    const storedReview = existing ? await onLoadReview(existing.id) : null;
    const loadedDetail = existing ? storedReview?.review_target ?? null : await onLoadReviewTarget(target.target_id);
    if (loadedDetail) {
      const storesOutgoingContent = target.command_id === "outreach.linkedin.send" || target.command_id === "outreach.email.send";
      const detail = storesOutgoingContent && storedReview?.operator_review
        ? {
            ...loadedDetail,
            subject: storedReview.operator_review.reviewed_subject ?? loadedDetail.subject,
            draft_text: storedReview.operator_review.reviewed_text ?? loadedDetail.draft_text,
          }
        : loadedDetail;
      setReviewTarget(target);
      setReviewDetail(detail);
      setReviewRecord(storedReview?.operator_review ?? existing ?? null);
      setReviewedSubject(detail.subject || "");
      setReviewedText(detail.draft_text || "");
    }
    setReviewBusy(false);
  }, [onLoadReview, onLoadReviewTarget, recentReviews]);

  useEffect(() => {
    if (!autoReviewCommandId || reviewTarget || reviewBusy) return;
    const lane = reviewLanes.find((candidate) => candidate.command_id === autoReviewCommandId);
    const target = lane?.targets?.[0];
    const attemptKey = `${overview?.generated_at || "loading"}:${autoReviewCommandId}:${target?.target_id || lane?.state || "missing"}`;
    if (autoReviewAttempted.current === attemptKey) return;
    autoReviewAttempted.current = attemptKey;
    if (!target) {
      if (overview?.generated_at) {
        const message = lane?.reason || "No exact reviewed nightly target is available right now.";
        window.setTimeout(() => setActionMessage(message), 0);
      }
      return;
    }
    const timer = window.setTimeout(() => { void selectReviewTarget(target); }, 0);
    return () => window.clearTimeout(timer);
  }, [autoReviewCommandId, overview?.generated_at, reviewBusy, reviewLanes, reviewTarget, selectReviewTarget]);

  const advanceReview = async () => {
    if (!reviewTarget) return;
    setReviewBusy(true);
    let next = reviewRecord;
    if (!next) {
      next = await onCreateReview(
        reviewTarget.command_id,
        reviewTarget.target_id,
        hasEditableReviewContent ? reviewedText : "",
        isEmailReview ? reviewedSubject : "",
      );
      if (next && reviewDetail) {
        setReviewDetail({ ...reviewDetail, draft_text: reviewedText, subject: reviewedSubject });
      }
    } else if (reviewContentChanged) {
      const updated = await onUpdateReviewContent(
        next.id,
        reviewedText,
        reviewedSubject,
        reviewConfirmation,
      );
      if (updated?.operator_review) {
        const detailBase = updated.review_target || reviewDetail;
        const nextDetail = detailBase
          ? {
              ...detailBase,
              draft_text: updated.operator_review.reviewed_text ?? reviewedText,
              subject: updated.operator_review.reviewed_subject ?? reviewedSubject,
            }
          : null;
        setReviewRecord(updated.operator_review);
        setReviewDetail(nextDetail);
        setReviewedSubject(nextDetail?.subject || "");
        setReviewedText(nextDetail?.draft_text || "");
        setReviewConfirmation("");
        setActionMessage("Exact outgoing content updated. The review is pending again and must be reviewed and approved before execution.");
      }
      setReviewBusy(false);
      return;
    } else if (next.state === "pending") {
      next = await onTransitionReview(next.id, "review", reviewConfirmation);
    } else if (next.state === "reviewed") {
      next = await onTransitionReview(next.id, "approve", reviewConfirmation);
    } else if (next.state === "approved" && reviewExecutionAvailable) {
      const result = await onAction(
        next.command_id,
        reviewConfirmation,
        { review_id: next.id, target_id: next.target_id },
      );
      const job = result?.job || result?.operator_job;
      if (job) {
        setActionMessage(job.summary || `Reviewed action ${job.status || "accepted"}.`);
        setReviewTarget(null);
        setReviewDetail(null);
        setReviewRecord(null);
        setReviewedSubject("");
        setReviewedText("");
        setReviewConfirmation("");
      }
      setReviewBusy(false);
      return;
    } else if (next.state === "approved") {
      next = await onTransitionReview(next.id, "revoke", reviewConfirmation);
    }
    if (next) {
      setReviewRecord(next);
      setReviewConfirmation("");
      setActionMessage(
        next.state === "approved"
          ? reviewLanes.find((lane) => lane.command_id === next?.command_id)?.execution_state === "available"
            ? "Exact target approved and hash-bound. Its fixed guarded action is ready for a separate typed confirmation."
            : "Exact target approved and hash-bound. The execution gate remains closed until its installed contract is proven."
          : `Review is now ${next.state}.`,
      );
    }
    setReviewBusy(false);
  };

  const openExactReport = async (runId: string) => {
    setReportLoadingRunId(runId);
    const report = await onLoadReport(runId);
    setReportLoadingRunId("");
    if (report) setReportDocument(report);
  };

  if (!connected || !overview) {
    return (
      <section className="operator-empty app-panel">
        <span className="operator-kicker">Private operator mode</span>
        <h2>Pair the local companion to unlock your installed engines.</h2>
        <p>The public preview cannot read account trackers, application materials, stories, messages, or reports. Pair this tab, then choose Existing engine in Settings.</p>
        <div><a href="/app/settings">Connect this Mac →</a><a href="/install">Operator setup guide ↗</a></div>
      </section>
    );
  }

  const runSelected = async () => {
    if (!selected) return;
    setExecuting(true);
    setActionMessage("");
    try {
      const result = await onAction(selected.id, confirmation, selectedParameters);
      const job = result?.job || result?.operator_job;
      if (!result || !job) {
        setActionMessage("Action did not start. Review the connection notice and capability guard, then retry.");
      } else {
        setActionMessage(result.message || job.summary || job.result_code || `Job ${job.status ?? "accepted"}.`);
      }
      setSelected(null);
      setSelectedParameters({});
      setConfirmation("");
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="operator-workspace">
      <header className="operator-lead">
        <div><span>{copy.eyebrow}</span><h2>{copy.title}</h2><p>{copy.body}</p></div>
        <OperatorGuard overview={overview} />
      </header>

      {actionMessage ? <div className="operator-action-message" role="status">{actionMessage}</div> : null}

      {view === "dashboard" ? <OperatorDashboard overview={overview} workbooks={workbooks} queue={queue} storyComms={storyComms} reports={reports} progress={currentRunProgress} commands={commands} onSelect={selectCommand} /> : null}
      {view === "accounts" ? <AccountsSurface workbooks={workbooks} accountTracker={accountTracker} commands={commands} onSelect={selectCommand} /> : null}
      {view === "queue" ? <ApplyQueueSurface queue={queue} items={items} commands={commands} onSelect={selectCommand} /> : null}
      {view === "applications" ? <ApplicationHistorySurface workbooks={workbooks} commands={commands} onSelect={selectCommand} /> : null}
      {view === "stories" ? <StorySurface storyComms={storyComms} commands={commands} onSelect={selectCommand} /> : null}
      {view === "outreach" ? <CommunicationSurface storyComms={storyComms} commands={commands} onSelect={selectCommand} /> : null}
      {view === "reports" || view === "sources" ? <ReportSurface reports={reports} sources={sources} commands={commands} onSelect={selectCommand} onOpenReport={openExactReport} reportLoadingRunId={reportLoadingRunId} showSources={view === "sources"} /> : null}
      {view === "runs" ? <VerifiedRunsSurface reports={reports} sources={sources} progress={currentRunProgress} commands={commands} onSelect={selectCommand} onOpenReport={openExactReport} reportLoadingRunId={reportLoadingRunId} /> : null}
      {view === "plan" ? <NextRunPlanSurface plan={nextRunPlan} progress={currentRunProgress} commands={commands} onSelect={selectCommand} /> : null}
      {view === "operations" ? <OperationsSurface overview={overview} commands={commands} onSelect={selectCommand} /> : null}

      {(view === "reports" || view === "runs") && reportDocument ? <ExactReportViewer report={reportDocument} onClose={() => setReportDocument(null)} /> : null}

      {visibleReviewLanes.length ? (
        <ReviewQueue
          lanes={visibleReviewLanes}
          reviews={recentReviews}
          onSelect={selectReviewTarget}
          loading={reviewBusy}
        />
      ) : null}

      {selected ? (
        <div className="operator-dialog-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.currentTarget === event.target && !executing) {
            setSelected(null);
            setSelectedParameters({});
            setConfirmation("");
          }
        }}>
          <section className="operator-dialog" role="dialog" aria-modal="true" aria-labelledby="operator-dialog-title">
            <button type="button" onClick={() => { setSelected(null); setSelectedParameters({}); }} disabled={executing} aria-label="Close action confirmation">×</button>
            <span className={`operator-command-state state-${commandState(selected)}`}>{riskLabel(selected)}</span>
            <h3 id="operator-dialog-title">{selected.label || selected.id}</h3>
            <p>{selected.description || commandReason(selected)}</p>
            <div className="operator-command-preview"><small>Fixed capability</small><code>{selected.id}</code></div>
            {typeof selectedParameters.job_id === "number" ? <div className="operator-target-preview"><span>Selected queue job</span><strong>#{selectedParameters.job_id}</strong></div> : null}
            {selected.requires_confirmation !== false ? (
              <label>Type <strong>{selected.confirmation_phrase || selected.confirmation || selected.id}</strong> to confirm<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoFocus /></label>
            ) : null}
            <button className="operator-confirm-button" type="button" onClick={runSelected} disabled={executing || commandState(selected) !== "available" || (selected.requires_confirmation !== false && confirmation !== (selected.confirmation_phrase || selected.confirmation || selected.id))}>{executing ? "Running…" : "Run local action"}</button>
            <small>Only this named capability can run. The UI cannot inject a path, flag, or shell command.</small>
          </section>
        </div>
      ) : null}

      {reviewTarget && reviewDetail ? (
        <div className="operator-dialog-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.currentTarget === event.target && !reviewBusy) {
            setReviewTarget(null);
            setReviewDetail(null);
            setReviewRecord(null);
            setReviewedSubject("");
            setReviewedText("");
            setReviewConfirmation("");
          }
        }}>
          <section className="operator-dialog operator-review-dialog" role="dialog" aria-modal="true" aria-labelledby="operator-review-title">
            <button type="button" onClick={() => { setReviewTarget(null); setReviewDetail(null); setReviewRecord(null); setReviewedSubject(""); setReviewedText(""); setReviewConfirmation(""); }} disabled={reviewBusy} aria-label="Close exact target review">×</button>
            <span className={`operator-command-state review-state-${reviewRecord?.state || "unstaged"}`}>{reviewRecord?.state || "Unstaged exact target"}</span>
            <h3 id="operator-review-title">{reviewDetail.label || reviewTarget.label}</h3>
            <p>{reviewDetail.detail || reviewTarget.detail}</p>
            <div className="operator-review-binding">
              <div><span>Capability</span><strong>{reviewTarget.command_id}</strong></div>
              {reviewDetail.job_id ? <div><span>Exact job</span><strong>#{reviewDetail.job_id}</strong></div> : null}
              <div><span>Artifact fingerprint</span><code>{(reviewRecord?.artifact_sha256 || reviewDetail.artifact_sha256)?.slice(0, 20)}…</code></div>
              <div><span>Bounded execution</span><strong>One target maximum</strong></div>
            </div>
            {reviewDetail.recipient ? <section className="operator-sensitive-review"><span>Exact recipient · immutable</span><strong>{reviewDetail.recipient}</strong></section> : null}
            {isEmailReview ? (
              <label className="operator-sensitive-review operator-editable-review"><span>Exact email subject · editable</span><input type="text" value={reviewedSubject} onChange={(event) => { setReviewedSubject(event.target.value); setReviewConfirmation(""); }} disabled={reviewBusy} autoComplete="off" /></label>
            ) : reviewDetail.subject ? <section className="operator-sensitive-review"><span>Exact subject</span><strong>{reviewDetail.subject}</strong></section> : null}
            {hasEditableReviewContent ? (
              <label className="operator-sensitive-review operator-editable-review"><span>{isEmailReview ? "Exact email body · editable" : "Exact LinkedIn body · editable"}</span><textarea value={reviewedText} onChange={(event) => { setReviewedText(event.target.value); setReviewConfirmation(""); }} disabled={reviewBusy} /></label>
            ) : reviewDetail.draft_text ? <section className="operator-sensitive-review"><span>Exact draft text</span><pre>{reviewDetail.draft_text}</pre></section> : null}
            {reviewDetail.context ? <section className="operator-sensitive-review"><span>Bound context · immutable</span><pre>{reviewDetail.context}</pre></section> : null}
            {reviewRecord ? (
              <label>Type <strong>{reviewPhrase}</strong> to {reviewContentChanged ? "replace the stored exact content and reset approval" : reviewRecord.state === "pending" ? "record review" : reviewRecord.state === "reviewed" ? "approve" : reviewExecutionAvailable ? "run this separately confirmed action" : "revoke approval"}<input value={reviewConfirmation} onChange={(event) => setReviewConfirmation(event.target.value)} autoFocus /></label>
            ) : null}
            <button className="operator-confirm-button" type="button" onClick={advanceReview} disabled={reviewBusy || !reviewContentValid || Boolean(reviewRecord && reviewConfirmation !== reviewPhrase)}>{reviewBusy ? "Recording…" : !reviewRecord ? "Stage exact content" : reviewContentChanged ? "Update exact review content" : reviewRecord.state === "pending" ? "Record reviewed" : reviewRecord.state === "reviewed" ? "Approve exact target" : reviewExecutionAvailable ? "Run reviewed action" : "Revoke approval"}</button>
            <small>{reviewDetail.content_binding} Recipient and context remain immutable. Exact content is fetched only for this authenticated dialog and is never included in the overview or browser session storage.</small>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function OperatorGuard({ overview }: { overview: OperatorOverview }) {
  const locks = overview.guard?.locks ?? {};
  const busy = overview.guard?.busy ?? Object.values(locks).some((value) => value === "busy");
  const allFree = Object.keys(locks).length > 0 && Object.values(locks).every((value) => value === "free");
  const guardState = busy ? "busy" : allFree ? "ready" : "attention";
  return (
    <aside className={`operator-guard ${guardState === "ready" ? "guard-ready" : "guard-busy"}`}>
      <span><i />{busy ? "Production work in progress" : allFree ? "Local systems available" : "Local guards need attention"}</span>
      <strong>{overview.guard?.production_guard === "configured" ? "Release attestation configured" : "Release attestation unavailable"}</strong>
      <small>{busy ? "Immutable run evidence remains readable; mutable queue and workbook projections pause." : `${Object.keys(locks).length} lock boundaries reported · ${overview.guard?.external_actions === "reviewed-single-target-only" ? "reviewed one-target actions only" : "external actions disabled"}`}</small>
    </aside>
  );
}

function OperatorDashboard({ overview, workbooks, queue, storyComms, reports, progress, commands, onSelect }: { overview: OperatorOverview; workbooks: UnknownRecord; queue: UnknownRecord; storyComms: UnknownRecord; reports: UnknownRecord; progress: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const accountTracker = firstRecord(workbooks, ["account_tracker", "accounts", "outreach"]);
  const storyInventory = firstRecord(storyComms, ["stories", "inventories", "story_inventory"]);
  const communication = firstRecord(storyComms, ["communications", "comms", "outcomes", "outcome_totals"]);
  const reportItems = firstList(reports, ["items", "reports", "run_reports"]);
  const storyFileCount = Object.values(storyInventory).reduce<number>((total, value) => total + number(asRecord(value).file_count), 0);
  const queueReturned = number(queue.items_returned, firstList(queue, ["items", "priority_items"]).length);
  const queueTotal = number(queue.items_total, queueReturned);
  const metrics = [
    ["Accounts", number(accountTracker.total ?? accountTracker.account_count ?? accountTracker.rows), "Derived tracker"],
    ["Apply queue", number(queue.ready_count ?? queue.item_count ?? queueReturned), `${number(queue.manual_review_count)} review · ${queueReturned}/${queueTotal} shown`],
    ["Story sources", number(storyInventory.total ?? storyInventory.count ?? storyInventory.file_count, storyFileCount), "Private evidence"],
    ["Reports", number(reports.total ?? reports.count ?? reportItems.length), "Exact run scope"],
  ];
  const systemCards = [
    ["Account tracker", accountTracker, workbooks.status === "available", "Organizations, opportunities, contacts, touchpoints, and campaign actions."],
    ["ResumeGenerator", queue, projectionAvailable(queue), "Current apply queue and generated-material readiness."],
    ["Story workbench", storyInventory, projectionAvailable(storyInventory), "Structured story files, source banks, and protected prep inventory."],
    ["Communication outcomes", communication, projectionAvailable(communication), "Delivery totals, corpus state, and recommendation decisions."],
  ] as const;
  return (
    <>
      <CurrentRunProgressCard progress={progress} compact />
      <section className="operator-metrics" aria-label="Operator workspace metrics">{metrics.map(([label, value, detail]) => <article key={label}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>)}</section>
      <section className="operator-grid operator-system-grid">
        {systemCards.map(([label, data, available, detail]) => <article key={label}><span className={available ? "system-online" : "system-offline"}>{available ? "Available" : text(data.status, "Unavailable")}</span><h3>{label}</h3><p>{detail}</p><small>{available ? "Minimized local projection" : text(data.reason, "Capability reason available in Operations")}</small></article>)}
      </section>
      <section className="operator-panel operator-command-strip"><div><span>Common moves</span><h3>Open the real workspace or refresh a safe view.</h3><p>Production writes remain disabled while upstream locks are occupied.</p></div><CommandButtons commands={commands.filter((command) => commandState(command) === "available").slice(0, 4)} onSelect={onSelect} /></section>
      <section className="operator-fineprint">Generated {text(overview.generated_at, "by the local companion")} · data class {text(overview.data_class, "local-private")} · arbitrary commands unavailable</section>
    </>
  );
}

function AccountsSurface({ workbooks, accountTracker, commands, onSelect }: { workbooks: UnknownRecord; accountTracker: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const legacyTracker = firstRecord(workbooks, ["account_tracker", "accounts", "outreach"]);
  const trackerSummary = asRecord(accountTracker.summary);
  const tracker = Object.keys(trackerSummary).length ? { ...legacyTracker, ...trackerSummary } : legacyTracker;
  const actions = firstList(legacyTracker, ["action_queue", "action_items", "top_actions", "items"]);
  const actionTotal = number(tracker.action_items_total ?? tracker.action_count, actions.length);
  const actionReturned = number(tracker.action_items_returned, actions.length);
  const actionsTruncated = Boolean(tracker.action_items_truncated);
  const stages = asRecord(tracker.stage_counts ?? tracker.by_stage);
  const tiers = asRecord(tracker.tier_counts ?? tracker.by_tier);
  const activity = asRecord(tracker.activity_totals);
  const sheetRows = asRecord(legacyTracker.sheet_row_counts);
  const openAction = asRecord(accountTracker.open_action);
  const openCommandId = text(openAction.command_id, "open.account_tracker");
  const openCommand = commands.find((command) => command.id === openCommandId);
  const surfaceOpenAvailable = text(openAction.status, "unavailable") === "available";
  const guardedOpenCommand = openCommand ? {
    ...openCommand,
    ...(surfaceOpenAvailable ? {} : {
      available: false,
      state: "unavailable",
      status: "unavailable",
      reason: text(openAction.reason, "The account tracker projection is not safe to open right now."),
      unavailable_reason: text(openAction.reason, "The account tracker projection is not safe to open right now."),
    }),
    confirmation_phrase: text(openAction.confirmation_phrase, openCommand.confirmation_phrase || "OPEN_ACCOUNT_TRACKER"),
  } : null;
  return (
    <>
      <section className="operator-metrics"><article><span>Companies</span><strong>{number(tracker.total ?? tracker.account_count ?? tracker.rows)}</strong><small>Tracker universe</small></article><article><span>Action queue</span><strong>{number(tracker.action_count ?? sheetRows["Action Queue"] ?? actions.length)}</strong><small>{number(tracker.actions_due_now)} due now</small></article><article><span>People mapped</span><strong>{number(tracker.people_mapped ?? tracker.contacts ?? activity["People Mapped"])}</strong><small>Relationship surface</small></article><article><span>Workbook sheets</span><strong>{number(legacyTracker.sheet_count ?? legacyTracker.sheets ?? Object.keys(sheetRows).length)}</strong><small>Derived views</small></article></section>
      {guardedOpenCommand ? <section className="operator-panel operator-account-open"><div><span>Primary account surface</span><h3>Open the full live tracker in Excel.</h3><p>The cockpit keeps a safe aggregate and today’s bounded action queue; Excel remains the complete editable account system.</p></div><CommandButtons commands={[guardedOpenCommand]} onSelect={onSelect} /></section> : null}
      <section className="operator-split">
        <div className="operator-panel"><div className="operator-panel-head"><div><span>Today’s queue</span><h3>Account moves worth attention</h3></div><small>{actionReturned} of {actionTotal}{actionsTruncated ? " · bounded view" : ""}</small></div><OperatorRows items={actions} empty="No account actions were projected." /></div>
        <aside className="operator-panel operator-breakdown"><span>Portfolio state</span><h3>Stage and tier mix</h3><Breakdown title="Stages" data={stages} /><Breakdown title="Tiers" data={tiers} /></aside>
      </section>
      <CommandSection title="Account controls" commands={commands.filter((command) => (command.id.includes("account") || command.id.includes("campaign")) && command.id !== openCommandId)} onSelect={onSelect} />
      {accountTracker.status && accountTracker.status !== "available" ? <p className="operator-boundary-note">Account tracker projection: {text(accountTracker.status)}. {text(accountTracker.reason, "The next refresh will recover when its local lock is free.")}</p> : null}
    </>
  );
}

function ApplyQueueSurface({ queue, items, commands, onSelect }: { queue: UnknownRecord; items: OperatorQueueItem[]; commands: OperatorCommand[]; onSelect: (command: OperatorCommand, parameters?: Record<string, unknown>) => void }) {
  const itemCommandIds = ["open.application_folder", "application.resume.generate", "application.apply_packet.build"];
  const itemCommands = itemCommandIds.map((id) => commands.find((command) => command.id === id)).filter((command): command is OperatorCommand => Boolean(command));
  const generalCommands = commands.filter((command) => (command.id.includes("apply") || command.id.includes("resume") || command.id.includes("queue")) && !itemCommandIds.includes(command.id));
  const itemsTotal = number(queue.items_total, items.length);
  const itemsReturned = number(queue.items_returned, items.length);
  const truncated = Boolean(queue.truncated);
  return (
    <>
      <section className="operator-metrics"><article><span>Ready</span><strong>{number(queue.ready_count ?? items.length)}</strong><small>Current queue</small></article><article><span>Manual review</span><strong>{number(queue.manual_review_count)}</strong><small>Human decision</small></article><article><span>Generated</span><strong>{items.filter((item) => item.has_resume || item.status === "generated").length}</strong><small>Resume present</small></article><article><span>Latest run</span><strong className="metric-compact">{text(queue.latest_discovery_run ?? queue.run_id, "Unavailable")}</strong><small>Derived state</small></article></section>
      <section className="operator-panel">
        <div className="operator-panel-head"><div><span>Priority order</span><h3>Live application queue</h3></div><small>{itemsReturned} of {itemsTotal}{truncated ? " · bounded view" : ""}</small></div>
        <div className="operator-queue-table">
          {items.length ? items.map((item, index) => (
            <article key={item.id || `${item.company}-${index}`}>
              <span className="operator-rank">{item.priority_rank || index + 1}</span>
              <div><strong>{item.company}</strong><p>{item.role_title || item.role}</p><small>{item.queue_bucket} · {item.status}</small></div>
              <div className="operator-materials"><span className={item.has_strategy ? "ready" : "missing"}>Strategy</span><span className={item.has_resume || item.status === "generated" ? "ready" : "missing"}>Resume</span><span className={item.has_cover_letter ? "ready" : "optional"}>CL</span></div>
              <div className="operator-item-actions">
                {itemCommands.map((command) => {
                  const rowAction = item.actions?.find((action) => action.command_id === command.id);
                  const guardedCommand: OperatorCommand = {
                    ...command,
                    state: rowAction?.status || "unavailable",
                    status: rowAction?.status || "unavailable",
                    reason: rowAction?.reason || "Per-item guard state is unavailable.",
                    confirmation_phrase: rowAction?.confirmation_phrase || command.confirmation_phrase,
                  };
                  const jobId = rowAction?.parameters?.job_id;
                  const available = Number.isInteger(jobId) && Number(jobId) > 0 && commandState(guardedCommand) === "available";
                  const label = command.id === "open.application_folder" ? "Open" : command.id === "application.resume.generate" ? "Generate" : "Apply packet";
                  return <button key={command.id} type="button" disabled={!available} title={available ? command.description : commandReason(guardedCommand)} onClick={() => available && onSelect(guardedCommand, { job_id: jobId })}>{label}</button>;
                })}
              </div>
              <strong className="operator-score">{typeof item.fit_score === "number" && item.fit_score > 0 ? item.fit_score.toFixed(1) : "—"}</strong>
            </article>
          )) : <p className="operator-empty-row">No current queue is available.</p>}
        </div>
      </section>
      {truncated ? <p className="operator-boundary-note">This is a bounded projection showing {itemsReturned} of {itemsTotal} queue rows. Open the local queue for the complete ordering.</p> : null}
      <CommandSection title="Application controls" commands={generalCommands} onSelect={onSelect} />
      <p className="operator-boundary-note">Applied and closed transitions use an exact-target, archive-first review flow that preserves generated artifacts before the current queue changes. Final application Submit remains human-owned.</p>
    </>
  );
}

function ApplicationHistorySurface({ workbooks, commands, onSelect }: { workbooks: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const resumeWorkbook = firstRecord(workbooks, ["resume_workbook"]);
  const jobs = asRecord(resumeWorkbook.jobs);
  const archive = asRecord(resumeWorkbook.archive);
  const reviewCache = asRecord(resumeWorkbook.review_cache);
  const fit = asRecord(jobs.fit_score);
  const currentRows = number(jobs.row_count);
  const archivedRows = number(archive.row_count);
  const reviewRows = number(reviewCache.row_count);
  const workbookAvailable = workbooks.status === "available" && Object.keys(resumeWorkbook).length > 0;
  return (
    <>
      <section className="operator-metrics"><article><span>Current jobs</span><strong>{currentRows}</strong><small>Live tracker rows</small></article><article><span>Archived</span><strong>{archivedRows}</strong><small>Historical rows</small></article><article><span>Review cache</span><strong>{reviewRows}</strong><small>Recorded decisions</small></article><article><span>Average fit</span><strong>{fit.average === null || fit.average === undefined ? "—" : number(fit.average).toFixed(1)}</strong><small>{number(fit.count)} scored jobs</small></article></section>
      <section className="operator-split">
        <div className="operator-panel"><div className="operator-panel-head"><div><span>Current workbook</span><h3>Status, source, and role mix</h3></div><small>{workbookAvailable ? "Aggregate projection available" : text(workbooks.reason, "Workbook unavailable")}</small></div><Breakdown title="Current status" data={asRecord(jobs.status_counts)} /><Breakdown title="Current sources" data={asRecord(jobs.source_counts)} /><Breakdown title="Current role types" data={asRecord(jobs.role_type_counts)} /></div>
        <aside className="operator-panel operator-breakdown"><span>History and review</span><h3>Archive and decision evidence</h3><Breakdown title="Archive status" data={asRecord(archive.status_counts)} /><Breakdown title="Review decisions" data={asRecord(reviewCache.decision_counts)} /><Breakdown title="Review categories" data={asRecord(reviewCache.category_counts)} /></aside>
      </section>
      <CommandSection title="Application history controls" commands={commands.filter((command) => command.id === "open.current_apply_queue" || command.id.includes("workbook"))} onSelect={onSelect} />
      <p className="operator-boundary-note">This surface exposes workbook aggregates only. Company, role, description, and application-answer rows remain in the local workbook and current queue.</p>
    </>
  );
}

function StorySurface({ storyComms, commands, onSelect }: { storyComms: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const stories = firstRecord(storyComms, ["stories", "inventories", "story_inventory"]);
  const inventory = firstList(stories, ["inventory"]);
  const items = firstList(stories, ["items"]);
  const returned = number(stories.items_returned, items.length);
  const total = number(stories.items_total, returned);
  const truncated = Boolean(stories.truncated);
  return (
    <>
      <section className="operator-metrics"><article><span>Story files</span><strong>{number(stories.file_count)}</strong><small>Inventory total</small></article><article><span>Story candidates</span><strong>{number(stories.canonical_count)}</strong><small>Filename-classified, not canonical</small></article><article><span>Source groups</span><strong>{inventory.length}</strong><small>Curated inventories</small></article><article><span>Private prep</span><strong>{text(stories.private_status, "Protected")}</strong><small>Never projected raw</small></article></section>
      <section className="operator-split"><div className="operator-panel"><div className="operator-panel-head"><div><span>Curated filenames</span><h3>Reusable story and source candidates</h3></div><small>{returned} of {total}{truncated ? " · bounded view" : ""}</small></div><OperatorRows items={items} empty="No curated story filenames are available." /></div><aside className="operator-panel"><span className="operator-kicker">Source inventory</span><h3>What the workbench contains</h3><OperatorRows items={inventory} empty="Story source inventory is not available." /></aside></section>
      <CommandSection title="Story controls" commands={commands.filter((command) => command.id.includes("story"))} onSelect={onSelect} />
      <p className="operator-boundary-note">The filename-classified count can include drafts; it is not a canonical-story guarantee or a role-framing model. Raw interview prep and full answer-bank payloads remain private. {truncated ? `Open the local workbench to inspect all ${total} candidates.` : ""}</p>
    </>
  );
}

function CommunicationSurface({ storyComms, commands, onSelect }: { storyComms: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const comms = firstRecord(storyComms, ["communications", "comms", "outcomes"]);
  const totals = Object.keys(asRecord(comms.totals)).length ? asRecord(comms.totals) : asRecord(storyComms.outcome_totals);
  const review = Object.keys(asRecord(comms.review)).length ? asRecord(comms.review) : asRecord(storyComms.recommendation_review);
  const decisionCounts = asRecord(comms.review_decision_counts);
  const corpusCounts: UnknownRecord = { gold: totals.gold, silver: totals.silver, negative: totals.negative };
  const aggregateRows: UnknownRecord[] = [
    { label: "Recommendations found", status: "aggregate", summary: "Advisory suggestions in the current outcome-learning artifact.", count: number(comms.recommendation_count) },
    { label: "Recommendations reviewed", status: "aggregate", summary: "Decisions recorded in the latest recommendation-review artifact.", count: number(comms.review_decision_count) },
    { label: "Automatic prompt changes", status: review.automatic_prompt_changes_applied ? "applied" : "not applied", summary: "The review artifact explicitly records whether automation changed prompts.", count: review.automatic_prompt_changes_applied ? 1 : 0 },
    { label: "Policy changes", status: review.policy_changes_applied ? "applied" : "not applied", summary: "The review artifact explicitly records whether policy changes were applied.", count: review.policy_changes_applied ? 1 : 0 },
  ];
  return (
    <>
      <section className="operator-metrics"><article><span>Sends recorded</span><strong>{number(totals.sends)}</strong><small>Delivery evidence</small></article><article><span>Accepts</span><strong>{number(totals.accepts)}</strong><small>{text(totals.accept_rate, "—")} rate</small></article><article><span>Replies</span><strong>{number(totals.replies)}</strong><small>{text(totals.reply_rate, "—")} rate</small></article><article><span>Pending review</span><strong>{number(comms.pending_review_count)}</strong><small>Recommendation gap</small></article></section>
      <section className="operator-split"><div className="operator-panel"><div className="operator-panel-head"><div><span>Recommendation state</span><h3>Outcome-learning aggregates</h3></div><small>No drafts or message bodies</small></div><OperatorRows items={aggregateRows} empty="No communication aggregates are available." /></div><aside className="operator-panel operator-breakdown"><span>Evidence mix</span><h3>Corpus and review decisions</h3><Breakdown title="Corpus labels" data={corpusCounts} /><Breakdown title="Review decisions" data={decisionCounts} /></aside></section>
      <CommandSection title="Communication controls" commands={commands.filter((command) => command.id.includes("communication") || command.id.includes("comms") || command.id.includes("outreach"))} onSelect={onSelect} />
      <p className="operator-boundary-note">The aggregate projection is not a draft queue. Exact-run recipient and message detail appears only after selecting one authenticated review target below; generic or unreviewed sends remain impossible.</p>
    </>
  );
}

function ReportSurface({ reports, sources, commands, onSelect, onOpenReport, reportLoadingRunId, showSources }: { reports: UnknownRecord; sources: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void; onOpenReport: (runId: string) => Promise<void>; reportLoadingRunId: string; showSources: boolean }) {
  const reportItems = firstList(reports, ["items", "reports", "run_reports"]);
  const latestSources = asRecord(sources.latest);
  const sourceItems = firstList(sources, ["items", "sources", "source_breakdown"]).length ? firstList(sources, ["items", "sources", "source_breakdown"]) : firstList(latestSources, ["sources", "source_breakdown"]);
  const returned = number(reports.items_returned, reportItems.length);
  const total = number(reports.total ?? reports.count, reportItems.length);
  const truncated = Boolean(reports.truncated);
  return (
    <>
      <section className="operator-metrics"><article><span>Verified reports</span><strong>{number(reports.total ?? reports.count ?? reportItems.length)}</strong><small>Exact run scope</small></article><article><span>Latest run</span><strong className="metric-compact">{text(reports.latest_run_id ?? sources.run_id ?? latestSources.run_id, "Unavailable")}</strong><small>Immutable pointer</small></article><article><span>Sources</span><strong>{number(sources.total ?? sourceItems.length)}</strong><small>Manifest source families</small></article><article><span>Failures</span><strong>{number(reports.failure_count ?? sources.failure_count)}</strong><small>Never hidden</small></article></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>{showSources ? "Source breakdown" : "Run-scoped briefs"}</span><h3>{showSources ? "What ran, skipped, failed, and advanced" : "Daily reports bound to exact evidence"}</h3></div><small>{showSources ? `${sourceItems.length} exact manifest source-family rows` : `${returned} of ${total}${truncated ? " · bounded view" : ""}`}</small></div>{showSources ? <SourceRows items={sourceItems} /> : <ReportRows items={reportItems} onOpen={onOpenReport} loadingRunId={reportLoadingRunId} />}</section>
      <CommandSection title="Report controls" commands={commands.filter((command) => command.id.includes("report") || command.id.includes("source"))} onSelect={onSelect} />
      <p className="operator-boundary-note">Convenience “latest” mirrors never become run evidence. Refresh is enabled only when a completed summary supplies the exact metrics, queue, and report pointers.</p>
    </>
  );
}

function VerifiedRunsSurface({ reports, sources, progress, commands, onSelect, onOpenReport, reportLoadingRunId }: { reports: UnknownRecord; sources: UnknownRecord; progress: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void; onOpenReport: (runId: string) => Promise<void>; reportLoadingRunId: string }) {
  const reportItems = firstList(reports, ["items"]);
  const returned = number(reports.items_returned, reportItems.length);
  const total = number(reports.total, reportItems.length);
  return (
    <>
      <CurrentRunProgressCard progress={progress} />
      <section className="operator-metrics"><article><span>Verified runs</span><strong>{total}</strong><small>Complete evidence chain</small></article><article><span>Latest run</span><strong className="metric-compact">{text(reports.latest_run_id, "Unavailable")}</strong><small>Run-scoped pointer</small></article><article><span>Latest sources</span><strong>{number(sources.total)}</strong><small>Manifest source families</small></article><article><span>Latest failures</span><strong>{number(reports.failure_count ?? sources.failure_count)}</strong><small>Never hidden</small></article></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>Verified run ledger</span><h3>Nightly runs with exact bound artifacts</h3></div><small>{returned} of {total}{Boolean(reports.truncated) ? " · bounded view" : ""}</small></div><ReportRows items={reportItems} onOpen={onOpenReport} loadingRunId={reportLoadingRunId} /></section>
      <CommandSection title="Verified run controls" commands={commands.filter((command) => command.id === "production.preflight" || command.id.includes("report"))} onSelect={onSelect} />
      <p className="operator-boundary-note">A row appears only after the nightly summary, manifest, source metrics, queue, and Outreach report pass the complete evidence chain. Mutable workspace snapshots are never substituted for a run artifact.</p>
    </>
  );
}

function CurrentRunProgressCard({ progress, compact = false }: { progress: UnknownRecord; compact?: boolean }) {
  const phase = asRecord(progress.phase);
  const timestamps = asRecord(progress.timestamps);
  const counts = asRecord(progress.counts);
  const evidence = asList(progress.evidence);
  const status = text(progress.status, "unavailable").toLowerCase();
  const active = Boolean(progress.is_current) && ["running", "attention", "partial"].includes(status);
  const linkedinLeaseEvidenced = active && (
    evidence.some((item) => text(item.kind).toLowerCase().includes("linkedin"))
    || counts.scoring_attempted !== null && counts.scoring_attempted !== undefined
    || text(phase.id).toLowerCase().includes("linkedin")
    || text(phase.id).toLowerCase() === "track_2"
  );
  const value = (raw: unknown): string => raw === null || raw === undefined || raw === "" ? "—" : String(number(raw));
  const scoringAvailable = counts.scoring_attempted !== null && counts.scoring_attempted !== undefined;
  const countCards = scoringAvailable ? [
    ["Scoring attempts", value(counts.scoring_attempted), "Exact current artifact"],
    ["Scoring errors", value(counts.scoring_errors), "Must never hide behind exit zero"],
    ["Accepted", value(counts.accepted_for_write), "Advanced for write"],
    ["Discovered", value(counts.items_discovered ?? counts.raw_total), "Current evidenced total"],
  ] : [
    ["Searches", counts.searches_completed === null || counts.searches_completed === undefined ? "—" : `${value(counts.searches_completed)}/${value(counts.searches_total)}`, "Live source progress"],
    ["Discovered", value(counts.items_discovered ?? counts.raw_total), "Current evidenced total"],
    ["Kept", value(counts.kept_total), "Advanced after filtering"],
    ["Review", value(counts.pending_review_count ?? counts.decision_total), "Human decision surface"],
  ];
  return (
    <section className={`operator-run-progress ${active ? "run-progress-active" : ""} ${compact ? "run-progress-compact" : ""}`}>
      <span className="operator-live-announcement" role="status" aria-live="polite" aria-atomic="true">{active ? `Run ${text(progress.run_id, "active")}: ${text(phase.label, "in progress")}, ${status}.` : `Run evidence: ${text(phase.label, status)}.`}</span>
      <div className="operator-run-progress-head">
        <div><span>{active ? "Active nightly run" : text(progress.selection, "Run evidence").replaceAll("_", " ")}</span><h3>{text(phase.label, active ? "Run in progress" : "No active run")}</h3><p>{text(progress.reason, active ? "The companion is following minimized run-owned evidence on this Mac." : "The next verified run will appear here automatically.")}</p></div>
        <div className="operator-run-identity"><strong>{text(progress.run_id, "No run ID")}</strong><span className={`operator-live-state state-${status}`}>{active ? `● live · ${status}` : status}</span></div>
      </div>
      {active ? <div className="operator-progress-track" role="progressbar" aria-label="Current run phase" aria-valuetext={`${text(phase.label, "Run in progress")} · ${text(phase.status, status)}`}><i /></div> : null}
      <div className="operator-run-progress-meta"><span>Started <strong>{text(timestamps.started_at)}</strong></span><span>Last progress <strong>{text(timestamps.last_progress_at ?? timestamps.captured_at)}</strong></span><span>Evidence <strong>{evidence.length} bound artifacts</strong></span><span>Phase state <strong>{text(phase.status, status)}</strong></span></div>
      {!compact || active ? <div className="operator-run-counts">{countCards.map(([label, count, detail]) => <article key={label}><span>{label}</span><strong>{count}</strong><small>{detail}</small></article>)}</div> : null}
      {linkedinLeaseEvidenced ? <p className="operator-browser-lease">LinkedIn browser contract · ownership-scoped cleanup expected at finalization. This projection proves the run and LinkedIn-related phase or artifact, not a browser PID or cleanup result; verify terminal cleanup evidence before intervening.</p> : null}
    </section>
  );
}

function NextRunPlanSurface({ plan, progress, commands, onSelect }: { plan: UnknownRecord; progress: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const items = asList(plan.items);
  const total = number(plan.items_total, items.length);
  const returned = number(plan.items_returned, items.length);
  const highPriority = items.filter((item) => ["blocker", "critical", "high", "1"].includes(text(item.priority, "").toLowerCase())).length;
  const categories = new Set(items.map((item) => text(item.category, "other")));
  const currentRun = Boolean(plan.current_run_in_progress) || (progress.status === "running" && Boolean(progress.is_current));
  const runControls = commands.filter((command) => command.id === "nightly.run" || command.id === "production.preflight" || command.id === "reports.daily.refresh");
  const budgets = asRecord(plan.budgets);
  const queueItems = asList(plan.queue_items);
  const queueTotal = number(plan.queue_items_total, queueItems.length);
  const queueReturned = number(plan.queue_items_returned, queueItems.length);
  const queueTruncated = Boolean(plan.queue_items_truncated);
  const queueStatus = text(plan.queue_items_status, queueItems.length ? "available" : "unavailable");
  const highLeveragePeople = asList(plan.high_leverage_people);
  return (
    <>
      {currentRun ? <CurrentRunProgressCard progress={progress} compact /> : null}
      <section className="operator-metrics"><article><span>Plan items</span><strong>{total}</strong><small>{returned} shown</small></article><article><span>High priority</span><strong>{highPriority}</strong><small>First-pass work</small></article><article><span>Workstreams</span><strong>{categories.size}</strong><small>Evidence-derived</small></article><article><span>Basis run</span><strong className="metric-compact">{text(plan.basis_run_id, "Unavailable")}</strong><small>{text(plan.basis_run_status, "No exact basis")}</small></article></section>
      <section className="operator-metrics">
        <article><span>Invite target</span><strong>{number(budgets.max_linkedin_invites)}</strong><small>LinkedIn invites</small></article>
        <article><span>Follow-ups</span><strong>{number(budgets.max_linkedin_followups)}</strong><small>Track 2 budget</small></article>
        <article><span>Mapping</span><strong>{number(budgets.max_company_mapping)}</strong><small>Company passes</small></article>
        <article><span>Queue rows</span><strong>{queueTotal}</strong><small>{queueStatus} · {queueReturned} shown</small></article>
      </section>
      <section className="operator-panel operator-next-plan">
        <div className="operator-panel-head"><div><span>Prioritized action plan</span><h3>{currentRun ? "Working plan while tonight’s run is active" : "Next cycle, in evidence order"}</h3></div><small>{returned} of {total}{Boolean(plan.truncated) ? " · bounded view" : ""}</small></div>
        {items.length ? <div className="operator-plan-list">{items.map((item, index) => {
          const evidence = asRecord(item.evidence);
          const evidenceBits = [evidence.source, evidence.status, evidence.lane, evidence.review_state, evidence.run_id].filter((entry) => entry !== null && entry !== undefined && entry !== "").map(String);
          return <article key={text(item.id, `${index}`)}><span className="operator-plan-rank">{String(index + 1).padStart(2, "0")}</span><div><small>{text(item.category, "run")} · {text(item.priority, "normal")} priority</small><h3>{text(item.title, "Untitled action")}</h3><p>{text(item.reason, "Exact run evidence supports this next action.")}</p><em>{evidenceBits.length ? evidenceBits.join(" · ") : "Evidence binding retained locally"}</em></div>{item.count !== null && item.count !== undefined ? <strong>{number(item.count)}</strong> : null}</article>;
        })}</div> : <p className="operator-empty-row">{text(plan.reason, "No grounded next-run actions are available yet.")}</p>}
      </section>
      <section className="operator-panel">
        <div className="operator-panel-head"><div><span>Exact action queue</span><h3>Ranked companies before the next run</h3></div><small>{queueReturned} of {queueTotal}{queueTruncated ? " · bounded view" : ""}</small></div>
        {queueItems.length ? (
          <div className="operator-queue-table operator-next-queue-scroll">
            {queueItems.map((item, index) => {
              const reasons = stringList(item.reasons).slice(0, 3).join(" · ");
              const fit = typeof item.fit_score === "number" && item.fit_score > 0 ? item.fit_score.toFixed(1) : "—";
              const summary = text(item.action_summary, text(item.recommended_action, "Queued").replaceAll("_", " "));
              const phase = text(item.plan_phase, "").replace(/^\d+_/, "").replaceAll("_", " ");
              const tier = text(item.tier, "");
              const laneLabel = text(item.lane, "lane") === "track_2_plan" ? "Track 2 plan" : text(item.lane, "lane").replaceAll("_", " ");
              return (
                <article key={text(item.id, `${index}`)}>
                  <span className="operator-rank">{number(item.rank, index + 1)}</span>
                  <div>
                    <strong>{text(item.company, "Unnamed company")}{tier ? ` · Tier ${tier}` : ""}</strong>
                    <p>{summary}</p>
                    <small>{laneLabel}{phase ? ` · ${phase}` : ""} · {text(item.target_run, "next run").replaceAll("-", " ")}{reasons ? ` · ${reasons}` : ""}</small>
                  </div>
                  <div className="operator-materials">
                    <span className="ready">{text(item.planned_channel, text(item.source, "source")).replaceAll("_", " ")}</span>
                    {text(item.role_title, "") ? <span className="optional">{text(item.role_title, "")}</span> : null}
                  </div>
                  <div className="operator-item-actions"><small>{text(asRecord(item.evidence).run_id, text(plan.basis_run_id, "—"))}</small></div>
                  <strong className="operator-score">{fit}</strong>
                </article>
              );
            })}
          </div>
        ) : (
          <p className="operator-empty-row">{text(plan.queue_items_reason, "No exact action-queue rows are bound yet.")}</p>
        )}
        {number(plan.automatic_followups_hidden, 0) > 0 ? <p className="operator-boundary-note">{number(plan.automatic_followups_hidden, 0)} automatic follow-up / continue-conversation actions run inside the nightly and are hidden from this decision queue.</p> : null}
      </section>
      {highLeveragePeople.length ? (
        <section className="operator-panel">
          <div className="operator-panel-head"><div><span>High-leverage people</span><h3>Senior contacts with a warm path</h3></div><small>{highLeveragePeople.length} flagged</small></div>
          <div className="operator-row-list">
            {highLeveragePeople.map((person, index) => (
              <article key={`${text(person.company, `${index}`)}`}>
                <span>{text(person.tier, "") ? `Tier ${text(person.tier, "")}` : "account"}</span>
                <div>
                  <strong>{text(person.company, "Unnamed company")}</strong>
                  <p>{text(person.contacts, "")}</p>
                </div>
                <small>score {number(person.account_score, 0)}</small>
              </article>
            ))}
          </div>
        </section>
      ) : null}
      {queueTruncated ? <p className="operator-boundary-note">This is a bounded projection showing {queueReturned} of {queueTotal} exact action-queue rows from the basis run.</p> : null}
      {text(plan.plan_reason, "") ? <p className="operator-boundary-note">{text(plan.plan_reason, "")} Rows show action-queue data without planned counts.</p> : null}
      {text(budgets.note) ? <p className="operator-boundary-note">{text(budgets.note)}</p> : null}
      {currentRun ? <p className="operator-boundary-note">This plan is intentionally provisional while the current run is active. It will rebase on the new exact summary, source metrics, action queue, and report after final verification.</p> : null}
      <CommandSection title="Next-run controls" commands={runControls} onSelect={onSelect} />
    </>
  );
}

function SourceRows({ items }: { items: UnknownRecord[] }) {
  if (!items.length) return <p className="operator-empty-row">No exact manifest source-family metrics are eligible yet.</p>;
  return <div className="operator-row-list">{items.map((item, index) => {
    const raw = number(item.raw_count);
    const kept = number(item.kept_count);
    const errors = stringList(item.errors);
    return <article key={text(item.source, `${index}`)}><span>{text(item.status, "not reported")}</span><div><strong>{text(item.source, "Unnamed source")}</strong><p>{raw} raw · {kept} kept{errors.length ? ` · ${errors.join(" ")}` : ""}</p></div><small>{kept}/{raw}</small></article>;
  })}</div>;
}

function ReportRows({ items, onOpen, loadingRunId }: { items: UnknownRecord[]; onOpen: (runId: string) => Promise<void>; loadingRunId: string }) {
  if (!items.length) return <p className="operator-empty-row">No verified daily report is eligible yet.</p>;
  return <div className="operator-row-list">{items.map((item, index) => {
    const workspace = asRecord(item.workspace_counts);
    const workspaceDetail = Object.entries(workspace).slice(0, 3).map(([label, value]) => `${label.replaceAll("_", " ")} ${number(value)}`).join(" · ");
    const deliveryMode = text(item.delivery_mode, "not reported").replaceAll("_", " ");
    const reportHealth = text(item.run_status, "not reported").replaceAll("_", " ");
    const detail = [deliveryMode, `report ${reportHealth}`, `${number(item.source_count)} sources`, `${number(item.failure_count)} summary failures`, `${number(item.pending_review_count)} pending review`, workspaceDetail].filter(Boolean).join(" · ");
    const runId = text(item.run_id, "");
    return <article key={runId || `${index}`}><span>{text(item.status, "verified")}</span><div><strong>{runId || "Run ID unavailable"}</strong><p>{detail}</p></div><div className="operator-report-row-actions"><small>{text(item.completed_at ?? item.started_at, "—")}</small><button type="button" disabled={!runId || Boolean(loadingRunId)} onClick={() => void onOpen(runId)}>{loadingRunId === runId ? "Loading…" : "View full report"}</button></div></article>;
  })}</div>;
}

function ExactReportViewer({ report, onClose }: { report: OperatorReportDocument; onClose: () => void }) {
  const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'">`;
  const sandboxedHtml = /<head(?:\s[^>]*)?>/i.test(report.html)
    ? report.html.replace(/<head(\s[^>]*)?>/i, (match) => `${match}${policy}`)
    : `<!doctype html><html><head>${policy}</head><body>${report.html}</body></html>`;
  return (
    <section className="operator-panel operator-report-viewer" aria-labelledby="exact-report-title">
      <header>
        <div><span>Authenticated exact artifact</span><h3 id="exact-report-title">Daily report · {report.run_id}</h3><small>{Math.ceil(report.size_bytes / 1024)} KB · SHA {report.sha256.slice(0, 12)}…</small></div>
        <button type="button" onClick={onClose} aria-label="Close full report">Close</button>
      </header>
      <iframe title={`Exact daily report ${report.run_id}`} sandbox="" referrerPolicy="no-referrer" srcDoc={sandboxedHtml} />
      <p>This viewer is sandboxed with scripts, forms, top-level navigation, and remote subresources disabled. The document remains served only by your authenticated local companion.</p>
    </section>
  );
}

function ReviewQueue({
  lanes,
  reviews,
  onSelect,
  loading,
}: {
  lanes: OperatorReviewLane[];
  reviews: OperatorReview[];
  onSelect: (target: OperatorReviewTarget) => void;
  loading: boolean;
}) {
  const reviewByTarget = new Map(
    reviews
      .filter((review) => ["pending", "reviewed", "approved"].includes(review.state))
      .map((review) => [`${review.command_id}:${review.target_id}`, review]),
  );
  return (
    <section className="operator-panel operator-review-queue">
      <div className="operator-panel-head">
        <div><span>Human-owned review gates</span><h3>Exact targets before consequential action</h3></div>
        <small>One target · 24-hour approval · hash-bound</small>
      </div>
      <div className="operator-review-lanes">
        {lanes.map((lane) => (
          <article key={lane.command_id} className="operator-review-lane">
            <header>
              <div><span>{lane.category || "review"}</span><strong>{lane.label || lane.command_id}</strong></div>
              <small className={`review-lane-state lane-${lane.state || "waiting_for_contract"}`}>{lane.state === "review_stage_available" ? "Review available" : "Waiting for contract"}</small>
            </header>
            <p>{lane.description}</p>
            {(lane.targets ?? []).length ? (
              <div className="operator-review-targets">
                {(lane.targets ?? []).slice(0, 8).map((target) => {
                  const review = reviewByTarget.get(`${lane.command_id}:${target.target_id}`);
                  return (
                    <button key={target.target_id} type="button" disabled={loading} onClick={() => onSelect(target)}>
                      <span>{review?.state || "unreviewed"}</span>
                      <strong>{target.label || target.target_id}</strong>
                      <small>{target.detail || "Open exact private review detail."}</small>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="operator-review-blocked"><strong>No eligible exact target</strong><p>{lane.reason || "The installed source contract is not yet proven."}</p></div>
            )}
            <footer><span>{lane.targets_returned ?? 0} target{lane.targets_returned === 1 ? "" : "s"}</span><strong>Execution: {lane.execution_state?.replaceAll("_", " ") || "blocked"}</strong></footer>
          </article>
        ))}
      </div>
      <p className="operator-boundary-note">Review and approval never imply execution. A changed artifact, recipient, thread, or text invalidates the target hash; uncertain delivery is consumed and reconciled instead of silently retried.</p>
    </section>
  );
}

function OperationsSurface({ overview, commands, onSelect }: { overview: OperatorOverview; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const locks = overview.guard?.locks ?? {};
  const jobs = overview.recent_jobs ?? [];
  return (
    <>
      <section className="operator-split"><div className="operator-panel"><div className="operator-panel-head"><div><span>Capability registry</span><h3>Fixed local actions</h3></div></div><CommandButtons commands={commands} onSelect={onSelect} full /></div><aside className="operator-panel operator-locks"><span className="operator-kicker">Concurrency guard</span><h3>Production locks</h3>{Object.keys(locks).length ? Object.entries(locks).map(([name, state]) => <div key={name}><span><i className={`lock-${state}`} />{name.replaceAll("_", " ")}</span><strong>{state}</strong></div>) : <p>No lock registry is configured.</p>}</aside></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>Audit trail</span><h3>Recent cockpit jobs</h3></div></div>{jobs.length ? <div className="operator-job-list">{jobs.map((job) => <article key={job.id}><span>{job.status ?? "unknown"}</span><div><strong>{job.label || job.command_id || job.id}</strong><p>{job.summary || job.error || "No summary recorded."}{job.result_run_id ? ` Run ${job.result_run_id} · ${job.result_health || "health unavailable"} · ${(job.result_delivery_mode || "delivery not reported").replaceAll("_", " ")}.` : ""}</p></div><small>{job.completed_at || job.started_at || job.created_at || "—"}</small></article>)}</div> : <p className="operator-empty-row">No cockpit action has run yet.</p>}</section>
      <p className="operator-boundary-note">Consequential capabilities have visible review lanes but stay non-executable until their installed fixed-argument, reservation, archive, and reconciliation contracts are proven. Arbitrary shell access remains impossible.</p>
    </>
  );
}

function CommandSection({ title, commands, onSelect }: { title: string; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  return <section className="operator-panel operator-command-section"><div className="operator-panel-head"><div><span>Local command registry</span><h3>{title}</h3></div></div><CommandButtons commands={commands} onSelect={onSelect} full /></section>;
}

function CommandButtons({ commands, onSelect, full = false }: { commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void; full?: boolean }) {
  if (!commands.length) return <p className="operator-empty-row">No safe command is registered for this surface yet.</p>;
  return <div className={full ? "operator-commands operator-commands-full" : "operator-commands"}>{commands.map((command) => { const state = commandState(command); return <button key={command.id} type="button" onClick={() => state === "available" && onSelect(command)} disabled={state !== "available"}><span className={`state-${state}`}>{state}</span><strong>{command.label || command.id}</strong><p>{command.description || commandReason(command)}</p><small>{state === "available" ? "Review and confirm →" : commandReason(command)}</small></button>; })}</div>;
}

function OperatorRows({ items, empty }: { items: UnknownRecord[]; empty: string }) {
  if (!items.length) return <p className="operator-empty-row">{empty}</p>;
  return <div className="operator-row-list">{items.slice(0, 12).map((item, index) => <article key={text(item.id ?? item.source_id ?? item.run_id ?? item.name ?? item.company, `${index}`)}><span>{text(item.state ?? item.status ?? item.tier ?? item.kind, "Live")}</span><div><strong>{text(item.title ?? item.label ?? item.company ?? item.source ?? item.name, "Untitled")}</strong><p>{text(item.detail ?? item.summary ?? item.description ?? item.role ?? item.next_action ?? item.note, "Local evidence available")}</p></div><small>{text(item.meta ?? item.created_at ?? item.updated_at ?? item.count ?? item.total, "")}</small></article>)}</div>;
}

function Breakdown({ title, data }: { title: string; data: UnknownRecord }) {
  const entries = Object.entries(data).sort((left, right) => number(right[1]) - number(left[1])).slice(0, 8);
  return <div className="operator-breakdown-list"><strong>{title}</strong>{entries.length ? entries.map(([label, value]) => <div key={label}><span>{label.replaceAll("_", " ")}</span><b>{number(value)}</b></div>) : <p>Not available</p>}</div>;
}

"use client";

import { useState } from "react";

import type {
  OperatorActionResult,
  OperatorCommand,
  OperatorOverview,
  OperatorQueueAction,
  OperatorQueueItem,
} from "@/lib/operator-contract";

export type OperatorView =
  | "dashboard"
  | "sources"
  | "queue"
  | "runs"
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
};

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
  "nightly.run": ["Run full nightly pipeline", "Disabled here because production arguments may include live sends."],
  "outreach.send": ["Send outreach", "Disabled in the generic cockpit command surface."],
  "application.submit": ["Submit application", "Final application submission remains human-owned."],
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

export function OperatorWorkspace({ view, overview, connected, onAction }: OperatorWorkspaceProps) {
  const [selected, setSelected] = useState<OperatorCommand | null>(null);
  const [selectedParameters, setSelectedParameters] = useState<Record<string, unknown>>({});
  const [confirmation, setConfirmation] = useState("");
  const [executing, setExecuting] = useState(false);
  const [actionMessage, setActionMessage] = useState("");

  const assets = asRecord(overview?.assets);
  const workbooks = asRecord(assets.workbooks);
  const rawQueue = asRecord(assets.current_apply_queue);
  const queue = { ...asRecord(rawQueue.summary), ...rawQueue };
  const storyComms = asRecord(assets.story_comms);
  const reports = asRecord(assets.daily_reports);
  const sources = asRecord(assets.source_metrics);
  const commands = normalizeCommands(overview?.capabilities?.commands ?? []);
  const items = queueItems(queue);
  const copy = viewCopy[view];

  const selectCommand = (command: OperatorCommand, parameters: Record<string, unknown> = {}) => {
    setSelectedParameters(parameters);
    setSelected(command);
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

      {view === "dashboard" ? <OperatorDashboard overview={overview} workbooks={workbooks} queue={queue} storyComms={storyComms} reports={reports} commands={commands} onSelect={selectCommand} /> : null}
      {view === "accounts" ? <AccountsSurface workbooks={workbooks} commands={commands} onSelect={selectCommand} /> : null}
      {view === "queue" ? <ApplyQueueSurface queue={queue} items={items} commands={commands} onSelect={selectCommand} /> : null}
      {view === "applications" ? <ApplicationHistorySurface workbooks={workbooks} commands={commands} onSelect={selectCommand} /> : null}
      {view === "stories" ? <StorySurface storyComms={storyComms} commands={commands} onSelect={selectCommand} /> : null}
      {view === "outreach" ? <CommunicationSurface storyComms={storyComms} commands={commands} onSelect={selectCommand} /> : null}
      {view === "reports" || view === "sources" ? <ReportSurface reports={reports} sources={sources} commands={commands} onSelect={selectCommand} showSources={view === "sources"} /> : null}
      {view === "runs" ? <VerifiedRunsSurface reports={reports} sources={sources} commands={commands} onSelect={selectCommand} /> : null}
      {view === "operations" ? <OperationsSurface overview={overview} commands={commands} onSelect={selectCommand} /> : null}

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
      <small>{busy ? "Immutable run evidence remains readable; mutable queue and workbook projections pause." : `${Object.keys(locks).length} lock boundaries reported · external sends disabled`}</small>
    </aside>
  );
}

function OperatorDashboard({ overview, workbooks, queue, storyComms, reports, commands, onSelect }: { overview: OperatorOverview; workbooks: UnknownRecord; queue: UnknownRecord; storyComms: UnknownRecord; reports: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
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
      <section className="operator-metrics" aria-label="Operator workspace metrics">{metrics.map(([label, value, detail]) => <article key={label}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>)}</section>
      <section className="operator-grid operator-system-grid">
        {systemCards.map(([label, data, available, detail]) => <article key={label}><span className={available ? "system-online" : "system-offline"}>{available ? "Available" : text(data.status, "Unavailable")}</span><h3>{label}</h3><p>{detail}</p><small>{available ? "Minimized local projection" : text(data.reason, "Capability reason available in Operations")}</small></article>)}
      </section>
      <section className="operator-panel operator-command-strip"><div><span>Common moves</span><h3>Open the real workspace or refresh a safe view.</h3><p>Production writes remain disabled while upstream locks are occupied.</p></div><CommandButtons commands={commands.filter((command) => commandState(command) === "available").slice(0, 4)} onSelect={onSelect} /></section>
      <section className="operator-fineprint">Generated {text(overview.generated_at, "by the local companion")} · data class {text(overview.data_class, "local-private")} · arbitrary commands unavailable</section>
    </>
  );
}

function AccountsSurface({ workbooks, commands, onSelect }: { workbooks: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const tracker = firstRecord(workbooks, ["account_tracker", "accounts", "outreach"]);
  const actions = firstList(tracker, ["action_queue", "action_items", "top_actions", "items"]);
  const actionTotal = number(tracker.action_items_total ?? tracker.action_count, actions.length);
  const actionReturned = number(tracker.action_items_returned, actions.length);
  const actionsTruncated = Boolean(tracker.action_items_truncated);
  const stages = asRecord(tracker.stage_counts ?? tracker.by_stage);
  const tiers = asRecord(tracker.tier_counts ?? tracker.by_tier);
  const activity = asRecord(tracker.activity_totals);
  const sheetRows = asRecord(tracker.sheet_row_counts);
  return (
    <>
      <section className="operator-metrics"><article><span>Companies</span><strong>{number(tracker.total ?? tracker.account_count ?? tracker.rows)}</strong><small>Tracker universe</small></article><article><span>Action queue</span><strong>{number(tracker.action_count ?? sheetRows["Action Queue"] ?? actions.length)}</strong><small>Due or active</small></article><article><span>People mapped</span><strong>{number(tracker.people_mapped ?? tracker.contacts ?? activity["People Mapped"])}</strong><small>Relationship surface</small></article><article><span>Workbook sheets</span><strong>{number(tracker.sheet_count ?? tracker.sheets ?? Object.keys(sheetRows).length)}</strong><small>Derived views</small></article></section>
      <section className="operator-split">
        <div className="operator-panel"><div className="operator-panel-head"><div><span>Today’s queue</span><h3>Account moves worth attention</h3></div><small>{actionReturned} of {actionTotal}{actionsTruncated ? " · bounded view" : ""}</small></div><OperatorRows items={actions} empty="No account actions were projected." /></div>
        <aside className="operator-panel operator-breakdown"><span>Portfolio state</span><h3>Stage and tier mix</h3><Breakdown title="Stages" data={stages} /><Breakdown title="Tiers" data={tiers} /></aside>
      </section>
      <CommandSection title="Account controls" commands={commands.filter((command) => command.id.includes("account") || command.id.includes("campaign"))} onSelect={onSelect} />
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
      <p className="operator-boundary-note">Status changes are intentionally unavailable until artifact-preserving archive semantics are guaranteed. “Applied” must never make a generated resume disappear.</p>
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
      <p className="operator-boundary-note">This projection reports aggregate outcomes and recommendation-review state only. It does not claim to be a draft queue, and external sends stay disabled in this cockpit release.</p>
    </>
  );
}

function ReportSurface({ reports, sources, commands, onSelect, showSources }: { reports: UnknownRecord; sources: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void; showSources: boolean }) {
  const reportItems = firstList(reports, ["items", "reports", "run_reports"]);
  const latestSources = asRecord(sources.latest);
  const sourceItems = firstList(sources, ["items", "sources", "source_breakdown"]).length ? firstList(sources, ["items", "sources", "source_breakdown"]) : firstList(latestSources, ["sources", "source_breakdown"]);
  const returned = number(reports.items_returned, reportItems.length);
  const total = number(reports.total ?? reports.count, reportItems.length);
  const truncated = Boolean(reports.truncated);
  return (
    <>
      <section className="operator-metrics"><article><span>Verified reports</span><strong>{number(reports.total ?? reports.count ?? reportItems.length)}</strong><small>Exact run scope</small></article><article><span>Latest run</span><strong className="metric-compact">{text(reports.latest_run_id ?? sources.run_id ?? latestSources.run_id, "Unavailable")}</strong><small>Immutable pointer</small></article><article><span>Sources</span><strong>{number(sources.total ?? sourceItems.length)}</strong><small>Explicit coverage</small></article><article><span>Failures</span><strong>{number(reports.failure_count ?? sources.failure_count)}</strong><small>Never hidden</small></article></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>{showSources ? "Source breakdown" : "Run-scoped briefs"}</span><h3>{showSources ? "What ran, skipped, failed, and advanced" : "Daily reports bound to exact evidence"}</h3></div><small>{showSources ? `${sourceItems.length} exact source rows` : `${returned} of ${total}${truncated ? " · bounded view" : ""}`}</small></div>{showSources ? <SourceRows items={sourceItems} /> : <ReportRows items={reportItems} />}</section>
      <CommandSection title="Report controls" commands={commands.filter((command) => command.id.includes("report") || command.id.includes("source"))} onSelect={onSelect} />
      <p className="operator-boundary-note">Convenience “latest” mirrors never become run evidence. Refresh is enabled only when a completed summary supplies the exact metrics, queue, and report pointers.</p>
    </>
  );
}

function VerifiedRunsSurface({ reports, sources, commands, onSelect }: { reports: UnknownRecord; sources: UnknownRecord; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const reportItems = firstList(reports, ["items"]);
  const returned = number(reports.items_returned, reportItems.length);
  const total = number(reports.total, reportItems.length);
  return (
    <>
      <section className="operator-metrics"><article><span>Verified runs</span><strong>{total}</strong><small>Complete evidence chain</small></article><article><span>Latest run</span><strong className="metric-compact">{text(reports.latest_run_id, "Unavailable")}</strong><small>Run-scoped pointer</small></article><article><span>Latest sources</span><strong>{number(sources.total)}</strong><small>Explicit source rows</small></article><article><span>Latest failures</span><strong>{number(reports.failure_count ?? sources.failure_count)}</strong><small>Never hidden</small></article></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>Verified run ledger</span><h3>Nightly runs with exact bound artifacts</h3></div><small>{returned} of {total}{Boolean(reports.truncated) ? " · bounded view" : ""}</small></div><ReportRows items={reportItems} /></section>
      <CommandSection title="Verified run controls" commands={commands.filter((command) => command.id === "production.preflight" || command.id.includes("report"))} onSelect={onSelect} />
      <p className="operator-boundary-note">A row appears only after the nightly summary, manifest, source metrics, queue, and Outreach report pass the complete evidence chain. Mutable workspace snapshots are never substituted for a run artifact.</p>
    </>
  );
}

function SourceRows({ items }: { items: UnknownRecord[] }) {
  if (!items.length) return <p className="operator-empty-row">No exact source metrics are eligible yet.</p>;
  return <div className="operator-row-list">{items.map((item, index) => {
    const raw = number(item.raw_count);
    const kept = number(item.kept_count);
    const errors = stringList(item.errors);
    return <article key={text(item.source, `${index}`)}><span>{text(item.status, "not reported")}</span><div><strong>{text(item.source, "Unnamed source")}</strong><p>{raw} raw · {kept} kept{errors.length ? ` · ${errors.join(" ")}` : ""}</p></div><small>{kept}/{raw}</small></article>;
  })}</div>;
}

function ReportRows({ items }: { items: UnknownRecord[] }) {
  if (!items.length) return <p className="operator-empty-row">No verified daily report is eligible yet.</p>;
  return <div className="operator-row-list">{items.map((item, index) => {
    const workspace = asRecord(item.workspace_counts);
    const workspaceDetail = Object.entries(workspace).slice(0, 3).map(([label, value]) => `${label.replaceAll("_", " ")} ${number(value)}`).join(" · ");
    const detail = [`${number(item.source_count)} sources`, `${number(item.failure_count)} failures`, `${number(item.pending_review_count)} pending review`, workspaceDetail].filter(Boolean).join(" · ");
    return <article key={text(item.run_id, `${index}`)}><span>{text(item.status, "verified")}</span><div><strong>{text(item.run_id, "Run ID unavailable")}</strong><p>{detail}</p></div><small>{text(item.completed_at ?? item.started_at, "—")}</small></article>;
  })}</div>;
}

function OperationsSurface({ overview, commands, onSelect }: { overview: OperatorOverview; commands: OperatorCommand[]; onSelect: (command: OperatorCommand) => void }) {
  const locks = overview.guard?.locks ?? {};
  const jobs = overview.recent_jobs ?? [];
  return (
    <>
      <section className="operator-split"><div className="operator-panel"><div className="operator-panel-head"><div><span>Capability registry</span><h3>Fixed local actions</h3></div></div><CommandButtons commands={commands} onSelect={onSelect} full /></div><aside className="operator-panel operator-locks"><span className="operator-kicker">Concurrency guard</span><h3>Production locks</h3>{Object.keys(locks).length ? Object.entries(locks).map(([name, state]) => <div key={name}><span><i className={`lock-${state}`} />{name.replaceAll("_", " ")}</span><strong>{state}</strong></div>) : <p>No lock registry is configured.</p>}</aside></section>
      <section className="operator-panel"><div className="operator-panel-head"><div><span>Audit trail</span><h3>Recent cockpit jobs</h3></div></div>{jobs.length ? <div className="operator-job-list">{jobs.map((job) => <article key={job.id}><span>{job.status ?? "unknown"}</span><div><strong>{job.label || job.command_id || job.id}</strong><p>{job.summary || job.error || "No summary recorded."}</p></div><small>{job.completed_at || job.started_at || job.created_at || "—"}</small></article>)}</div> : <p className="operator-empty-row">No cockpit action has run yet.</p>}</section>
      <p className="operator-boundary-note">Nightly execution, LinkedIn/email sends, arbitrary shell commands, and final application submit are forbidden capabilities—not hidden buttons.</p>
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

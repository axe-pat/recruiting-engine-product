export type OperatorCommand = {
  id: string;
  command_id?: string;
  label?: string;
  description?: string;
  category?: string;
  risk?: "read" | "local_write" | "external" | string;
  available?: boolean;
  state?: string;
  status?: string;
  kind?: string;
  confirmation?: string;
  confirmation_phrase?: string;
  requires_confirmation?: boolean;
  confirmation_required?: boolean;
  unavailable_reason?: string;
  reason?: string;
  requires_approved_review?: boolean;
  maximum_items?: number | null;
  execution_contract?: string;
};

export type OperatorQueueAction = {
  command_id: string;
  status?: string;
  reason?: string;
  confirmation_phrase?: string;
  parameters?: { job_id: number } | null;
  asynchronous?: boolean;
};

export type OperatorJob = {
  id: string;
  command_id?: string;
  label?: string;
  status?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  summary?: string;
  error?: string;
  result_code?: string;
  result_run_id?: string;
  result_health?: string;
  result_report_sha256?: string;
  result_delivery_mode?: string;
};

export type OperatorReviewTarget = {
  target_id: string;
  command_id: string;
  target_type?: string;
  label?: string;
  detail?: string;
  artifact_sha256?: string;
  bounded_limit?: number;
  job_id?: number | null;
  channel?: string | null;
  recipient_ref?: string | null;
  review_confirmation_phrase?: string;
};

export type OperatorReviewLane = {
  command_id: string;
  label?: string;
  description?: string;
  category?: string;
  state?: string;
  execution_state?: string;
  reason?: string;
  targets?: OperatorReviewTarget[];
  targets_returned?: number;
  targets_total?: number;
  truncated?: boolean;
};

export type OperatorReview = {
  id: string;
  command_id: string;
  label?: string;
  target_id: string;
  target_type?: string;
  target_label?: string;
  artifact_sha256?: string;
  state: string;
  job_id?: number | null;
  channel?: string | null;
  recipient_ref?: string | null;
  bounded_limit?: number;
  review_confirmation_phrase?: string;
  approval_confirmation_phrase?: string;
  revocation_confirmation_phrase?: string;
  action_confirmation_phrase?: string;
  expires_at?: string;
  reviewed_at?: string | null;
  approved_at?: string | null;
  revoked_at?: string | null;
  consumed_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type OperatorReviewPrivateDetail = OperatorReview & {
  reviewed_subject?: string | null;
  reviewed_text?: string | null;
};

export type OperatorReviewTargetDetail = OperatorReviewTarget & {
  recipient?: string | null;
  subject?: string | null;
  draft_text?: string | null;
  context?: string | null;
  content_binding?: string;
  maximum_items?: number;
};

export type OperatorQueueItem = {
  id?: string;
  job_id?: string;
  company?: string;
  role?: string;
  role_title?: string;
  fit_score?: number;
  priority_score?: number;
  priority_rank?: number;
  status?: string;
  queue_bucket?: string;
  has_resume?: boolean;
  has_cover_letter?: boolean;
  has_job_description?: boolean;
  has_strategy?: boolean;
  has_intel?: boolean;
  in_latest_run?: boolean;
  material_state?: string;
  actions?: OperatorQueueAction[];
};

export type OperatorCurrentRunProgress = {
  schema_version?: string;
  status?: "running" | "complete" | "attention" | "partial" | "unavailable";
  reason?: string;
  selection?: "current" | "most_recent_verified" | "latest_scheduler_attempt" | "unavailable";
  scope?: "current-snapshot" | "run-scoped";
  is_current?: boolean;
  run_id?: string | null;
  phase?: { id?: string; label?: string; status?: string };
  timestamps?: { started_at?: string | null; last_progress_at?: string | null; completed_at?: string | null; captured_at?: string | null };
  counts?: {
    searches_completed?: number | null;
    searches_total?: number | null;
    items_discovered?: number | null;
    source_families_total?: number | null;
    source_families_successful?: number | null;
    source_families_attention?: number | null;
    raw_total?: number | null;
    kept_total?: number | null;
    decision_total?: number | null;
    pending_review_count?: number | null;
    scoring_attempted?: number | null;
    scoring_errors?: number | null;
    accepted_for_write?: number | null;
  };
  evidence?: Array<{ kind?: string; state?: string; path?: string; sha256?: string; size_bytes?: number; binding?: string }>;
};

export type OperatorNextRunPlan = {
  schema_version?: string;
  status?: "available" | "partial" | "unavailable";
  reason?: string;
  scope?: "derived-plan";
  basis_run_id?: string | null;
  basis_run_status?: string | null;
  basis_completed_at?: string | null;
  current_run_in_progress?: boolean;
  items?: Array<{
    id?: string;
    category?: string;
    priority?: string | number;
    title?: string;
    reason?: string;
    count?: number | null;
    evidence?: { kind?: string; run_id?: string; source?: string; status?: string; lane?: string; review_state?: string; sha256?: string };
  }>;
  items_returned?: number;
  items_total?: number;
  truncated?: boolean;
  limit?: number;
  budgets?: {
    schema_version?: string;
    source?: string;
    max_total_actions?: number;
    max_companies?: number;
    max_linkedin_invites?: number;
    max_linkedin_followups?: number;
    max_company_mapping?: number;
    max_email_research?: number;
    max_context_enrichment?: number;
    max_email_drafts?: number;
    note?: string;
  };
  queue_items?: Array<{
    id?: string;
    rank?: number;
    company?: string;
    role_title?: string;
    lane?: string;
    target_run?: string;
    reasons?: string[];
    fit_score?: number | null;
    queue_rank?: number | null;
    recommended_action?: string;
    source?: string;
    planned_action?: string;
    planned_channel?: string;
    plan_phase?: string;
    planned_counts?: Record<string, number>;
    action_summary?: string;
    tier?: string;
    account_score?: number | null;
    evidence?: { kind?: string; run_id?: string; lane?: string; sha256?: string };
  }>;
  plan_status?: "bound" | "unavailable";
  plan_reason?: string;
  queue_items_returned?: number;
  queue_items_total?: number;
  queue_items_truncated?: boolean;
  queue_items_limit?: number;
  queue_items_status?: "available" | "partial" | "unavailable";
  queue_items_reason?: string;
};

export type OperatorAccountTracker = {
  schema_version?: string;
  status?: "available" | "busy" | "partial" | "unavailable";
  reason?: string;
  scope?: "current-snapshot";
  summary?: Record<string, unknown> | null;
  evidence?: { state?: string; path?: string; sha256?: string; size_bytes?: number } | null;
  open_action?: {
    command_id?: string;
    label?: string;
    status?: string;
    reason?: string;
    confirmation_phrase?: string;
    parameters?: Record<string, unknown>;
    asynchronous?: boolean;
  };
};

export type OperatorOverview = {
  schema_version?: string;
  generated_at?: string;
  mode?: string;
  data_class?: string;
  guard?: {
    locks?: Record<string, string>;
    busy?: boolean;
    production_guard?: string;
    mutation_gate?: string;
    external_actions?: string;
    reasons?: string[];
  };
  capabilities?: {
    mutations_enabled?: boolean;
    review_workflows_enabled?: boolean;
    approved_external_actions_enabled?: boolean;
    commands?: OperatorCommand[];
  };
  assets?: {
    workbooks?: Record<string, unknown>;
    current_apply_queue?: Record<string, unknown>;
    story_comms?: Record<string, unknown>;
    daily_reports?: Record<string, unknown>;
    source_metrics?: Record<string, unknown>;
    current_run_progress?: OperatorCurrentRunProgress;
    next_run_plan?: OperatorNextRunPlan;
    account_tracker?: OperatorAccountTracker;
  };
  recent_jobs?: OperatorJob[];
  review_queue?: {
    schema_version?: string;
    generated_at?: string;
    review_confirmation_phrase?: string;
    approval_confirmation_phrase?: string;
    revocation_confirmation_phrase?: string;
    expires_after_hours?: number;
    maximum_items_per_action?: number;
    lanes?: OperatorReviewLane[];
    recent_reviews?: OperatorReview[];
    review_counts?: Record<string, number>;
    recent_reviews_items_returned?: number;
    recent_reviews_items_total?: number;
    recent_reviews_truncated?: boolean;
    recent_reviews_limit?: number;
    recent_reviews_meta?: {
      items_returned?: number;
      items_total?: number;
      truncated?: boolean;
      limit?: number;
    };
    execution_boundary?: string;
  };
};

export type OperatorActionResult = {
  job?: OperatorJob;
  operator_job?: OperatorJob;
  message?: string;
};

export type OperatorReviewResult = {
  operator_review?: OperatorReviewPrivateDetail;
  review_target?: OperatorReviewTargetDetail;
};

export type OperatorReportDocument = {
  run_id: string;
  html: string;
  sha256: string;
  size_bytes: number;
  content_type: "text/html; charset=utf-8" | string;
};

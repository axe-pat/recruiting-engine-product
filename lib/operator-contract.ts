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

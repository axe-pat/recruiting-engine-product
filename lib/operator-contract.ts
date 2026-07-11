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
};

export type OperatorActionResult = {
  job?: OperatorJob;
  operator_job?: OperatorJob;
  message?: string;
};

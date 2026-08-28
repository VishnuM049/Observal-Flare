export type SiteStatus =
  | "pending"
  | "provisioning"
  | "deploying"
  | "running"
  | "stopping"
  | "stopped"
  | "sleeping"
  | "destroying"
  | "destroyed"
  | "failed";

export type DeployType = "branch" | "commit" | "pr" | "tag" | "release";

export type SleepMode = "none" | "nightly" | "idle";

export type UserRole = "admin" | "member";

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  is_active: boolean;
}

export type CloudProvider = "aws" | "gcp";

export type ExperimentStatus =
  | "pending"
  | "provisioning"
  | "running"
  | "destroying"
  | "completed"
  | "failed"
  | "cleanup_failed"
  | "cancelled";

export interface ExperimentConfig {
  enabled: boolean;
  target_ref: string;
  target_name: string | null;
  max_rate_per_minute: number;
  max_duration_minutes: number;
  max_concurrency: number;
  max_instances: number;
  max_images: number;
  max_transfer_bytes: number;
}

export interface ExperimentTarget {
  requested_ref: string;
  target_ref: string;
  package_url: string;
  platform: string;
  image_size_bytes: number;
  layer_count: number;
  weight: number;
  expected_pulls: number;
  estimated_transfer_bytes: number;
  launched_pulls: number;
  successful_pulls: number;
  failed_pulls: number;
  baseline_count: number | null;
  current_count: number | null;
  final_count: number | null;
}

export interface ExperimentInstance {
  index: number;
  instance_id: string | null;
  status: string;
  cleanup_status: string;
  launched_pulls: number;
  successful_pulls: number;
  failed_pulls: number;
  active_pulls: number;
  max_concurrency: number;
  last_progress_at: string | null;
  error_message: string | null;
  run_log: string | null;
  targets: Array<{
    target_ref: string;
    launched: number;
    successful: number;
    failed: number;
    active?: number;
  }>;
}

export interface Experiment {
  id: string;
  status: ExperimentStatus;
  target_ref: string;
  package_url: string;
  targets: ExperimentTarget[];
  rate_per_minute: number;
  duration_minutes: number;
  expected_pulls: number;
  instance_count: number;
  concurrency_limit: number;
  platform: string;
  image_size_bytes: number;
  layer_count: number;
  estimated_transfer_bytes: number;
  instance_type: string;
  launched_pulls: number;
  successful_pulls: number;
  failed_pulls: number;
  active_pulls: number;
  max_concurrency: number | null;
  baseline_count: number | null;
  immediate_count: number | null;
  delayed_count: number | null;
  results: Record<string, unknown>;
  instances: ExperimentInstance[];
  instance_id: string | null;
  terraform_state_key: string;
  cancellation_requested: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  destroyed_at: string | null;
  last_progress_at: string | null;
  error_message: string | null;
  cleanup_error: string | null;
}

export interface ExperimentEvent {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ExperimentPreflight {
  targets: ExperimentTarget[];
  estimated_transfer_bytes: number;
  within_transfer_limit: boolean;
  max_transfer_bytes: number;
}

export interface ExperimentCreateRequest {
  target_refs: string[];
  resolved_target_refs: string[];
  target_weights: number[];
  rate_per_minute: number;
  duration_minutes: number;
  concurrency_limit: number;
  instance_count: number;
  confirmation: string;
}

export interface Site {
  id: string;
  name: string;
  cloud_provider: CloudProvider;
  domain: string;
  status: SiteStatus;
  requestor_email: string;
  deploy_type: DeployType;
  deploy_ref: string;
  resolved_sha: string | null;
  auto_update: boolean;
  auto_wipe_on_failure: boolean;
  sleep_mode: SleepMode;
  idle_timeout_minutes: number;
  sleep_at_hour: number;
  wake_at_hour: number;
  instance_size: string;
  env_overrides: Record<string, string>;
  ip_address: string | null;
  instance_id: string | null;
  error_message: string | null;
  ttl_days: number | null;
  scheduled_destroy_at: string | null;
  created_at: string;
  updated_at: string;
  last_activity_at: string | null;
  last_deployed_at: string | null;
  destroyed_at: string | null;
}

export interface AuditLogEntry {
  id: string;
  site_id: string | null;
  user_id: string;
  user_name: string | null;
  user_email: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface DayCost {
  date: string;
  cost: number;
  site_count: number;
}

export interface CostSummary {
  history: DayCost[];
  projection: DayCost[];
  today_daily: number;
  today_site_count: number;
}

export interface SiteCreateRequest {
  name: string;
  cloud_provider?: CloudProvider;
  deploy_type: DeployType;
  deploy_ref: string;
  requestor_email: string;
  instance_size?: string;
  env_overrides?: Record<string, string>;
  auto_update?: boolean;
  auto_wipe_on_failure?: boolean;
  sleep_mode?: SleepMode;
  idle_timeout_minutes?: number;
  sleep_at_hour?: number;
  wake_at_hour?: number;
  ttl_days?: number | null;
}

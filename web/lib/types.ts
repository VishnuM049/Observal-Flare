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

export interface Site {
  id: string;
  name: string;
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

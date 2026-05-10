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

export type UserRole = "admin" | "member" | "guest";

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

export interface Invite {
  id: string;
  token: string;
  label: string | null;
  max_sites: number;
  allowed_instance_sizes: string[];
  forced_ttl_days: number | null;
  allowed_deploy_types: string[];
  env_overrides_locked: boolean;
  expires_at: string;
  max_uses: number | null;
  use_count: number;
  created_at: string;
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
}

export interface InviteCreateRequest {
  label?: string;
  max_sites?: number;
  allowed_instance_sizes?: string[];
  forced_ttl_days?: number | null;
  allowed_deploy_types?: string[];
  env_overrides_locked?: boolean;
  expires_at: string;
  max_uses?: number | null;
}

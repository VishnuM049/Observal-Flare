import type {
  AuditLogEntry,
  CostSummary,
  Site,
  SiteCreateRequest,
  User,
} from "./types";

const BASE = "";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
      window.location.href = "/login";
      return new Promise(() => {}) as T;
    }
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// Auth
export const auth = {
  login: (code: string) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  devLogin: () =>
    request<User>("/api/auth/dev-login", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
};

// Sites
export const sites = {
  list: () => request<Site[]>("/api/sites"),
  get: (id: string) => request<Site>(`/api/sites/${id}`),
  create: (data: SiteCreateRequest) =>
    request<Site>("/api/sites", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<SiteCreateRequest>) =>
    request<Site>(`/api/sites/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  redeploy: (id: string) =>
    request<Site>(`/api/sites/${id}/redeploy`, { method: "POST" }),
  stop: (id: string) =>
    request<Site>(`/api/sites/${id}/stop`, { method: "POST" }),
  start: (id: string) =>
    request<Site>(`/api/sites/${id}/start`, { method: "POST" }),
  destroy: (id: string) =>
    request<Site>(`/api/sites/${id}/destroy`, { method: "POST" }),
  unlock: (id: string, lockId: string) =>
    request(`/api/sites/${id}/unlock`, {
      method: "POST",
      body: JSON.stringify({ lock_id: lockId }),
    }),
};

// Deploy sources
export const deploySources = {
  validate: (type: string, ref: string) =>
    request<{ type: string; ref: string; resolved_sha: string; commit_message: string; valid: boolean }>(
      `/api/deploy-sources/validate?type=${encodeURIComponent(type)}&ref=${encodeURIComponent(ref)}`
    ),
};

// Audit logs
export const auditLogs = {
  list: (params?: { site_id?: string; action?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.site_id) query.set("site_id", params.site_id);
    if (params?.action) query.set("action", params.action);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));
    const qs = query.toString();
    return request<AuditLogEntry[]>(`/api/audit-logs${qs ? `?${qs}` : ""}`);
  },
};

// Costs
export const costs = {
  summary: (historyDays = 30, projectionDays = 14) =>
    request<CostSummary>(`/api/costs?history_days=${historyDays}&projection_days=${projectionDays}`),
};

// Health
export const health = {
  check: () => request<{ status: string; checks: Record<string, string> }>("/api/health"),
};

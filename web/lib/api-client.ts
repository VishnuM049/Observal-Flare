import type {
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
  logs: (id: string) =>
    request<{ logs: string }>(`/api/sites/${id}/logs`),
  unlock: (id: string, lockId: string) =>
    request(`/api/sites/${id}/unlock`, {
      method: "POST",
      body: JSON.stringify({ lock_id: lockId }),
    }),
};

// Deploy sources
export const deploySources = {
  validate: (type: string, ref: string) =>
    request<{ type: string; ref: string; resolved_sha: string; valid: boolean }>(
      `/api/deploy-sources/validate?type=${encodeURIComponent(type)}&ref=${encodeURIComponent(ref)}`
    ),
};

// Health
export const health = {
  check: () => request<{ status: string; checks: Record<string, string> }>("/api/health"),
};

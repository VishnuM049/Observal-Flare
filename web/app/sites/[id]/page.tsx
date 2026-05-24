"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Site, SiteStatus, SleepMode } from "@/lib/types";
import { sites as sitesApi, deploySources } from "@/lib/api-client";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EnvEditor } from "@/components/env-editor";
import { SelectField } from "@/components/select-field";
import { StatusBadge } from "@/components/status-badge";

function TtlCountdown({ createdAt, ttlDays, scheduledDestroyAt }: { createdAt: string; ttlDays: number; scheduledDestroyAt: string | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (scheduledDestroyAt) {
    const destroyTime = new Date(scheduledDestroyAt).getTime();
    const remaining = Math.max(0, destroyTime - now);
    if (remaining === 0) return <span style={{ color: "var(--color-danger)" }}>Destruction pending</span>;
    const d = Math.floor(remaining / 86400000);
    const h = Math.floor((remaining % 86400000) / 3600000);
    const m = Math.floor((remaining % 3600000) / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    return <span style={{ color: "var(--color-danger)" }}>Destroys in {d}d {h.toString().padStart(2, "0")}:{m.toString().padStart(2, "0")}:{s.toString().padStart(2, "0")}</span>;
  }

  const expiresAt = new Date(createdAt).getTime() + ttlDays * 86400000;
  const remaining = Math.max(0, expiresAt - now);
  if (remaining === 0) return <span style={{ color: "var(--color-warning)" }}>TTL expired — awaiting cleanup</span>;
  const d = Math.floor(remaining / 86400000);
  const h = Math.floor((remaining % 86400000) / 3600000);
  const m = Math.floor((remaining % 3600000) / 60000);
  const s = Math.floor((remaining % 60000) / 1000);
  return <span>{d}d {h.toString().padStart(2, "0")}:{m.toString().padStart(2, "0")}:{s.toString().padStart(2, "0")}</span>;
}

function LastActivityDisplay({ activityAt, idleTimeout }: { activityAt: string; idleTimeout: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const lastTime = new Date(activityAt).getTime();
  const elapsed = now - lastTime;
  const timeoutMs = idleTimeout * 60000;
  const remaining = Math.max(0, timeoutMs - elapsed);

  const ago = Math.floor(elapsed / 60000);
  const agoText = ago < 1 ? "just now" : ago < 60 ? `${ago}m ago` : `${Math.floor(ago / 60)}h ${ago % 60}m ago`;

  if (remaining === 0) {
    return <span style={{ color: "var(--color-warning)" }}>Idle — last visited {agoText}</span>;
  }

  const m = Math.floor(remaining / 60000);
  const s = Math.floor((remaining % 60000) / 1000);
  return <span>Last visited {agoText} — sleeps in {m}:{s.toString().padStart(2, "0")}</span>;
}

export default function SiteDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [site, setSite] = useState<Site | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editSleepMode, setEditSleepMode] = useState<SleepMode>("none");
  const [editAutoUpdate, setEditAutoUpdate] = useState(false);
  const [editAutoWipe, setEditAutoWipe] = useState(false);
  const [editIdleTimeout, setEditIdleTimeout] = useState(120);
  const [editSleepAtHour, setEditSleepAtHour] = useState(19);
  const [editWakeAtHour, setEditWakeAtHour] = useState(7);
  const [editTtlDays, setEditTtlDays] = useState<number | null>(null);
  const [editRequestorEmail, setEditRequestorEmail] = useState("");
  const [editDeployRef, setEditDeployRef] = useState("");
  const [refValidating, setRefValidating] = useState(false);
  const [refValidation, setRefValidation] = useState<{ sha: string; message: string } | null>(null);
  const [refError, setRefError] = useState<string | null>(null);
  const [editEnvOverrides, setEditEnvOverrides] = useState<Record<string, string>>({});
  const [showDestroyConfirm, setShowDestroyConfirm] = useState(false);

  const loadSite = useCallback(() => {
    sitesApi
      .get(id)
      .then(setSite)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    loadSite();
  }, [loadSite]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let delay = 1000;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let wsConnected = false;
    let stopped = false;

    function startPolling() {
      if (pollInterval) return;
      pollInterval = setInterval(loadSite, 15000);
    }

    function stopPolling() {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    }

    function connect() {
      if (stopped) return;
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsHost = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.host}`;
      ws = new WebSocket(`${wsHost}/api/sites/ws/${id}`);

      ws.onopen = () => {
        delay = 1000;
        wsConnected = true;
        stopPolling();
      };

      ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          const STABLE = ["running", "stopped", "sleeping", "destroyed", "failed"];
          if (event.type === "status_change") {
            setSite((prev: Site | null) => prev ? { ...prev, status: event.status } : prev);
            setStageMessage(event.message);
            if (STABLE.includes(event.status)) {
              setActionLoading(null);
              setTimeout(() => setStageMessage(null), 4000);
              loadSite();
            }
          } else if (event.type === "stage_progress") {
            setStageMessage(event.message);
          } else if (event.type === "error") {
            setSite((prev: Site | null) => prev ? { ...prev, status: "failed", error_message: event.message } : prev);
            setStageMessage(null);
            setActionLoading(null);
            loadSite();
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        wsConnected = false;
        if (stopped) return;
        startPolling();
        reconnectTimer = setTimeout(() => {
          delay = Math.min(delay * 2, 30000);
          connect();
        }, delay);
      };
    }

    connect();
    // Start polling as fallback until WS connects
    if (!wsConnected) startPolling();

    return () => {
      stopped = true;
      stopPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [id, loadSite]);

  const pendingStatuses: Record<string, SiteStatus> = {
    stop: "stopping",
    redeploy: "deploying",
    rebuild: "deploying",
    destroy: "destroying",
  };

  async function doAction(action: string) {
    if (!site) return;
    setActionLoading(action);
    setSite((prev: Site | null) => prev ? { ...prev, status: pendingStatuses[action] || prev.status } : prev);
    try {
      const fn =
        action === "redeploy"
          ? sitesApi.redeploy
          : action === "rebuild"
            ? sitesApi.rebuild
            : action === "stop"
              ? sitesApi.stop
              : action === "start"
                ? sitesApi.start
                : sitesApi.destroy;
      await fn(site.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
      loadSite();
      setActionLoading(null);
    }
  }

  function copyShareable() {
    if (!site) return;
    const sha = site.resolved_sha ? ` (${site.resolved_sha.slice(0, 8)})` : "";
    const text = [
      `${site.domain} — ${site.status}`,
      `Deploy: ${site.deploy_type}/${site.deploy_ref}${sha}`,
      `Instance: ${site.instance_size}`,
    ].join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  function startEditing() {
    if (!site) return;
    setEditSleepMode(site.sleep_mode);
    setEditIdleTimeout(site.idle_timeout_minutes);
    setEditSleepAtHour(site.sleep_at_hour);
    setEditWakeAtHour(site.wake_at_hour);
    setEditAutoUpdate(site.auto_update);
    setEditAutoWipe(site.auto_wipe_on_failure);
    setEditTtlDays(site.ttl_days);
    setEditRequestorEmail(site.requestor_email);
    setEditDeployRef(site.deploy_ref);
    setEditEnvOverrides({ ...site.env_overrides });
    setEditing(true);
  }

  async function saveSettings() {
    if (!site) return;
    setActionLoading("save");
    try {
      const updated = await sitesApi.update(site.id, {
        deploy_ref: editDeployRef,
        sleep_mode: editSleepMode,
        idle_timeout_minutes: editSleepMode === "idle" ? editIdleTimeout : undefined,
        sleep_at_hour: editSleepMode === "nightly" ? editSleepAtHour : undefined,
        wake_at_hour: editSleepMode === "nightly" ? editWakeAtHour : undefined,
        auto_update: editAutoUpdate,
        auto_wipe_on_failure: editAutoWipe,
        ttl_days: editTtlDays === null ? 0 : editTtlDays,
        requestor_email: editRequestorEmail,
        env_overrides: editEnvOverrides,
      });
      setSite(updated);
      setEditing(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-8 w-48" />
        <div className="card p-6">
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i}><div className="skeleton h-3 w-20 mb-2" /><div className="skeleton h-4 w-32" /></div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error && !site) {
    return (
      <div className="card px-4 py-3 flex items-center justify-between" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)" }}>
        <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
        <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
      </div>
    );
  }

  if (!site) return <p style={{ color: "var(--color-ink-muted)" }}>Site not found</p>;

  const isActive = ["running", "sleeping", "stopped", "failed"].includes(site.status);

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-2xl font-bold">{site.name}</h1>
            <StatusBadge status={site.status} />
          </div>
          <Link href="/sites" className="btn-primary">&larr; All Sites</Link>
        </div>
        {stageMessage && (
          <div className="mt-2 card px-3 py-2 flex items-center gap-2 text-sm" style={{ borderColor: "var(--color-accent-light)", backgroundColor: "var(--color-accent-light)" }}>
            {!["running", "stopped", "sleeping", "destroyed", "failed"].includes(site.status) && (
              <span className="inline-block w-3 h-3 border-2 rounded-full animate-spin" style={{ borderColor: "var(--color-border)", borderTopColor: "var(--color-accent)" }} />
            )}
            <span style={{ color: "var(--color-accent)" }}>{stageMessage}</span>
          </div>
        )}
      </div>

      <div className="card p-6">
        <dl className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
          <div>
            <dt className="section-label">Domain</dt>
            <dd className="flex items-center gap-2 mt-1">
              <a
                href={`https://${site.domain}`}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline"
                style={{ color: "var(--color-accent)" }}
              >
                {site.domain} <span className="text-xs">&#8599;</span>
              </a>
              <button
                onClick={copyShareable}
                className="btn-secondary text-xs px-1.5 py-0.5"
              >
                {copied ? "Copied!" : "Share"}
              </button>
            </dd>
          </div>
          <div>
            <dt className="section-label">Deploy</dt>
            <dd className="mt-1">
              {site.deploy_type}/{site.deploy_ref}
              {site.resolved_sha && (
                <span className="font-mono text-xs ml-1" style={{ color: "var(--color-ink-muted)" }}>
                  ({site.resolved_sha.slice(0, 8)})
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="section-label">Instance</dt>
            <dd className="mt-1 font-mono text-xs">
              <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase mr-2" style={{ backgroundColor: site.cloud_provider === "gcp" ? "#e8f0fe" : "#fef3e8", color: site.cloud_provider === "gcp" ? "#1a73e8" : "#c97a2e" }}>
                {site.cloud_provider}
              </span>
              {site.instance_size}
              {site.ip_address && <span className="ml-2" style={{ color: "var(--color-ink-muted)" }}>{site.ip_address}</span>}
            </dd>
          </div>
          <div>
            <dt className="section-label">Auto-update</dt>
            <dd className="mt-1">{site.auto_update ? "Enabled" : "Disabled"}</dd>
          </div>
          <div>
            <dt className="section-label">Sleep Mode</dt>
            <dd className="mt-1">
              {site.sleep_mode}
              {site.sleep_mode === "idle" && (
                <span className="ml-1" style={{ color: "var(--color-ink-muted)" }}>
                  ({site.idle_timeout_minutes}min timeout)
                </span>
              )}
              {site.sleep_mode === "nightly" && (
                <span className="ml-1" style={{ color: "var(--color-ink-muted)" }}>
                  (sleep {site.sleep_at_hour.toString().padStart(2, "0")}:00, wake {site.wake_at_hour.toString().padStart(2, "0")}:00 UTC)
                </span>
              )}
            </dd>
          </div>
          {site.sleep_mode === "idle" && (
            <div>
              <dt className="section-label">Last Activity</dt>
              <dd className="mt-1">
                {site.status !== "running"
                  ? <span style={{ color: "var(--color-ink-muted)" }}>Site is {site.status}</span>
                  : site.last_activity_at
                    ? <LastActivityDisplay activityAt={site.last_activity_at} idleTimeout={site.idle_timeout_minutes} />
                    : "Awaiting first check..."}
              </dd>
            </div>
          )}
          <div>
            <dt className="section-label">Time-to-Live</dt>
            <dd className="mt-1">
              {site.ttl_days ? <TtlCountdown createdAt={site.created_at} ttlDays={site.ttl_days} scheduledDestroyAt={site.scheduled_destroy_at} /> : "No limit"}
            </dd>
          </div>
          <div>
            <dt className="section-label">Est. Cost</dt>
            <dd className="mt-1 font-medium">{formatDailyCost(estimateDailyCost(site.instance_size, site.sleep_mode, site.sleep_at_hour, site.wake_at_hour, site.idle_timeout_minutes, site.cloud_provider))}</dd>
          </div>
          <div>
            <dt className="section-label">Created</dt>
            <dd className="mt-1">{new Date(site.created_at).toLocaleString()}</dd>
          </div>
          {site.last_deployed_at && (
            <div>
              <dt className="section-label">Last Deployed</dt>
              <dd className="mt-1">{new Date(site.last_deployed_at).toLocaleString()}</dd>
            </div>
          )}
          {site.error_message && (
            <div className="col-span-2">
              <dt className="section-label">Error</dt>
              <dd className="text-xs font-mono p-2 rounded mt-1" style={{ backgroundColor: "var(--color-danger-light)", color: "var(--color-danger)" }}>
                {site.error_message}
              </dd>
            </div>
          )}
        </dl>

        {isActive && !editing && (
          <button onClick={startEditing} className="btn-primary mt-4">
            Edit Settings
          </button>
        )}
      </div>

      {editing && (
        <div className="card p-6 space-y-4" style={{ borderColor: "var(--color-accent-light)" }}>
          <h2 className="section-label">Edit Settings</h2>

          <div className="grid grid-cols-2 gap-4 text-sm">
            {site.deploy_type === "commit" && (
              <div>
                <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Commit SHA <span className="text-xs">(next redeploy)</span></label>
                <div className="flex gap-2">
                  <input type="text" value={editDeployRef} onChange={(e) => { setEditDeployRef(e.target.value); setRefValidation(null); setRefError(null); }} className="input-field font-mono flex-1" placeholder="e.g. abc1234" />
                  <button type="button" disabled={refValidating || !editDeployRef.trim()} className="btn-primary" onClick={async () => {
                    setRefValidating(true);
                    setRefError(null);
                    setRefValidation(null);
                    try {
                      const result = await deploySources.validate(site.deploy_type, editDeployRef);
                      setRefValidation({ sha: result.resolved_sha, message: result.commit_message });
                    } catch (err: unknown) {
                      setRefError(err instanceof Error ? err.message : "Validation failed");
                    } finally {
                      setRefValidating(false);
                    }
                  }}>{refValidating ? "..." : "Validate"}</button>
                </div>
                {refValidation && (
                  <p className="text-xs mt-1" style={{ color: "var(--color-accent)" }}>
                    Resolved to <span className="font-mono">{refValidation.sha.slice(0, 8)}</span> — {refValidation.message}
                  </p>
                )}
                {refError && <p className="text-xs mt-1" style={{ color: "var(--color-danger)" }}>{refError}</p>}
              </div>
            )}
            <div>
              <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Sleep Mode <span className="text-xs">(next redeploy)</span></label>
              <SelectField
                value={editSleepMode}
                onChange={(v) => setEditSleepMode(v as SleepMode)}
                options={[
                  { value: "none", label: "None — always running" },
                  { value: "nightly", label: "Nightly — scheduled sleep/wake" },
                  { value: "idle", label: "Idle — stop after no traffic" },
                ]}
              />
            </div>
            {editSleepMode === "nightly" && (
              <>
                <div>
                  <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Sleep At (UTC)</label>
                  <SelectField
                    value={editSleepAtHour.toString()}
                    onChange={(v) => setEditSleepAtHour(Number(v))}
                    options={Array.from({ length: 24 }, (_, i) => ({
                      value: i.toString(),
                      label: `${i.toString().padStart(2, "0")}:00 UTC`,
                    }))}
                  />
                </div>
                <div>
                  <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Wake At (UTC)</label>
                  <SelectField
                    value={editWakeAtHour.toString()}
                    onChange={(v) => setEditWakeAtHour(Number(v))}
                    options={Array.from({ length: 24 }, (_, i) => ({
                      value: i.toString(),
                      label: `${i.toString().padStart(2, "0")}:00 UTC`,
                    }))}
                  />
                </div>
              </>
            )}
            {editSleepMode === "idle" && (
              <div>
                <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Idle Timeout <span className="text-xs">(next redeploy)</span></label>
                <SelectField
                  value={editIdleTimeout.toString()}
                  onChange={(v) => setEditIdleTimeout(Number(v))}
                  options={[
                    { value: "15", label: "15 minutes" },
                    { value: "30", label: "30 minutes" },
                    { value: "60", label: "1 hour" },
                    { value: "120", label: "2 hours (default)" },
                    { value: "240", label: "4 hours" },
                    { value: "480", label: "8 hours" },
                  ]}
                />
              </div>
            )}
            <div>
              <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Time-to-Live</label>
              <SelectField
                value={editTtlDays?.toString() ?? ""}
                onChange={(v) => setEditTtlDays(v ? Number(v) : null)}
                options={[
                  { value: "", label: "No limit" },
                  { value: "1", label: "1 day" },
                  { value: "3", label: "3 days" },
                  { value: "7", label: "7 days" },
                  { value: "14", label: "14 days" },
                  { value: "30", label: "30 days" },
                ]}
              />
            </div>
            <div>
              <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Requestor Email</label>
              <input type="email" value={editRequestorEmail} onChange={(e) => setEditRequestorEmail(e.target.value)} className="input-field" />
            </div>
            <div className="space-y-2 pt-5">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={editAutoUpdate} onChange={(e) => setEditAutoUpdate(e.target.checked)} className="accent-[var(--color-accent)]" />
                Auto-update on push
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={editAutoWipe} onChange={(e) => setEditAutoWipe(e.target.checked)} className="accent-[var(--color-accent)]" />
                Auto-wipe data on failed redeploy
              </label>
            </div>
          </div>

          <div>
            <label className="block mb-1 text-sm" style={{ color: "var(--color-ink-muted)" }}>
              Environment Variables
              <span className="text-xs ml-1">(next redeploy)</span>
            </label>
            <EnvEditor value={editEnvOverrides} onChange={setEditEnvOverrides} />
          </div>

          <div className="flex gap-3 pt-2">
            <button onClick={saveSettings} disabled={actionLoading === "save"} className="btn-primary">
              {actionLoading === "save" ? "Saving..." : "Save"}
            </button>
            <button onClick={() => setEditing(false)} className="btn-secondary">Cancel</button>
          </div>
        </div>
      )}

      {site.scheduled_destroy_at && isActive && (
        <div className="card px-4 py-3 text-sm flex items-center justify-between" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)", color: "var(--color-danger)" }}>
          <span>
            Scheduled for destruction on{" "}
            <strong>{new Date(site.scheduled_destroy_at).toLocaleString()}</strong>.
          </span>
          <select
            onChange={async (e) => {
              const val = Number(e.target.value);
              if (!val) return;
              setActionLoading("extend");
              try {
                const updated = await sitesApi.update(site.id, { ttl_days: val });
                setSite(updated);
              } catch (err: unknown) {
                setError(err instanceof Error ? err.message : "Failed to extend TTL");
              } finally {
                setActionLoading(null);
              }
            }}
            disabled={actionLoading !== null}
            className="input-field ml-4"
            style={{ width: "auto" }}
            defaultValue=""
          >
            <option value="" disabled>Extend TTL...</option>
            <option value="1">1 day</option>
            <option value="3">3 days</option>
            <option value="7">7 days</option>
            <option value="14">14 days</option>
            <option value="30">30 days</option>
          </select>
        </div>
      )}

      {isActive && (
        <div className="flex gap-3">
          {["running", "sleeping", "failed"].includes(site.status) && (
            <>
              <button onClick={() => doAction("redeploy")} disabled={actionLoading !== null} className="btn-primary">
                {actionLoading === "redeploy" ? "..." : "Redeploy"}
              </button>
              <button onClick={() => doAction("rebuild")} disabled={actionLoading !== null} className="btn-secondary">
                {actionLoading === "rebuild" ? "..." : "Rebuild"}
              </button>
            </>
          )}
          {site.status === "running" && (
            <button onClick={() => doAction("stop")} disabled={actionLoading !== null} className="btn-primary">
              {actionLoading === "stop" ? "..." : "Stop"}
            </button>
          )}
          {["stopped", "sleeping"].includes(site.status) && (
            <button onClick={() => doAction("start")} disabled={actionLoading !== null} className="btn-primary">
              {actionLoading === "start" ? "..." : "Start"}
            </button>
          )}
          <button
            onClick={() => setShowDestroyConfirm(true)}
            disabled={actionLoading !== null}
            className="btn-primary"
          >
            {actionLoading === "destroy" ? "..." : "Destroy"}
          </button>
        </div>
      )}

      <ConfirmDialog
        open={showDestroyConfirm}
        title={`Destroy ${site.name}?`}
        message="This will tear down all infrastructure including the EC2 instance, DNS record, and EIP. This action is irreversible."
        confirmLabel="Destroy"
        onConfirm={() => {
          setShowDestroyConfirm(false);
          doAction("destroy");
        }}
        onCancel={() => setShowDestroyConfirm(false)}
      />
    </div>
  );
}

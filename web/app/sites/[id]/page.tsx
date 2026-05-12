"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Site } from "@/lib/types";
import { sites as sitesApi } from "@/lib/api-client";
import type { SleepMode } from "@/lib/types";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EnvEditor } from "@/components/env-editor";
import { SelectField } from "@/components/select-field";
import { StatusBadge } from "@/components/status-badge";

export default function SiteDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [site, setSite] = useState<Site | null>(null);
  const [logs, setLogs] = useState<string | null>(null);
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
    const interval = setInterval(loadSite, 15000);
    return () => clearInterval(interval);
  }, [loadSite]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let delay = 1000;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    function connect() {
      if (stopped) return;
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsHost = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.host}`;
      ws = new WebSocket(`${wsHost}/api/sites/ws/${id}`);

      ws.onopen = () => {
        delay = 1000;
      };

      ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          const STABLE = ["running", "stopped", "sleeping", "destroyed", "failed"];
          if (event.type === "status_change") {
            setSite((prev) => prev ? { ...prev, status: event.status } : prev);
            setStageMessage(event.message);
            if (STABLE.includes(event.status)) {
              setTimeout(() => setStageMessage(null), 4000);
            }
          } else if (event.type === "stage_progress") {
            setStageMessage(event.message);
          } else if (event.type === "error") {
            setSite((prev) => prev ? { ...prev, status: "failed", error_message: event.message } : prev);
            setStageMessage(null);
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (stopped) return;
        reconnectTimer = setTimeout(() => {
          delay = Math.min(delay * 2, 30000);
          connect();
        }, delay);
      };
    }

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [id, loadSite]);

  async function doAction(action: string) {
    if (!site) return;
    setActionLoading(action);
    try {
      const fn =
        action === "redeploy"
          ? sitesApi.redeploy
          : action === "stop"
            ? sitesApi.stop
            : action === "start"
              ? sitesApi.start
              : sitesApi.destroy;
      const updated = await fn(site.id);
      setSite(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
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

  async function fetchLogs() {
    if (!site) return;
    try {
      const result = await sitesApi.logs(site.id);
      setLogs(result.logs);
    } catch (err: unknown) {
      setLogs(err instanceof Error ? err.message : "Failed to fetch logs");
    }
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
    setEditEnvOverrides({ ...site.env_overrides });
    setEditing(true);
  }

  async function saveSettings() {
    if (!site) return;
    setActionLoading("save");
    try {
      const updated = await sitesApi.update(site.id, {
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
          <div>
            <dt className="section-label">Time-to-Live</dt>
            <dd className="mt-1">{site.ttl_days ? `${site.ttl_days} day${site.ttl_days > 1 ? "s" : ""}` : "No limit"}</dd>
          </div>
          {site.scheduled_destroy_at && (
            <div>
              <dt className="section-label">Scheduled Destruction</dt>
              <dd className="mt-1" style={{ color: "var(--color-danger)" }}>{new Date(site.scheduled_destroy_at).toLocaleString()}</dd>
            </div>
          )}
          <div>
            <dt className="section-label">Est. Cost</dt>
            <dd className="mt-1 font-medium">{formatDailyCost(estimateDailyCost(site.instance_size, site.sleep_mode, site.sleep_at_hour, site.wake_at_hour, site.idle_timeout_minutes))}</dd>
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
            <div>
              <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Sleep Mode</label>
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
                <label className="block mb-1" style={{ color: "var(--color-ink-muted)" }}>Idle Timeout</label>
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
              <span className="ml-1">(applied on next redeploy)</span>
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
            <button onClick={() => doAction("redeploy")} disabled={actionLoading !== null} className="btn-primary">
              {actionLoading === "redeploy" ? "..." : "Redeploy"}
            </button>
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
          <button onClick={fetchLogs} className="btn-secondary">
            View Logs
          </button>
        </div>
      )}

      {logs !== null && (
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2" style={{ borderBottom: "1px solid var(--color-border)" }}>
            <span className="section-label">Container Logs</span>
            <button onClick={() => setLogs(null)} className="text-xs" style={{ color: "var(--color-ink-muted)" }}>Close</button>
          </div>
          <div className="p-4 font-mono text-xs overflow-x-auto max-h-96 overflow-y-auto whitespace-pre" style={{ backgroundColor: "var(--color-ink)", color: "var(--color-accent-light)" }}>
            {logs}
          </div>
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

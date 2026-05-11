"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Site } from "@/lib/types";
import { sites as sitesApi } from "@/lib/api-client";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
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

      ws.onmessage = (e) => {
        delay = 1000;
        try {
          const event = JSON.parse(e.data);
          if (event.type === "status_change") {
            setSite((prev) => prev ? { ...prev, status: event.status } : prev);
            setStageMessage(event.message);
            loadSite();
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

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md">{error}</div>;
  if (!site) return <p>Site not found</p>;

  const isActive = ["running", "sleeping", "stopped", "failed"].includes(site.status);

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">{site.name}</h1>
          <StatusBadge status={site.status} />
        </div>
        {stageMessage && (
          <p className="text-sm text-gray-500 mt-1 animate-pulse">{stageMessage}</p>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-6">
        <dl className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
          <div>
            <dt className="text-gray-500">Domain</dt>
            <dd className="flex items-center gap-2">
              <a
                href={`https://${site.domain}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                {site.domain}
              </a>
              <button
                onClick={copyShareable}
                className="text-xs text-gray-400 hover:text-gray-600 border border-gray-200 rounded px-1.5 py-0.5"
              >
                {copied ? "Copied!" : "Share"}
              </button>
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Deploy</dt>
            <dd>
              {site.deploy_type}/{site.deploy_ref}
              {site.resolved_sha && (
                <span className="font-mono text-xs text-gray-400 ml-1">
                  ({site.resolved_sha.slice(0, 8)})
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Instance</dt>
            <dd>
              {site.instance_size}
              {site.ip_address && <span className="text-gray-400 ml-2">{site.ip_address}</span>}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Auto-update</dt>
            <dd>{site.auto_update ? "Enabled" : "Disabled"}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Sleep Mode</dt>
            <dd>{site.sleep_mode}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Time-to-Live</dt>
            <dd>{site.ttl_days ? `${site.ttl_days} day${site.ttl_days > 1 ? "s" : ""}` : "No limit"}</dd>
          </div>
          {site.scheduled_destroy_at && (
            <div>
              <dt className="text-gray-500">Scheduled Destruction</dt>
              <dd className="text-red-600">{new Date(site.scheduled_destroy_at).toLocaleString()}</dd>
            </div>
          )}
          <div>
            <dt className="text-gray-500">Est. Cost</dt>
            <dd>{formatDailyCost(estimateDailyCost(site.instance_size, site.sleep_mode))}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Created</dt>
            <dd>{new Date(site.created_at).toLocaleString()}</dd>
          </div>
          {site.last_deployed_at && (
            <div>
              <dt className="text-gray-500">Last Deployed</dt>
              <dd>{new Date(site.last_deployed_at).toLocaleString()}</dd>
            </div>
          )}
          {site.error_message && (
            <div className="col-span-2">
              <dt className="text-gray-500">Error</dt>
              <dd className="text-red-600 text-xs font-mono bg-red-50 p-2 rounded mt-1">
                {site.error_message}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {site.scheduled_destroy_at && isActive && (
        <div className="bg-red-50 border border-red-200 rounded-md px-4 py-3 text-sm text-red-700 flex items-center justify-between">
          <span>
            This site is scheduled for automatic destruction on{" "}
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
            className="ml-4 border border-red-300 rounded px-2 py-1 text-sm bg-white text-red-700"
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
            <button
              onClick={() => doAction("redeploy")}
              disabled={actionLoading !== null}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {actionLoading === "redeploy" ? "..." : "Redeploy"}
            </button>
          )}
          {site.status === "running" && (
            <button
              onClick={() => doAction("stop")}
              disabled={actionLoading !== null}
              className="px-4 py-2 text-sm bg-yellow-500 text-white rounded-md hover:bg-yellow-600 disabled:opacity-50"
            >
              {actionLoading === "stop" ? "..." : "Stop"}
            </button>
          )}
          {["stopped", "sleeping"].includes(site.status) && (
            <button
              onClick={() => doAction("start")}
              disabled={actionLoading !== null}
              className="px-4 py-2 text-sm bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
            >
              {actionLoading === "start" ? "..." : "Start"}
            </button>
          )}
          <button
            onClick={() => doAction("destroy")}
            disabled={actionLoading !== null}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50"
          >
            {actionLoading === "destroy" ? "..." : "Destroy"}
          </button>
          <button
            onClick={fetchLogs}
            className="px-4 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
          >
            View Logs
          </button>
        </div>
      )}

      {logs !== null && (
        <div className="bg-gray-900 text-green-400 p-4 rounded-lg font-mono text-xs overflow-x-auto max-h-96 overflow-y-auto whitespace-pre">
          {logs}
        </div>
      )}
    </div>
  );
}

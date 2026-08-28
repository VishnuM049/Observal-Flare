"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ExperimentStatusBadge } from "@/components/experiment-status-badge";
import { experiments as experimentsApi } from "@/lib/api-client";
import type { Experiment, ExperimentEvent } from "@/lib/types";

const TERMINAL = new Set(["completed", "failed", "cleanup_failed", "cancelled"]);
const CANCELLABLE = new Set(["pending", "provisioning", "running"]);

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(2)} KB`;
  return `${bytes} B`;
}

function Value({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="section-label">{label}</dt>
      <dd className="mt-1 text-sm">{children}</dd>
    </div>
  );
}

export default function ExperimentDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [events, setEvents] = useState<ExperimentEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cancelLoading, setCancelLoading] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const isTerminal = experiment ? TERMINAL.has(experiment.status) : false;

  const reloadDetail = useCallback(() => {
    experimentsApi.get(id)
      .then((detail) => {
        setExperiment(detail);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [id]);

  const reloadEvents = useCallback(() => {
    experimentsApi.events(id)
      .then(setEvents)
      .catch((err) => setError(err.message));
  }, [id]);

  useEffect(() => {
    reloadDetail();
    reloadEvents();
  }, [reloadDetail, reloadEvents]);

  useEffect(() => {
    if (isTerminal) {
      reloadEvents();
      return;
    }
    const detailInterval = setInterval(reloadDetail, 5000);
    const eventInterval = setInterval(reloadEvents, 30000);
    return () => {
      clearInterval(detailInterval);
      clearInterval(eventInterval);
    };
  }, [isTerminal, reloadDetail, reloadEvents]);

  async function cancelRun() {
    setShowCancelConfirm(false);
    setCancelLoading(true);
    setError(null);
    try {
      setExperiment(await experimentsApi.cancel(id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Cancellation failed");
    } finally {
      setCancelLoading(false);
    }
  }

  async function retryCleanup() {
    setCleanupLoading(true);
    setError(null);
    try {
      setExperiment(await experimentsApi.retryCleanup(id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Cleanup request failed");
    } finally {
      setCleanupLoading(false);
    }
  }

  if (!experiment && !error) return <p style={{ color: "var(--color-ink-muted)" }}>Loading experiment…</p>;
  if (!experiment) return <div className="card px-4 py-3" style={{ color: "var(--color-danger)" }}>{error}</div>;

  const finalCount = experiment.delayed_count ?? experiment.immediate_count;
  const counterDelta = finalCount !== null && experiment.baseline_count !== null
    ? finalCount - experiment.baseline_count
    : null;
  const progressPercent = experiment.expected_pulls > 0
    ? Math.min(100, (experiment.launched_pulls / experiment.expected_pulls) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">GHCR Experiment</h1>
            <ExperimentStatusBadge status={experiment.status} />
          </div>
          <p className="font-mono text-xs mt-2" style={{ color: "var(--color-ink-muted)" }}>{experiment.id}</p>
        </div>
        <div className="flex gap-3">
          {CANCELLABLE.has(experiment.status) && (
            <button className="btn-primary" onClick={() => setShowCancelConfirm(true)} disabled={cancelLoading}>
              {cancelLoading ? "Sending cancellation…" : experiment.cancellation_requested ? "Retry Cancellation" : "Cancel Run"}
            </button>
          )}
          <Link href="/experiments" className="btn-primary">&larr; All Experiments</Link>
        </div>
      </div>

      {error && <div className="card px-4 py-3" style={{ color: "var(--color-danger)" }}>{error}</div>}

      <section className="card p-5">
        <div className="flex items-center justify-between text-sm mb-2">
          <strong>Run progress</strong>
          <span>{experiment.launched_pulls.toLocaleString()} / {experiment.expected_pulls.toLocaleString()} launched ({progressPercent.toFixed(1)}%)</span>
        </div>
        <div className="h-2 rounded-full overflow-hidden" style={{ backgroundColor: "var(--color-cream-dark)" }}>
          <div className="h-full transition-all" style={{ width: `${progressPercent}%`, backgroundColor: "var(--color-accent)" }} />
        </div>
      </section>

      <div className="grid grid-cols-4 gap-4">
        <div className="card px-4 py-3">
          <div className="section-label">Launched</div>
          <div className="text-2xl font-bold mt-1">{experiment.launched_pulls.toLocaleString()}</div>
          <div className="text-xs" style={{ color: "var(--color-ink-muted)" }}>{experiment.active_pulls} currently active</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Failed pulls</div>
          <div className="text-2xl font-bold mt-1">{experiment.failed_pulls.toLocaleString()}</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Last progress</div>
          <div className="text-sm font-medium mt-1">{experiment.last_progress_at ? new Date(experiment.last_progress_at).toLocaleTimeString() : "Waiting…"}</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Estimated peak concurrency</div>
          <div className="text-2xl font-bold mt-1">{experiment.max_concurrency ?? "—"}</div>
          <div className="text-xs" style={{ color: "var(--color-ink-muted)" }}>Latest per-instance samples</div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="card px-4 py-3">
          <div className="section-label">Successful pulls</div>
          <div className="text-2xl font-bold mt-1">{experiment.successful_pulls.toLocaleString()}</div>
          <div className="text-xs" style={{ color: "var(--color-ink-muted)" }}>of {experiment.expected_pulls.toLocaleString()} — authoritative result</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Counter before</div>
          <div className="text-2xl font-bold mt-1">{experiment.baseline_count ?? "—"}</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Counter after</div>
          <div className="text-2xl font-bold mt-1">{finalCount ?? "—"}</div>
        </div>
        <div className="card px-4 py-3">
          <div className="section-label">Observed delta</div>
          <div className="text-2xl font-bold mt-1">{counterDelta === null ? "—" : `+${counterDelta}`}</div>
        </div>
      </div>

      <section className="card p-6">
        <dl className="grid grid-cols-2 gap-x-8 gap-y-5">
          <Value label="Targets">{experiment.targets.length} image{experiment.targets.length === 1 ? "" : "s"}</Value>
          <Value label="Plan">{experiment.rate_per_minute}/minute for {experiment.duration_minutes} minutes on each instance</Value>
          <Value label="Fleet size">{experiment.instance_count} instance{experiment.instance_count === 1 ? "" : "s"}</Value>
          <Value label="Concurrency limit">{experiment.concurrency_limit} per instance</Value>
          <Value label="Images">{experiment.layer_count} total layers, {formatBytes(experiment.image_size_bytes)} combined</Value>
          <Value label="Estimated transfer">{formatBytes(experiment.estimated_transfer_bytes)}</Value>
          <Value label="Platform">{experiment.platform}</Value>
          <Value label="Instance type">{experiment.instance_type}</Value>
          <Value label="Estimated peak concurrency">{experiment.max_concurrency ?? "—"}</Value>
          <Value label="Live infrastructure">
            {experiment.instance_id ? `${experiment.instances.filter((item) => item.instance_id && item.cleanup_status !== "destroyed").length} instance(s)` : "Destroyed or not yet created"}
          </Value>
          <Value label="Terraform state"><span className="font-mono text-xs">{experiment.terraform_state_key}</span></Value>
          <Value label="Created">{new Date(experiment.created_at).toLocaleString()}</Value>
          <Value label="Completed">{experiment.completed_at ? new Date(experiment.completed_at).toLocaleString() : "—"}</Value>
        </dl>
      </section>

      <section className="card overflow-hidden">
        <div className="px-5 py-4 border-b" style={{ borderColor: "var(--color-border)" }}>
          <h2 className="section-label">Fleet members</h2>
          <p className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>
            Every member runs the same rate, duration, concurrency, and image set. EC2 IDs are retained after cleanup for auditability.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--color-border)" }}>
                <th className="px-4 py-3">Member</th>
                <th className="px-4 py-3">EC2 instance</th>
                <th className="px-4 py-3">Run status</th>
                <th className="px-4 py-3">Cleanup</th>
                <th className="px-4 py-3">Successful / expected</th>
                <th className="px-4 py-3">Failed</th>
                <th className="px-4 py-3">Active</th>
                <th className="px-4 py-3">Max concurrency</th>
                <th className="px-4 py-3">Last progress</th>
                <th className="px-4 py-3">Per-image results</th>
              </tr>
            </thead>
            <tbody>
              {experiment.instances.map((instance) => (
                <tr key={instance.index} className="border-b" style={{ borderColor: "var(--color-border)" }}>
                  <td className="px-4 py-3 font-medium">#{instance.index + 1}</td>
                  <td className="px-4 py-3 font-mono">{instance.instance_id ?? "Not allocated"}</td>
                  <td className="px-4 py-3">{instance.status.replaceAll("_", " ")}</td>
                  <td className="px-4 py-3">{instance.cleanup_status.replaceAll("_", " ")}</td>
                  <td className="px-4 py-3">{instance.successful_pulls.toLocaleString()} / {(experiment.rate_per_minute * experiment.duration_minutes).toLocaleString()}</td>
                  <td className="px-4 py-3">{instance.failed_pulls.toLocaleString()}</td>
                  <td className="px-4 py-3">{instance.active_pulls}</td>
                  <td className="px-4 py-3">{instance.max_concurrency}</td>
                  <td className="px-4 py-3">{instance.last_progress_at ? new Date(instance.last_progress_at).toLocaleTimeString() : "—"}</td>
                  <td className="px-4 py-3 space-y-1">
                    {instance.targets.map((target) => (
                      <div key={target.target_ref} title={target.target_ref}>
                        <span className="font-mono">{target.target_ref.split("@")[0]}</span>: {target.successful}/{target.launched}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {experiment.instances.some((instance) => instance.error_message) && (
          <div className="p-4 space-y-2" style={{ color: "var(--color-danger)" }}>
            {experiment.instances.filter((instance) => instance.error_message).map((instance) => (
              <div key={instance.index} className="text-xs"><strong>Member #{instance.index + 1}:</strong> {instance.error_message}</div>
            ))}
          </div>
        )}
        {experiment.instances.some((instance) => instance.run_log) && (
          <div className="p-4 space-y-2">
            {experiment.instances.filter((instance) => instance.run_log).map((instance) => (
              <details key={instance.index}>
                <summary className="text-xs cursor-pointer">Member #{instance.index + 1} final command output</summary>
                <pre className="text-xs whitespace-pre-wrap mt-2 overflow-auto">{instance.run_log}</pre>
              </details>
            ))}
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="px-5 py-4 border-b" style={{ borderColor: "var(--color-border)" }}>
          <h2 className="section-label">Per-image fleet progress and live counters</h2>
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left" style={{ borderColor: "var(--color-border)" }}>
              <th className="px-4 py-3">Image</th>
              <th className="px-4 py-3">Weight</th>
              <th className="px-4 py-3">Pulls</th>
              <th className="px-4 py-3">Failed</th>
              <th className="px-4 py-3">Baseline</th>
              <th className="px-4 py-3">Current</th>
              <th className="px-4 py-3">Delta</th>
            </tr>
          </thead>
          <tbody>
            {experiment.targets.map((target) => {
              const current = target.final_count ?? target.current_count;
              const delta = current !== null && target.baseline_count !== null
                ? current - target.baseline_count
                : null;
              return (
                <tr key={target.target_ref} className="border-b" style={{ borderColor: "var(--color-border)" }}>
                  <td className="px-4 py-3">
                    <a href={target.package_url} target="_blank" rel="noopener noreferrer" className="font-mono hover:underline" style={{ color: "var(--color-accent)" }}>
                      {target.requested_ref}
                      {target.requested_ref !== target.target_ref && (
                        <span className="block" style={{ color: "var(--color-ink-muted)" }}>resolved {target.target_ref.slice(-20)}</span>
                      )}
                    </a>
                  </td>
                  <td className="px-4 py-3">{target.weight}</td>
                  <td className="px-4 py-3">{target.successful_pulls}/{target.expected_pulls}</td>
                  <td className="px-4 py-3">{target.failed_pulls}</td>
                  <td className="px-4 py-3">{target.baseline_count ?? "—"}</td>
                  <td className="px-4 py-3">{current ?? "—"}</td>
                  <td className="px-4 py-3">{delta === null ? "—" : `+${delta}`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {(experiment.error_message || experiment.cleanup_error) && (
        <section className="card p-5 space-y-3" style={{ borderColor: "var(--color-danger)" }}>
          <h2 className="section-label">Errors</h2>
          {experiment.error_message && <pre className="text-xs whitespace-pre-wrap" style={{ color: "var(--color-danger)" }}>{experiment.error_message}</pre>}
          {experiment.cleanup_error && <pre className="text-xs whitespace-pre-wrap" style={{ color: "var(--color-danger)" }}>{experiment.cleanup_error}</pre>}
          {experiment.status === "cleanup_failed" && (
            <button className="btn-primary" onClick={retryCleanup} disabled={cleanupLoading}>
              {cleanupLoading ? "Requesting…" : "Retry Infrastructure Cleanup"}
            </button>
          )}
        </section>
      )}

      <section className="card p-5">
        <h2 className="section-label mb-4">Run timeline (latest 200 events)</h2>
        <div className="space-y-3">
          {events.map((event) => (
            <div key={event.id} className="grid grid-cols-[10rem_12rem_1fr] gap-3 text-xs border-b pb-3" style={{ borderColor: "var(--color-border)" }}>
              <span style={{ color: "var(--color-ink-muted)" }}>{new Date(event.created_at).toLocaleString()}</span>
              <strong>{event.event_type.replaceAll("_", " ")}</strong>
              <code className="break-all" style={{ color: "var(--color-ink-muted)" }}>{JSON.stringify(event.payload)}</code>
            </div>
          ))}
          {events.length === 0 && <p className="text-sm" style={{ color: "var(--color-ink-muted)" }}>No events recorded yet.</p>}
        </div>
      </section>

      <section className="card p-5">
        <h2 className="section-label mb-3">Isolation guarantees</h2>
        <ul className="text-sm space-y-1 list-disc pl-5" style={{ color: "var(--color-ink-muted)" }}>
          <li>Dedicated ARQ queue and worker, separate from site provisioning.</li>
          <li>Dedicated Terraform module and experiments/ state prefix.</li>
          <li>No DNS, Elastic IP, inbound ports, Docker, or Observal deployment.</li>
          <li>Unique cache/output directory for every pull with immediate cleanup.</li>
        </ul>
      </section>

      <ConfirmDialog
        open={showCancelConfirm}
        title="Cancel this experiment?"
        message={experiment.cancellation_requested
          ? "Flare will retry cancellation signals and force fleet cleanup if any instance cannot be reached."
          : "Flare will stop new pulls, terminate active pull processes, and destroy the disposable infrastructure."}
        confirmLabel="Cancel Run"
        confirmDisabled={cancelLoading}
        onConfirm={cancelRun}
        onCancel={() => setShowCancelConfirm(false)}
      />
    </div>
  );
}

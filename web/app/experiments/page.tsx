"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ExperimentStatusBadge } from "@/components/experiment-status-badge";
import { experiments as experimentsApi } from "@/lib/api-client";
import type { Experiment } from "@/lib/types";

const ACTIVE = new Set(["pending", "provisioning", "running", "destroying"]);

export default function ExperimentsPage() {
  const [items, setItems] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    experimentsApi
      .list()
      .then((data) => {
        setItems(data);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
    const interval = setInterval(reload, 5000);
    return () => clearInterval(interval);
  }, []);

  const active = items.filter((item) => ACTIVE.has(item.status)).length;
  const completed = items.filter((item) => item.status === "completed").length;
  const totalPulls = items.reduce((sum, item) => sum + item.successful_pulls, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-2xl font-bold">GHCR Experiments</h1>
          <p className="mt-1 text-sm" style={{ color: "var(--color-ink-muted)" }}>
            Isolated, disposable EC2 workloads. Experiments use a separate queue and never provision an Observal site.
          </p>
        </div>
        <Link href="/experiments/new" className="btn-primary">New Experiment &rarr;</Link>
      </div>

      {!loading && !error && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card px-4 py-3">
            <div className="section-label">Active</div>
            <div className="text-2xl font-bold mt-1">{active}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Completed</div>
            <div className="text-2xl font-bold mt-1">{completed}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Successful Test Pulls</div>
            <div className="text-2xl font-bold mt-1">{totalPulls.toLocaleString()}</div>
          </div>
        </div>
      )}

      {error && (
        <div className="card px-4 py-3" style={{ borderColor: "var(--color-danger)", color: "var(--color-danger)" }}>
          {error}
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left" style={{ borderColor: "var(--color-border)" }}>
              <th className="px-4 py-3 section-label">Created</th>
              <th className="px-4 py-3 section-label">Status</th>
              <th className="px-4 py-3 section-label">Target</th>
              <th className="px-4 py-3 section-label">Plan</th>
              <th className="px-4 py-3 section-label">Pulls</th>
              <th className="px-4 py-3 section-label">Counter</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-b" style={{ borderColor: "var(--color-border)" }}>
                <td className="px-4 py-3">
                  <Link href={`/experiments/${item.id}`} className="hover:underline">
                    {new Date(item.created_at).toLocaleString()}
                  </Link>
                </td>
                <td className="px-4 py-3"><ExperimentStatusBadge status={item.status} /></td>
                <td className="px-4 py-3 font-mono text-xs">
                  {item.target_ref.split("@")[0]}
                  {item.targets.length > 1 && <span className="ml-1">+{item.targets.length - 1}</span>}
                </td>
                <td className="px-4 py-3">{item.rate_per_minute}/min × {item.duration_minutes}m × {item.instance_count} EC2</td>
                <td className="px-4 py-3">{item.successful_pulls}/{item.expected_pulls}</td>
                <td className="px-4 py-3">
                  {item.baseline_count ?? "—"} → {item.delayed_count ?? item.immediate_count ?? "—"}
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10 text-center" style={{ color: "var(--color-ink-muted)" }}>No experiments yet.</td></tr>
            )}
            {loading && (
              <tr><td colSpan={6} className="px-4 py-10 text-center" style={{ color: "var(--color-ink-muted)" }}>Loading…</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

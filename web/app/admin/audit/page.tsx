"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuditLogEntry } from "@/lib/types";
import { auditLogs } from "@/lib/api-client";
import { SelectField } from "@/components/select-field";

const PAGE_SIZE = 50;

const ACTION_COLORS: Record<string, string> = {
  "site.created": "var(--color-accent)",
  "site.redeployed": "#1D4ED8",
  "site.redeploy_requested": "#1D4ED8",
  "site.stopped": "var(--color-warning)",
  "site.stop_requested": "var(--color-warning)",
  "site.started": "var(--color-accent)",
  "site.start_requested": "var(--color-accent)",
  "site.destroyed": "var(--color-danger)",
  "site.destroy_requested": "var(--color-danger)",
};

export default function AuditLogPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");
  const [offset, setOffset] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    auditLogs
      .list({
        action: actionFilter || undefined,
        limit: PAGE_SIZE,
        offset,
      })
      .then(setEntries)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [actionFilter, offset]);

  useEffect(() => {
    load();
  }, [load]);

  function handleFilterChange(value: string) {
    setActionFilter(value);
    setOffset(0);
  }

  const ACTION_OPTIONS = [
    { value: "", label: "All actions" },
    { value: "site.created", label: "site.created" },
    { value: "site.redeploy_requested", label: "site.redeploy_requested" },
    { value: "site.redeployed", label: "site.redeployed" },
    { value: "site.stop_requested", label: "site.stop_requested" },
    { value: "site.stopped", label: "site.stopped" },
    { value: "site.start_requested", label: "site.start_requested" },
    { value: "site.started", label: "site.started" },
    { value: "site.destroy_requested", label: "site.destroy_requested" },
    { value: "site.destroyed", label: "site.destroyed" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Audit Log</h1>

      <div className="card p-6">
        <div className="flex items-center gap-4 mb-4">
          <span className="section-label">Filter</span>
          <SelectField
            value={actionFilter}
            onChange={handleFilterChange}
            options={ACTION_OPTIONS}
            style={{ width: "16rem" }}
          />
        </div>

        {error && (
          <div className="rounded-lg px-4 py-3 flex items-center justify-between mb-4" style={{ backgroundColor: "var(--color-danger-light)" }}>
            <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
            <button onClick={load} className="btn-secondary">Retry</button>
          </div>
        )}

        <div className="overflow-hidden rounded-lg" style={{ border: "1px solid var(--color-border)" }}>
          <table className="w-full text-sm">
            <thead style={{ borderBottom: "1px solid var(--color-border)", backgroundColor: "var(--color-cream)" }}>
              <tr className="text-left" style={{ color: "var(--color-ink-muted)" }}>
                <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wider">Time</th>
                <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wider">User</th>
                <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wider">Action</th>
                <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wider">Details</th>
              </tr>
            </thead>
            <tbody>
              {loading &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--color-border)" }}>
                    <td className="px-4 py-4"><div className="skeleton h-3 w-28" /></td>
                    <td className="px-4 py-4"><div className="skeleton h-3 w-20" /></td>
                    <td className="px-4 py-4"><div className="skeleton h-3 w-24" /></td>
                    <td className="px-4 py-4"><div className="skeleton h-3 w-40" /></td>
                  </tr>
                ))}
              {!loading && entries.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-12 text-center">
                    <p style={{ color: "var(--color-ink-muted)" }}>No entries found.</p>
                  </td>
                </tr>
              )}
              {!loading &&
                entries.map((entry) => (
                  <tr
                    key={entry.id}
                    className="transition-colors hover:bg-[var(--color-cream)]"
                    style={{ borderBottom: "1px solid var(--color-border)" }}
                  >
                    <td className="px-4 py-3 whitespace-nowrap" style={{ color: "var(--color-ink-muted)" }}>
                      {new Date(entry.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <div style={{ color: "var(--color-ink)" }}>{entry.user_name || "Unknown"}</div>
                      {entry.user_email && (
                        <div className="text-xs" style={{ color: "var(--color-ink-muted)" }}>{entry.user_email}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="font-mono text-xs px-2 py-0.5 rounded-full"
                        style={{
                          backgroundColor: "var(--color-cream)",
                          color: ACTION_COLORS[entry.action] || "var(--color-ink-light)",
                          border: "1px solid var(--color-border)",
                        }}
                      >
                        {entry.action}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm max-w-md" style={{ color: "var(--color-ink-light)" }}>
                      {entry.details && Object.keys(entry.details).length > 0 ? (
                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                          {Object.entries(entry.details).map(([key, value]) => (
                            <span key={key}>
                              <span style={{ color: "var(--color-ink-muted)" }}>{key.replace(/_/g, " ")}:</span>{" "}
                              {String(value)}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span style={{ color: "var(--color-ink-muted)" }}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="btn-secondary"
          >
            &larr; Previous
          </button>
          <span className="text-sm" style={{ color: "var(--color-ink-muted)" }}>
            {offset + 1}–{offset + entries.length}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={entries.length < PAGE_SIZE}
            className="btn-secondary"
          >
            Next &rarr;
          </button>
        </div>
      </div>
    </div>
  );
}

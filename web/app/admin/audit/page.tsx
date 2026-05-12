"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuditLogEntry } from "@/lib/types";
import { auditLogs } from "@/lib/api-client";

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

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Audit Log</h1>

      <div className="flex items-center gap-4 mb-4">
        <select
          value={actionFilter}
          onChange={(e) => handleFilterChange(e.target.value)}
          className="input-field"
          style={{ width: "auto" }}
        >
          <option value="">All actions</option>
          <option value="site.created">site.created</option>
          <option value="site.redeploy_requested">site.redeploy_requested</option>
          <option value="site.redeployed">site.redeployed</option>
          <option value="site.stop_requested">site.stop_requested</option>
          <option value="site.stopped">site.stopped</option>
          <option value="site.start_requested">site.start_requested</option>
          <option value="site.started">site.started</option>
          <option value="site.destroy_requested">site.destroy_requested</option>
          <option value="site.destroyed">site.destroyed</option>
        </select>
      </div>

      {error && (
        <div className="card px-4 py-3 flex items-center justify-between mb-4" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)" }}>
          <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
          <button onClick={load} className="btn-secondary text-xs">Retry</button>
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead style={{ borderBottom: "1px solid var(--color-border)" }}>
            <tr className="text-left" style={{ color: "var(--color-ink-muted)" }}>
              <th className="px-4 py-3 font-medium">Time</th>
              <th className="px-4 py-3 font-medium">User</th>
              <th className="px-4 py-3 font-medium">Action</th>
              <th className="px-4 py-3 font-medium">Details</th>
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
                  <div className="text-2xl mb-2" style={{ color: "var(--color-ink-muted)" }}>~</div>
                  <p style={{ color: "var(--color-ink-muted)" }}>No audit log entries found.</p>
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
                      className="font-mono text-xs px-2 py-0.5 rounded"
                      style={{
                        backgroundColor: "var(--color-cream)",
                        color: ACTION_COLORS[entry.action] || "var(--color-ink-light)",
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
          className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Previous
        </button>
        <span className="text-sm" style={{ color: "var(--color-ink-muted)" }}>
          Showing {offset + 1}–{offset + entries.length}
        </span>
        <button
          onClick={() => setOffset(offset + PAGE_SIZE)}
          disabled={entries.length < PAGE_SIZE}
          className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    </div>
  );
}

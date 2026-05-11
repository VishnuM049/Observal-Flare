"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuditLogEntry } from "@/lib/types";
import { auditLogs } from "@/lib/api-client";

const PAGE_SIZE = 50;

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
          className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
        >
          <option value="">All actions</option>
          <option value="site.created">site.created</option>
          <option value="site.destroyed">site.destroyed</option>
          <option value="site.redeployed">site.redeployed</option>
          <option value="site.stopped">site.stopped</option>
          <option value="site.started">site.started</option>
        </select>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm mb-4">
          {error}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-500">
            <tr>
              <th className="px-4 py-3 font-medium">Time</th>
              <th className="px-4 py-3 font-medium">User</th>
              <th className="px-4 py-3 font-medium">Action</th>
              <th className="px-4 py-3 font-medium">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            )}
            {!loading && entries.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                  No audit log entries found.
                </td>
              </tr>
            )}
            {!loading &&
              entries.map((entry) => (
                <tr key={entry.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                    {new Date(entry.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <div>{entry.user_name || "Unknown"}</div>
                    {entry.user_email && (
                      <div className="text-xs text-gray-400">{entry.user_email}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">
                      {entry.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500 font-mono max-w-xs truncate">
                    {JSON.stringify(entry.details)}
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
          className="px-3 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Previous
        </button>
        <span className="text-sm text-gray-500">
          Showing {offset + 1}–{offset + entries.length}
        </span>
        <button
          onClick={() => setOffset(offset + PAGE_SIZE)}
          disabled={entries.length < PAGE_SIZE}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    </div>
  );
}

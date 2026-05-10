"use client";

import type { Site } from "@/lib/types";
import { StatusBadge } from "./status-badge";

export function SiteTable({ sites }: { sites: Site[] }) {
  if (sites.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        No sites yet.{" "}
        <a href="/sites/new" className="text-blue-600 hover:underline">
          Create one
        </a>
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="text-left px-4 py-3 font-medium">Name</th>
            <th className="text-left px-4 py-3 font-medium">Status</th>
            <th className="text-left px-4 py-3 font-medium">Deploy</th>
            <th className="text-left px-4 py-3 font-medium">Size</th>
            <th className="text-left px-4 py-3 font-medium">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sites.map((site) => (
            <tr key={site.id} className="hover:bg-gray-50">
              <td className="px-4 py-3">
                <a
                  href={`/sites/${site.id}`}
                  className="font-medium text-blue-600 hover:underline"
                >
                  {site.name}
                </a>
                <div className="text-xs text-gray-400">{site.domain}</div>
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={site.status} />
              </td>
              <td className="px-4 py-3 text-gray-600">
                {site.deploy_type}/{site.deploy_ref}
                {site.resolved_sha && (
                  <span className="text-xs text-gray-400 ml-1 font-mono">
                    ({site.resolved_sha.slice(0, 7)})
                  </span>
                )}
              </td>
              <td className="px-4 py-3 text-gray-600">{site.instance_size}</td>
              <td className="px-4 py-3 text-gray-500">
                {new Date(site.created_at).toLocaleDateString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

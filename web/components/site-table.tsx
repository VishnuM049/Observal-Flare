"use client";

import Link from "next/link";
import type { Site } from "@/lib/types";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
import { StatusBadge } from "./status-badge";

export function SiteTable({ sites }: { sites: Site[] }) {
  if (sites.length === 0) {
    return (
      <div className="card text-center py-16 px-6">
        <div className="text-4xl mb-3">~</div>
        <p className="text-lg font-medium mb-1" style={{ color: "var(--color-ink)" }}>No sites yet</p>
        <p className="text-sm mb-4" style={{ color: "var(--color-ink-muted)" }}>
          Create your first Observal preview environment.
        </p>
        <Link href="/sites/new" className="btn-primary inline-block">
          Create Site
        </Link>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead style={{ borderBottom: "1px solid var(--color-border)" }}>
          <tr className="text-left" style={{ color: "var(--color-ink-muted)" }}>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Deploy</th>
            <th className="px-4 py-3 font-medium">Size</th>
            <th className="px-4 py-3 font-medium">Cost</th>
            <th className="px-4 py-3 font-medium">Expires</th>
            <th className="px-4 py-3 font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => (
            <tr
              key={site.id}
              className="transition-colors hover:bg-[var(--color-cream)] cursor-pointer"
              style={{ borderBottom: "1px solid var(--color-border)" }}
              onClick={() => (window.location.href = `/sites/${site.id}`)}
            >
              <td className="px-4 py-3">
                <span className="font-medium" style={{ color: "var(--color-accent)" }}>
                  {site.name}
                </span>
                <div className="text-xs" style={{ color: "var(--color-ink-muted)" }}>{site.domain}</div>
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={site.status} />
              </td>
              <td className="px-4 py-3" style={{ color: "var(--color-ink-light)" }}>
                {site.deploy_type}/{site.deploy_ref}
                {site.resolved_sha && (
                  <span className="text-xs ml-1 font-mono" style={{ color: "var(--color-ink-muted)" }}>
                    ({site.resolved_sha.slice(0, 7)})
                  </span>
                )}
              </td>
              <td className="px-4 py-3 font-mono text-xs" style={{ color: "var(--color-ink-light)" }}>{site.instance_size}</td>
              <td className="px-4 py-3" style={{ color: "var(--color-ink-light)" }}>
                {formatDailyCost(estimateDailyCost(site.instance_size, site.sleep_mode, site.sleep_at_hour, site.wake_at_hour, site.idle_timeout_minutes))}
              </td>
              <td className="px-4 py-3">
                {site.scheduled_destroy_at ? (
                  <span className="text-xs" style={{ color: "var(--color-danger)" }}>
                    {new Date(site.scheduled_destroy_at).toLocaleDateString()}
                  </span>
                ) : site.ttl_days ? (
                  <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>{site.ttl_days}d</span>
                ) : (
                  <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>--</span>
                )}
              </td>
              <td className="px-4 py-3 text-xs" style={{ color: "var(--color-ink-muted)" }}>
                {new Date(site.created_at).toLocaleDateString()}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot style={{ borderTop: "1px solid var(--color-border)" }}>
          <tr style={{ backgroundColor: "var(--color-cream)" }}>
            <td className="px-4 py-3 font-medium" style={{ color: "var(--color-ink-light)" }} colSpan={4}>
              Total ({sites.length} site{sites.length !== 1 ? "s" : ""})
            </td>
            <td className="px-4 py-3 font-medium" style={{ color: "var(--color-ink-light)" }}>
              {formatDailyCost(sites.reduce((sum, s) => sum + estimateDailyCost(s.instance_size, s.sleep_mode, s.sleep_at_hour, s.wake_at_hour, s.idle_timeout_minutes), 0))}
            </td>
            <td colSpan={2}></td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { Site } from "@/lib/types";
import { sites as sitesApi } from "@/lib/api-client";
import { estimateDailyCost } from "@/lib/cost-estimate";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { SiteTable } from "@/components/site-table";

export default function SitesPage() {
  const [siteList, setSiteList] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [bulkAction, setBulkAction] = useState<"stop" | "start" | "destroy" | null>(null);
  const [bulkLoading, setBulkLoading] = useState(false);

  function reload() {
    sitesApi
      .list()
      .then(setSiteList)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
    const interval = setInterval(reload, 10000);
    return () => clearInterval(interval);
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return siteList;
    const q = search.toLowerCase();
    return siteList.filter(
      (s) => s.name.toLowerCase().includes(q) || s.domain.toLowerCase().includes(q)
    );
  }, [siteList, search]);

  const runningCount = siteList.filter((s) => s.status === "running").length;
  const stoppedCount = siteList.filter((s) => s.status === "stopped" || s.status === "sleeping").length;
  const destroyableCount = siteList.filter((s) => !["destroying", "destroyed"].includes(s.status)).length;
  const totalDaily = siteList.reduce((sum, s) => sum + estimateDailyCost(s.instance_size, s.sleep_mode, s.sleep_at_hour, s.wake_at_hour, s.idle_timeout_minutes), 0);

  const bulkConfig = {
    stop: {
      title: `Stop ${runningCount} running site${runningCount !== 1 ? "s" : ""}?`,
      message: "All running sites will be stopped. Containers will be shut down but instances will remain. You can start them again later.",
      count: runningCount,
      targets: () => siteList.filter((s) => s.status === "running"),
    },
    start: {
      title: `Start ${stoppedCount} stopped site${stoppedCount !== 1 ? "s" : ""}?`,
      message: "All stopped and sleeping sites will be started.",
      count: stoppedCount,
      targets: () => siteList.filter((s) => s.status === "stopped" || s.status === "sleeping"),
    },
    destroy: {
      title: `Destroy ${destroyableCount} site${destroyableCount !== 1 ? "s" : ""}?`,
      message: "All sites will be permanently destroyed. This tears down all infrastructure including EC2 instances, DNS records, and EIPs. This action is irreversible.",
      count: destroyableCount,
      targets: () => siteList.filter((s) => !["destroying", "destroyed"].includes(s.status)),
    },
  };

  async function executeBulk() {
    if (!bulkAction) return;
    setBulkLoading(true);
    const failures: string[] = [];
    const targets = bulkConfig[bulkAction].targets();
    const fn = bulkAction === "stop" ? sitesApi.stop : bulkAction === "start" ? sitesApi.start : sitesApi.destroy;
    for (const site of targets) {
      try {
        await fn(site.id);
      } catch {
        failures.push(site.name);
      }
    }
    setBulkAction(null);
    setBulkLoading(false);
    reload();
    if (failures.length > 0) {
      setError(`Failed for: ${failures.join(", ")}`);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Sites</h1>
        <Link href="/sites/new" className="btn-primary">
          New Site &rarr;
        </Link>
      </div>

      {!loading && !error && siteList.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="card px-4 py-3">
            <div className="section-label">Total Sites</div>
            <div className="text-2xl font-bold mt-1">{siteList.length}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Running</div>
            <div className="text-2xl font-bold mt-1" style={{ color: "var(--color-accent)" }}>{runningCount}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Daily Cost</div>
            <div className="text-2xl font-bold mt-1">${totalDaily.toFixed(2)}</div>
          </div>
        </div>
      )}

      {!loading && !error && siteList.length > 0 && (
        <div className="flex items-center gap-3 mb-4">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or domain..."
            className="input-field"
            style={{ maxWidth: "24rem" }}
          />
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={() => setBulkAction("stop")}
              disabled={runningCount === 0 || bulkLoading}
              className="btn-primary text-xs"
            >
              Stop All ({runningCount})
            </button>
            <button
              onClick={() => setBulkAction("start")}
              disabled={stoppedCount === 0 || bulkLoading}
              className="btn-primary text-xs"
            >
              Start All ({stoppedCount})
            </button>
            <button
              onClick={() => setBulkAction("destroy")}
              disabled={destroyableCount === 0 || bulkLoading}
              className="btn-primary text-xs"
            >
              Destroy All ({destroyableCount})
            </button>
          </div>
        </div>
      )}

      {loading && (
        <div className="card overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-4 border-b" style={{ borderColor: "var(--color-border)" }}>
              <div className="skeleton h-4 w-32" />
              <div className="skeleton h-4 w-16" />
              <div className="skeleton h-4 w-24" />
              <div className="skeleton h-4 w-16" />
              <div className="skeleton h-4 w-12" />
              <div className="skeleton h-4 w-20 ml-auto" />
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="card px-4 py-3 flex items-center justify-between" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)" }}>
          <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
          <button onClick={() => { setError(null); reload(); }} className="btn-secondary">
            Retry
          </button>
        </div>
      )}

      {!loading && !error && <SiteTable sites={filtered} />}

      {bulkAction && (
        <ConfirmDialog
          open={true}
          title={bulkConfig[bulkAction].title}
          message={bulkConfig[bulkAction].message}
          confirmLabel={bulkLoading ? "Processing..." : bulkAction === "destroy" ? "Destroy All" : bulkAction === "stop" ? "Stop All" : "Start All"}
          onConfirm={executeBulk}
          onCancel={() => setBulkAction(null)}
        />
      )}
    </div>
  );
}
